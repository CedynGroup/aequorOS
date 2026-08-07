"""W6 canonical-position return generators (docs/submission_pipeline_plan.md §W6.2–3).

Two generators that derive directly from the Data Engine's canonical position
book (the same current-generation, accepted/warning snapshot slice
``fact_derivation`` reads) instead of from module run metrics alone:

- ``large_exposures`` — the monthly Large Exposures return (LE-MONTHLY,
  Templates 1/1a/2/3/4 per the Large Exposures Directive appendix). Requires a
  succeeded baseline CAPITAL run (Tier 1 = the Net-Own-Funds proxy) AND
  canonical LOAN / INTERBANK_PLACEMENT / SECURITY_HOLDING positions at the
  period end.
- ``lmt`` — the Liquidity Monitoring Tools return: the LCR-by-significant-
  currency subset from the liquidity run (via the ``generation.py`` seams)
  plus three canonical-data monitoring tools (contractual maturity-mismatch
  ladder, top-10 depositor funding concentration, available HQLA-classified
  assets). Retires TODO(RR-6).

Documented derivation decisions (kept honest — nothing absent is fabricated):

- **NOF proxy**: Net Own Funds = Tier 1 capital (CET1 + AT1) from the
  succeeded baseline capital run. The CRD "Net Own Funds" definition is not
  separately computed; every template notes the proxy.
- **Connected-counterparty grouping**: counterparties sharing a non-empty
  ``group_reference`` (column first, then the ``group_reference``/``group``/
  ``parent`` attribute keys) form one group of connected counterparties;
  every other counterparty stands alone. Control/economic-interdependence
  analysis beyond the ingested group reference is not performed.
- **Exemption classification** (Template 3): a counterparty is classified
  exempt when its canonical ``counterparty_type`` is SOVEREIGN, CENTRAL_BANK
  or GOVERNMENT_ENTITY; a security held without a counterparty link is
  exempt when its product ``regulatory_category`` starts with ``SOVEREIGN``.
  Groups are exempt only when every member is. This is AequorOS's mapping of
  the directive's sovereign/BoG/GoG exemptions onto canonical categories.
- **Exposure value**: on-balance drawn exposure is ``balance_ghs`` (attribute,
  falling back to ``balance`` for base-currency books exactly like
  ``fact_derivation``); off-balance exposure is ``notional_ghs × ccf`` ONLY
  where both are present (Template 1 footnote: undrawn/contingent after CCF).
  No CRM/collateral data exists in the canonical model, so pre-CRM and
  post-CRM (net) exposures are equal and Template 4 is empty by construction.
- **Securities without a counterparty**: the ``issuer`` snapshot attribute is
  the counterparty identity; positions with neither counterparty nor issuer
  are excluded from the counterparty tables and surfaced as an INFO finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    ParamLiquidityThreshold,
)
from app.services import regulatory_capital, regulatory_liquidity
from app.services.liquidity_thresholds import BANK_MINIMUM_PCT as _TABLE1_BANK_MINIMUM_PCT
from app.services.params import get_active_params
from app.services.regulatory_reporting.generation import (
    MODULE_CAPITAL,
    MODULE_LIQUIDITY,
    GeneratedReturn,
    baseline_run_or_409,
    build_envelope,
    latest_succeeded_runs_by_scenario,
    lcr_snapshot_sections,
    lcr_snapshot_totals,
    liquidity_snapshot_metadata,
    snapshot_row,
    snapshot_section,
    snapshot_total,
    source_run_entry,
)
from app.services.regulatory_reporting.registry import ReturnDefinition

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_PCT = Decimal("0.0001")

# Mirrors fact_derivation._INCLUDED_VALIDATION_STATUSES: the derivation slice
# is the current (non-superseded) generation with accepted/warning status.
_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")

_LE_POSITION_TYPES = ("LOAN", "INTERBANK_PLACEMENT", "SECURITY_HOLDING")
_EXEMPT_COUNTERPARTY_TYPES = ("SOVEREIGN", "CENTRAL_BANK", "GOVERNMENT_ENTITY")
_SOVEREIGN_CATEGORY_PREFIX = "SOVEREIGN"
_LE_THRESHOLD_FRACTION = Decimal("0.10")  # large exposure = ≥10% of NOF (¶11)
_TOP_EXPOSURES_CAP = 100  # Template 2: top-100 exposures

# LMTD Table 2 (Contractual Cash Flow Mismatch) — the FULL published bucket
# set: 13 dated columns + non-contractual (built 2026-08-07, replacing the
# condensed 7-bucket ladder whose aggregation was lossy). Upper bounds are
# calendar-approximate day counts (months ≈ 30/31-day steps, years ≈ 365);
# undated positions land in the separate non-contractual column, never in
# Next Day. Keys double as snapshot-row extras and template column ids.
_TABLE2_BUCKETS: tuple[tuple[str, str, int | None], ...] = (
    ("next_day_ghs", "Next Day", 1),
    ("d2_7_ghs", "2 - 7 days", 7),
    ("d8_14_ghs", "8 - 14 days", 14),
    ("d15_1m_ghs", "15 days to 1 mth", 30),
    ("m1_2_ghs", "1 - 2 mths", 61),
    ("m2_3_ghs", "2 - 3 mths", 91),
    ("m3_6_ghs", "3 - 6 mths", 182),
    ("m6_9_ghs", "6 - 9 mths", 273),
    ("m9_12_ghs", "9 mths - 1 yr", 365),
    ("y1_2_ghs", "1 - 2 yrs", 730),
    ("y2_3_ghs", "2 - 3 yrs", 1095),
    ("y3_5_ghs", "3 - 5 yrs", 1825),
    ("y5_plus_ghs", "> 5 yrs", None),
)
_NON_CONTRACTUAL_BUCKET = ("non_contractual_ghs", "Non-contractual")

# Table 2 row taxonomy: canonical position types → published row categories.
# Derivative-family instruments split by carrying-value sign (MTM asset →
# row 3, MTM liability → row 8) because the canonical model stores one
# signed balance per instrument, not separate legs.
_T2_ADVANCES = ("LOAN",)
_T2_DERIVATIVE_FAMILY = ("DERIVATIVE", "FX_HEDGE", "INTEREST_RATE_SWAP")
_T2_OTHER_ASSETS = ("CASH", "INTERBANK_PLACEMENT", "OTHER_ASSET")
_T2_OTHER_LIABILITIES = ("INTERBANK_BORROWING", "OTHER_LIABILITY")
_T2_OFF_BALANCE = ("LC_GUARANTEE", "COMMITMENT_UNDRAWN")
_TABLE2_POSITION_TYPES = (
    *_T2_ADVANCES,
    "SECURITY_HOLDING",
    *_T2_DERIVATIVE_FAMILY,
    *_T2_OTHER_ASSETS,
    "DEPOSIT",
    *_T2_OTHER_LIABILITIES,
    *_T2_OFF_BALANCE,
)
# LMTD ¶5: Volatile Liabilities = all demand deposits (current and call).
# Stable = the complement; an unclassified deposit reports as stable/OTHER
# and is flagged at the ingestion gate (lmtd_classification_coverage).
_VOLATILE_DEPOSIT_TYPES = ("CURRENT", "CALL")
# Off-balance-sheet sub-rows (Table 2 rows 13/15/16/17). Sources that
# distinguish OBS instruments set attributes["obs_category"]; without it,
# undrawn commitments default to row 15 (irrevocable lending facilities)
# and LC/guarantee positions to row 17 (indemnities and guarantees) — the
# dominant category in Ghanaian OBS books. Row 16 fills only when the
# source names its letters of credit.
_OBS_CATEGORY_ROWS = {
    "obs_vehicle_facility": "13",
    "lending_facility": "15",
    "letter_of_credit": "16",
    "guarantee": "17",
}
_TOP_DEPOSITORS = 10

# --- LMTD Table 1: BOG Prudential Ratios (tool (a); built 2026-08-07) ------
#
# Six inputs (LMTD ¶5 definitions) for the reporting and previous month, and
# eight ratios compared against the Board threshold register
# (ParamLiquidityThreshold) with the directive's published bank minimums as
# the regulatory floor when no Board row exists. ¶9 makes these BINDING
# compliance ratios for SDIs; for banks they are monitoring tools.
_TABLE1_RATIOS: tuple[tuple[str, str, str, str], ...] = (
    ("narrow_to_volatile", "Narrow Liquid Assets to Volatile Liabilities", "narrow", "volatile"),
    ("broad_to_volatile", "Broad Liquid Assets to Volatile Liabilities", "broad", "volatile"),
    (
        "narrow_to_short_term",
        "Narrow Liquid Assets to Short Term Liabilities",
        "narrow",
        "short_term",
    ),
    (
        "broad_to_short_term",
        "Broad Liquid Assets to Short Term Liabilities",
        "broad",
        "short_term",
    ),
    (
        "narrow_to_total_deposits",
        "Narrow Liquid Assets to Total Deposits",
        "narrow",
        "total_deposits",
    ),
    (
        "broad_to_total_deposits",
        "Broad Liquid Assets to Total Deposits",
        "broad",
        "total_deposits",
    ),
    ("narrow_to_total_assets", "Narrow Liquid Assets to Total Assets", "narrow", "total_assets"),
    ("broad_to_total_assets", "Broad Liquid Assets to Total Assets", "broad", "total_assets"),
)
_TABLE1_INPUT_LABELS: tuple[tuple[str, str], ...] = (
    ("narrow", "Narrow Liquid Assets"),
    ("broad", "Broad Liquid Assets"),
    ("volatile", "Volatile liabilities"),
    ("total_deposits", "Total Deposits"),
    ("short_term", "Short-term liabilities"),
    ("total_assets", "Total Assets"),
)
_ONE_YEAR_DAYS = 365
_BANK_COUNTERPARTY_TYPES = ("BANK_OECD", "BANK_NON_OECD")
# LMTD ¶5 short-term liabilities (a): current, call and savings accounts are
# by their nature assessed to mature within one year, regardless of any
# stated maturity — behavioural/NMD lives must never leak into this rule.
_SHORT_TERM_BY_NATURE = ("CURRENT", "CALL", "SAVINGS")
_EQUITY_CATEGORY_PREFIX = "EQUITY"
_TOTAL_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "CASH", "INTERBANK_PLACEMENT", "OTHER_ASSET")


def _conflict_409(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": error_code, "message": message},
    )


def _finding(rule: str, severity: str, detail: str) -> dict[str, str]:
    """One generation finding; folded into the validation report as data."""
    return {"rule": rule, "severity": severity, "detail": detail}


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _pct_of(value: Decimal, denominator: Decimal) -> Decimal:
    return (value / denominator * _HUNDRED).quantize(_PCT)


@dataclass(frozen=True)
class _CanonicalRow:
    """One current-generation position snapshot flattened for W6 derivations."""

    position_type: str
    currency: str
    balance_ghs: Decimal
    has_ghs_value: bool
    contractual_maturity: date | None
    ifrs9_stage: int | None
    undrawn_ccf_ghs: Decimal
    ecl_ghs: Decimal
    counterparty_id: UUID | None
    counterparty_name: str | None
    counterparty_type: str | None
    counterparty_group: str | None
    counterparty_tin: str
    issuer: str | None
    regulatory_category: str | None
    # LMTD/LRMD 2026 classification fields (canonical epoch 2026-08-07).
    notional_ghs: Decimal | None
    deposit_account_type: str | None
    obs_category: str | None
    encumbered: bool | None
    encumbrance_reason: str | None
    owning_entity: str | None
    asset_location: str | None
    operational_purpose: bool | None
    redeemable_within_two_days: bool | None
    pledged_as_collateral: bool | None
    lien_reference: str | None
    counterparty_resident: bool | None
    counterparty_rating: str | None


def _group_reference(counterparty: CanonicalCounterparty) -> str | None:
    """Connected-counterparty key: column first, then attribute fallbacks."""
    if counterparty.group_reference:
        return counterparty.group_reference
    attributes = counterparty.attributes or {}
    for key in ("group_reference", "group", "parent"):
        value = attributes.get(key)
        if value:
            return str(value)
    return None


def _counterparty_tin(counterparty: CanonicalCounterparty) -> str:
    identifiers = counterparty.external_identifiers or {}
    for key in ("tin", "TIN", "ghana_card", "ghana_card_no"):
        value = identifiers.get(key)
        if value:
            return str(value)
    return ""


def _load_canonical_rows(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date, position_types: tuple[str, ...]
) -> list[_CanonicalRow]:
    records = db.execute(
        select(
            CanonicalPositionSnapshot, CanonicalPosition, CanonicalCounterparty, CanonicalProduct
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type.in_(position_types),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()

    base_currency = (bank.currency or "GHS").strip().upper()
    rows: list[_CanonicalRow] = []
    for snapshot, position, counterparty, product in records:
        attributes = snapshot.attributes or {}
        balance_ghs = _dec_or_none(attributes.get("balance_ghs"))
        has_ghs_value = True
        if balance_ghs is None:
            if position.currency == base_currency:
                balance_ghs = Decimal(str(snapshot.balance or _ZERO))
            else:
                # Mirrors fact_derivation: a foreign-currency book without an
                # ingested GHS conversion contributes zero, never a made-up
                # converted amount (surfaced as an INFO finding).
                balance_ghs = _ZERO
                has_ghs_value = False
        notional_ghs = _dec_or_none(attributes.get("notional_ghs"))
        if notional_ghs is None and position.currency == base_currency:
            notional_ghs = _dec_or_none(snapshot.notional)
        ccf = _dec_or_none(attributes.get("credit_conversion_factor"))
        undrawn = notional_ghs * ccf if notional_ghs is not None and ccf is not None else _ZERO
        issuer = attributes.get("issuer")
        obs_category = attributes.get("obs_category")
        rows.append(
            _CanonicalRow(
                position_type=position.position_type,
                currency=position.currency,
                balance_ghs=balance_ghs,
                has_ghs_value=has_ghs_value,
                contractual_maturity=snapshot.contractual_maturity,
                ifrs9_stage=snapshot.ifrs9_stage,
                undrawn_ccf_ghs=undrawn,
                ecl_ghs=_dec_or_none(attributes.get("ecl_provision_ghs")) or _ZERO,
                counterparty_id=counterparty.id if counterparty is not None else None,
                counterparty_name=counterparty.name if counterparty is not None else None,
                counterparty_type=(
                    counterparty.counterparty_type if counterparty is not None else None
                ),
                counterparty_group=(
                    _group_reference(counterparty) if counterparty is not None else None
                ),
                counterparty_tin=_counterparty_tin(counterparty) if counterparty else "",
                issuer=str(issuer) if issuer else None,
                regulatory_category=(product.regulatory_category if product is not None else None),
                notional_ghs=notional_ghs,
                deposit_account_type=snapshot.deposit_account_type,
                obs_category=str(obs_category) if obs_category else None,
                encumbered=snapshot.encumbered,
                encumbrance_reason=snapshot.encumbrance_reason,
                owning_entity=snapshot.owning_entity,
                asset_location=snapshot.asset_location,
                operational_purpose=snapshot.operational_purpose,
                redeemable_within_two_days=snapshot.redeemable_within_two_days,
                pledged_as_collateral=snapshot.pledged_as_collateral,
                lien_reference=snapshot.lien_reference,
                counterparty_resident=(
                    counterparty.resident if counterparty is not None else None
                ),
                counterparty_rating=counterparty.rating if counterparty is not None else None,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# LE-MONTHLY — Large Exposures return (Templates 1/1a/2/3/4)
# ---------------------------------------------------------------------------


@dataclass
class _Member:
    name: str
    tin: str
    exposure: Decimal = _ZERO


@dataclass
class _Entity:
    """One reporting unit: a single counterparty or a connected group."""

    key: str
    name: str
    connection: str  # "single" | "group"
    tin: str
    exempt: bool
    drawn: Decimal = _ZERO
    undrawn: Decimal = _ZERO
    provisions: Decimal = _ZERO
    worst_stage: int | None = None
    members: dict[str, _Member] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return self.drawn + self.undrawn


def _row_is_exempt(row: _CanonicalRow) -> bool:
    if row.counterparty_type is not None:
        return row.counterparty_type in _EXEMPT_COUNTERPARTY_TYPES
    category = row.regulatory_category or ""
    return category.startswith(_SOVEREIGN_CATEGORY_PREFIX)


def _entity_identity(row: _CanonicalRow) -> tuple[str, str, str, str] | None:
    """(key, name, connection, tin) for the row, or None when unattributable."""
    if row.counterparty_id is not None:
        if row.counterparty_group:
            return (f"group:{row.counterparty_group}", row.counterparty_group, "group", "")
        return (
            f"single:{row.counterparty_id}",
            row.counterparty_name or str(row.counterparty_id),
            "single",
            row.counterparty_tin,
        )
    if row.issuer:
        return (f"issuer:{row.issuer}", row.issuer, "single", "")
    return None


def _aggregate_entities(
    rows: list[_CanonicalRow],
) -> tuple[list[_Entity], Decimal, int]:
    """Group rows into reporting entities; returns (entities, unattributed_ghs, n)."""
    entities: dict[str, _Entity] = {}
    unattributed = _ZERO
    unattributed_count = 0
    for row in rows:
        identity = _entity_identity(row)
        exposure = row.balance_ghs + row.undrawn_ccf_ghs
        if identity is None:
            unattributed += exposure
            unattributed_count += 1
            continue
        key, name, connection, tin = identity
        entity = entities.get(key)
        if entity is None:
            entity = _Entity(
                key=key,
                name=name,
                connection=connection,
                tin=tin,
                exempt=_row_is_exempt(row),
            )
            entities[key] = entity
        # A group is exempt only while EVERY member row classifies exempt.
        entity.exempt = entity.exempt and _row_is_exempt(row)
        entity.drawn += row.balance_ghs
        entity.undrawn += row.undrawn_ccf_ghs
        entity.provisions += row.ecl_ghs
        if row.ifrs9_stage is not None and (
            entity.worst_stage is None or row.ifrs9_stage > entity.worst_stage
        ):
            entity.worst_stage = row.ifrs9_stage
        member_name = row.counterparty_name or row.issuer or "(unnamed)"
        member = entity.members.setdefault(
            member_name, _Member(name=member_name, tin=row.counterparty_tin)
        )
        member.exposure += exposure
    ordered = sorted(entities.values(), key=lambda entity: (-entity.total, entity.name))
    return ordered, unattributed, unattributed_count


def _entity_row(index: int, entity: _Entity, nof: Decimal, **extra: Any) -> dict[str, Any]:
    return snapshot_row(
        str(index),
        entity.name,
        entity.total,
        connection=entity.connection,
        tin=entity.tin,
        drawn_ghs=str(entity.drawn),
        undrawn_ccf_ghs=str(entity.undrawn),
        provisions_ghs=str(entity.provisions),
        pct_nof=str(_pct_of(entity.total, nof)),
        ifrs9_stage=str(entity.worst_stage) if entity.worst_stage is not None else None,
        **extra,
    )


def _exposure_section(  # noqa: PLR0913 - explicit section builder
    code: str,
    title: str,
    entities: list[_Entity],
    nof: Decimal,
    total_code: str,
    row_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [
        _entity_row(index, entity, nof, **(row_extra or {}))
        for index, entity in enumerate(entities, start=1)
    ]
    total_value = sum((entity.total for entity in entities), _ZERO)
    return snapshot_section(
        code,
        title,
        rows,
        snapshot_total(total_code, "Total exposure", total_value, equals_sum_of_rows=True),
        optional=True,
    )


def _connected_member_rows(groups: list[_Entity]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_index, group in enumerate(groups, start=1):
        members = sorted(group.members.values(), key=lambda member: (-member.exposure, member.name))
        rows.extend(
            snapshot_row(
                f"{group_index}.{member_index}",
                member.name,
                member.exposure,
                group_reference=group.name,
                basis_of_connection="Shared canonical counterparty group reference",
                tin=member.tin,
            )
            for member_index, member in enumerate(members, start=1)
        )
    return rows


def generate_large_exposures(  # noqa: PLR0914 - one linear template assembly
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    capital_run = baseline_run_or_409(
        db, ctx, bank, period, MODULE_CAPITAL, artifact="the Large Exposures return"
    )
    preview = regulatory_capital.get_bsd2_preview(db, ctx, bank.id, period.id)
    tier1 = Decimal(str(preview.tier1_total.value))
    if tier1 <= _ZERO:
        raise _conflict_409(
            "nof_not_positive",
            "Tier 1 capital from the baseline capital run is not positive; exposures "
            "cannot be expressed as a percentage of Net Own Funds.",
        )
    nof = tier1  # documented proxy: NOF = Tier 1 (CET1 + AT1) from the capital run

    rows = _load_canonical_rows(db, ctx, bank, period.period_end, _LE_POSITION_TYPES)
    if not rows:
        raise _conflict_409(
            "no_canonical_positions",
            "No accepted canonical LOAN, INTERBANK_PLACEMENT or SECURITY_HOLDING "
            f"position snapshots exist for {period.period_end.isoformat()}. Ingest "
            "position data for the period end before generating the Large Exposures "
            "return.",
        )

    entities, unattributed_ghs, unattributed_count = _aggregate_entities(rows)
    threshold = nof * _LE_THRESHOLD_FRACTION
    non_exempt = [entity for entity in entities if not entity.exempt]
    exempt = [entity for entity in entities if entity.exempt]
    template_1 = [entity for entity in non_exempt if entity.total >= threshold]
    template_1_keys = {entity.key for entity in template_1}
    template_1_groups = [entity for entity in template_1 if entity.connection == "group"]
    template_2 = entities[:_TOP_EXPOSURES_CAP]
    template_3 = [entity for entity in exempt if entity.total >= threshold]
    # Pre-CRM == post-CRM here (no CRM data in the canonical model), so every
    # non-exempt pre-CRM exposure ≥10% NOF is already in Template 1 and this
    # section is empty by construction — kept for template completeness.
    template_4 = [
        entity
        for entity in non_exempt
        if entity.total >= threshold and entity.key not in template_1_keys
    ]

    findings: list[dict[str, str]] = []
    if len(entities) > _TOP_EXPOSURES_CAP:
        findings.append(
            _finding(
                "le.top100_truncated",
                "INFO",
                f"Template 2 reports the top {_TOP_EXPOSURES_CAP} of {len(entities)} "
                "counterparty exposures; the remainder are excluded per the template "
                "definition.",
            )
        )
    if unattributed_count:
        findings.append(
            _finding(
                "le.unattributed_positions",
                "INFO",
                f"{unattributed_count} position(s) totalling {unattributed_ghs} GHS "
                "carry neither a counterparty link nor an issuer attribute and are "
                "excluded from the counterparty templates.",
            )
        )
    unconverted = sum(1 for row in rows if not row.has_ghs_value)
    if unconverted:
        findings.append(
            _finding(
                "le.missing_ghs_conversion",
                "INFO",
                f"{unconverted} foreign-currency position(s) without an ingested "
                "balance_ghs conversion contribute zero exposure (mirrors the fact "
                "pipeline; nothing is converted at a made-up rate).",
            )
        )

    sections = [
        _exposure_section(
            "template_1",
            "Template 1 — Exposures ≥10% of Net Own Funds (large exposures)",
            template_1,
            nof,
            "template_1_total_ghs",
        ),
        snapshot_section(
            "template_1a",
            "Template 1a — Details of Connected Counterparties",
            _connected_member_rows(template_1_groups),
            optional=True,
        ),
        _exposure_section(
            "template_2",
            "Template 2 — Top 100 exposures irrespective of value",
            template_2,
            nof,
            "template_2_total_ghs",
        ),
        _exposure_section(
            "template_3",
            "Template 3 — Exempted exposures ≥10% of Net Own Funds (pre-CRM)",
            template_3,
            nof,
            "template_3_total_ghs",
            row_extra={"exempt_basis": "Sovereign/central-bank/government canonical category"},
        ),
        _exposure_section(
            "template_4",
            "Template 4 — Other exposures ≥10% of NOF pre-CRM, not in Template 1",
            template_4,
            nof,
            "template_4_total_ghs",
        ),
    ]
    largest_pct = _pct_of(entities[0].total, nof) if entities else _ZERO
    totals = [
        snapshot_row("tier1_ghs", "Tier 1 Capital", tier1, unit="ghs"),
        snapshot_row("nof_ghs", "Net Own Funds (Tier 1 proxy)", nof, unit="ghs"),
        snapshot_row("large_exposure_count", "Exposures ≥10% of NOF", len(template_1)),
        snapshot_row("largest_exposure_pct_nof", "Largest exposure (% NOF)", largest_pct),
    ]
    metadata = {
        "nof_basis": (
            "Net Own Funds proxied by Tier 1 capital (CET1 + AT1) from the succeeded "
            "baseline capital run; the CRD Net-Own-Funds definition is not separately "
            "computed."
        ),
        "grouping_basis": (
            "Connected counterparties grouped by the canonical counterparty "
            "group_reference (column, then group_reference/group/parent attributes); "
            "counterparties without one stand alone."
        ),
        "exemption_basis": (
            "Exempt when counterparty_type is SOVEREIGN, CENTRAL_BANK or "
            "GOVERNMENT_ENTITY, or (counterparty-less securities) the product "
            "regulatory_category starts with SOVEREIGN."
        ),
        "crm_note": (
            "No CRM/collateral data exists in the canonical model: net exposure equals "
            "total exposure, pre-CRM equals post-CRM, and Template 4 is empty by "
            "construction. Collateral columns are omitted, not zero-filled."
        ),
        "large_exposure_threshold_pct_nof": "10",
        "single_exposure_limit_pct_nof": "20",
        "threshold_ghs": str(threshold),
        "canonical_position_count": len(rows),
        "capital_baseline_run_id": str(capital_run.id),
        "generation_findings": findings,
    }
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[source_run_entry(capital_run)],
    )


# ---------------------------------------------------------------------------
# LMT — Liquidity Monitoring Tools return (LCR subset + canonical tools)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LmtToolBlock:
    sections: list[dict[str, Any]]
    totals: list[dict[str, Any]]
    findings: list[dict[str, str]]
    notes: list[str]


def _maturity_bucket(maturity: date | None, as_of: date) -> str:
    if maturity is None:
        return _NON_CONTRACTUAL_BUCKET[0]
    days = (maturity - as_of).days
    for code, _, upper in _TABLE2_BUCKETS:
        if upper is None or days <= upper:
            return code
    return _TABLE2_BUCKETS[-1][0]


_BUCKET_CODES = tuple(code for code, _, _ in _TABLE2_BUCKETS)
_ALL_BUCKET_CODES = (*_BUCKET_CODES, _NON_CONTRACTUAL_BUCKET[0])

# Table 2's printed rows, in filing order. Derived rows (1, 5, 10, 11, 12,
# 14) are computed from the category rows; the rest accumulate positions.
_TABLE2_ROW_LABELS: tuple[tuple[str, str], ...] = (
    ("1", "Contractual maturity of assets (items 2 to 4)"),
    ("2", "Advances"),
    ("3", "Trading, hedging and other investment instruments"),
    ("4", "Other assets"),
    ("5", "Contractual maturity of liabilities (items 6 to 9)"),
    ("6", "Stable deposits"),
    ("7", "Volatile deposits"),
    ("8", "Trading and hedging instruments"),
    ("9", "Other liabilities"),
    ("10", "On-balance sheet contractual mismatch (item 1 less item 5)"),
    ("11", "Cumulative on-balance sheet contractual mismatch"),
    ("12", "Off-balance sheet exposure to liquidity risk, of which:"),
    ("13", "Liquidity facilities provided to off-balance-sheet vehicles"),
    ("14", "Undrawn commitments (items 15 to 17)"),
    ("15", "Unutilised portion of irrevocable lending facilities"),
    ("16", "Unutilised portion of irrevocable letters of credit"),
    ("17", "Indemnities and guarantees"),
)

_Vector = dict[str, Decimal]


def _vector() -> _Vector:
    return dict.fromkeys(_ALL_BUCKET_CODES, _ZERO)


def _vector_sum(*vectors: _Vector) -> _Vector:
    out = _vector()
    for vec in vectors:
        for code in _ALL_BUCKET_CODES:
            out[code] += vec[code]
    return out


def _vector_sub(left: _Vector, right: _Vector) -> _Vector:
    return {code: left[code] - right[code] for code in _ALL_BUCKET_CODES}


def _table2_row_for(row: _CanonicalRow) -> tuple[str, Decimal] | None:  # noqa: PLR0911 - one return per printed row category
    """(printed row number, contribution amount) for one canonical position.

    Classification is deterministic and documented — never heuristic:
    on-balance categories map from ``position_type`` (with the derivative
    sign split), deposits split stable/volatile on ``deposit_account_type``,
    and OBS sub-rows follow ``attributes["obs_category"]`` with documented
    per-type defaults. Amounts: on-balance rows use the GHS balance; OBS
    rows use the GHS notional (the unutilised amount itself, not a
    CCF-weighted capital equivalent), falling back to balance.
    """
    kind = row.position_type
    if kind in _T2_ADVANCES:
        return "2", row.balance_ghs
    if kind == "SECURITY_HOLDING":
        return "3", row.balance_ghs
    if kind in _T2_DERIVATIVE_FAMILY:
        if row.balance_ghs < _ZERO:
            return "8", -row.balance_ghs
        return "3", row.balance_ghs
    if kind in _T2_OTHER_ASSETS:
        return "4", row.balance_ghs
    if kind == "DEPOSIT":
        account_type = (row.deposit_account_type or "").upper()
        if account_type in _VOLATILE_DEPOSIT_TYPES:
            return "7", row.balance_ghs
        return "6", row.balance_ghs
    if kind in _T2_OTHER_LIABILITIES:
        return "9", row.balance_ghs
    if kind in _T2_OFF_BALANCE:
        amount = row.notional_ghs if row.notional_ghs is not None else row.balance_ghs
        category = (row.obs_category or "").strip().lower()
        target = _OBS_CATEGORY_ROWS.get(category)
        if target is None:
            target = "15" if kind == "COMMITMENT_UNDRAWN" else "17"
        return target, amount
    return None


def _table2_snapshot_row(number: str, label: str, vector: _Vector) -> dict[str, Any]:
    total = sum((vector[code] for code in _ALL_BUCKET_CODES), _ZERO)
    return snapshot_row(
        number, label, total, **{code: str(vector[code]) for code in _ALL_BUCKET_CODES}
    )


def _ladder_section(rows: list[_CanonicalRow], as_of: date) -> tuple[dict[str, Any], Decimal]:
    """LMTD Table 2 — the full 17-row × 15-column published grid."""
    accumulated: dict[str, _Vector] = {number: _vector() for number, _ in _TABLE2_ROW_LABELS}
    for row in rows:
        target = _table2_row_for(row)
        if target is None:
            continue
        number, amount = target
        accumulated[number][_maturity_bucket(row.contractual_maturity, as_of)] += amount

    vectors = accumulated
    vectors["1"] = _vector_sum(vectors["2"], vectors["3"], vectors["4"])
    vectors["5"] = _vector_sum(vectors["6"], vectors["7"], vectors["8"], vectors["9"])
    vectors["10"] = _vector_sub(vectors["1"], vectors["5"])
    vectors["14"] = _vector_sum(vectors["15"], vectors["16"], vectors["17"])
    # Row 12 is "of which" 13 + 14; a source can additionally book other OBS
    # liquidity exposures straight to row 12 via obs_category in the future —
    # today it is exactly the sum of its sub-rows.
    vectors["12"] = _vector_sum(vectors["13"], vectors["14"])

    labels = dict(_TABLE2_ROW_LABELS)
    table_rows: list[dict[str, Any]] = []
    on_balance_mismatch_total = _ZERO
    for number, label in _TABLE2_ROW_LABELS:
        if number == "11":
            # Cumulative mismatch runs across dated buckets only; the
            # non-contractual column sits outside the run (kept blank, as on
            # the printed form).
            cumulative = _ZERO
            extras: dict[str, str] = {}
            for code in _BUCKET_CODES:
                cumulative += vectors["10"][code]
                extras[code] = str(cumulative)
            table_rows.append(snapshot_row(number, labels[number], cumulative, **extras))
            continue
        table_rows.append(_table2_snapshot_row(number, label, vectors[number]))
        if number == "10":
            on_balance_mismatch_total = sum(
                (vectors["10"][code] for code in _ALL_BUCKET_CODES), _ZERO
            )

    section = snapshot_section(
        "maturity_ladder",
        "Contractual Maturity Mismatch",
        table_rows,
        snapshot_total(
            "on_balance_mismatch_total_ghs",
            "On-balance sheet contractual mismatch — total (item 1 less item 5)",
            on_balance_mismatch_total,
            equals_sum_of_rows=False,
        ),
    )
    return section, on_balance_mismatch_total


def _within_one_year(maturity: date | None, as_of: date) -> bool:
    return maturity is not None and (maturity - as_of).days <= _ONE_YEAR_DAYS


def _unencumbered(row: _CanonicalRow) -> bool:
    """NULL is treated as unencumbered — the backward-compatible reading; the
    lmtd_classification_coverage ingestion rule surfaces the gap."""
    return row.encumbered is not True


def _is_sovereign_security(row: _CanonicalRow) -> bool:
    return row.position_type == "SECURITY_HOLDING" and (
        (row.regulatory_category or "").startswith(_SOVEREIGN_CATEGORY_PREFIX)
    )


def _narrow_liquid_amount(  # noqa: PLR0911 - one return per directive leg
    row: _CanonicalRow, as_of: date
) -> Decimal:
    """LMTD ¶5 Narrow Liquid Assets, leg by leg, conservatively.

    Legs: (a) vault notes and coins — CASH without a counterparty link;
    (b) unencumbered non-resident correspondent balances held for operational
    purposes — CASH with a non-resident bank counterparty flagged
    operational_purpose; (c) placements with non-resident FIs rated AAA;
    (d) balances at the central bank — CASH with a CENTRAL_BANK counterparty;
    (e) unencumbered sovereign/central-bank bills ≤1yr; (f) other ≤1yr
    securities redeemable within two working days, unencumbered, not already
    counted under (e); (g) claims on other domestic banks — placements with
    resident bank counterparties. Every leg requires its positive signal;
    unknown residency or purpose is never assumed — understating liquid
    assets is the only safe direction for a prudential ratio.
    """
    kind = row.position_type
    cp_type = row.counterparty_type
    is_bank_cp = cp_type in _BANK_COUNTERPARTY_TYPES
    if kind == "CASH":
        if cp_type is None:
            return row.balance_ghs  # (a) vault cash
        if cp_type == "CENTRAL_BANK":
            return row.balance_ghs  # (d) balances at the central bank
        if (
            is_bank_cp
            and row.counterparty_resident is False
            and row.operational_purpose is True
            and _unencumbered(row)
        ):
            return row.balance_ghs  # (b) operational correspondent balances
        if is_bank_cp and row.counterparty_resident is True:
            return row.balance_ghs  # (g) claims on other domestic banks
        return _ZERO
    if kind == "INTERBANK_PLACEMENT" and is_bank_cp:
        if row.counterparty_resident is False and (row.counterparty_rating or "").startswith(
            "AAA"
        ):
            return row.balance_ghs  # (c) AAA non-resident FI placements
        if row.counterparty_resident is True:
            return row.balance_ghs  # (g) claims on other domestic banks
        return _ZERO
    if kind == "SECURITY_HOLDING" and _unencumbered(row) and _within_one_year(
        row.contractual_maturity, as_of
    ):
        if _is_sovereign_security(row):
            return row.balance_ghs  # (e) GoG/BoG bills ≤ 1 yr
        if row.redeemable_within_two_days is True:
            return row.balance_ghs  # (f) two-working-day redeemables
    return _ZERO


def _table1_inputs(rows: list[_CanonicalRow], as_of: date) -> dict[str, Decimal]:
    """The six Table 1 inputs from the canonical book (LMTD ¶5 definitions)."""
    inputs = {key: _ZERO for key, _ in _TABLE1_INPUT_LABELS}
    equities = _ZERO
    for row in rows:
        narrow = _narrow_liquid_amount(row, as_of)
        inputs["narrow"] += narrow
        # Broad = Narrow + unencumbered GoG bonds > 1 yr (marketable, freely
        # transferable) + GSE-listed equities capped at 10% of total liquid
        # assets (cap applied after the loop).
        if narrow == _ZERO and _is_sovereign_security(row) and _unencumbered(row):
            inputs["broad"] += row.balance_ghs
        if (
            row.position_type == "SECURITY_HOLDING"
            and _unencumbered(row)
            and (row.regulatory_category or "").startswith(_EQUITY_CATEGORY_PREFIX)
        ):
            equities += row.balance_ghs

        if row.position_type == "DEPOSIT":
            inputs["total_deposits"] += row.balance_ghs
            account_type = (row.deposit_account_type or "").upper()
            if account_type in _VOLATILE_DEPOSIT_TYPES:
                inputs["volatile"] += row.balance_ghs
            if account_type in _SHORT_TERM_BY_NATURE or _within_one_year(
                row.contractual_maturity, as_of
            ):
                inputs["short_term"] += row.balance_ghs
        elif row.position_type in _T2_OTHER_LIABILITIES and _within_one_year(
            row.contractual_maturity, as_of
        ):
            inputs["short_term"] += row.balance_ghs
        elif row.position_type in _T2_OFF_BALANCE and _within_one_year(
            row.contractual_maturity, as_of
        ):
            # ¶5 short-term liabilities (d): contingent liabilities ≤ 1 yr.
            amount = row.notional_ghs if row.notional_ghs is not None else row.balance_ghs
            inputs["short_term"] += amount

        if row.position_type in _TOTAL_ASSET_TYPES or (
            row.position_type in _T2_DERIVATIVE_FAMILY and row.balance_ghs > _ZERO
        ):
            inputs["total_assets"] += row.balance_ghs

    inputs["broad"] += inputs["narrow"]
    # GSE equities cap: E ≤ 10% of total liquid assets, total including the
    # capped equities themselves → E_capped = min(E, other_broad / 9).
    if equities > _ZERO:
        inputs["broad"] += min(equities, inputs["broad"] / Decimal("9"))
    return inputs


def _table1_thresholds(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[str, tuple[Decimal, str]]:
    """Ratio floor per code: Board register row when one is active, else the
    directive's published bank minimum (the regulatory floor)."""
    resolved: dict[str, tuple[Decimal, str]] = {
        code: (minimum, "regulatory_default")
        for code, minimum in _TABLE1_BANK_MINIMUM_PCT.items()
    }
    jurisdiction = (bank.jurisdiction_code or "GH").strip().upper()
    for row in get_active_params(
        db, ctx.organization_id, jurisdiction, ParamLiquidityThreshold, as_of
    ):
        if row.institution_class == "bank" and row.threshold_code in resolved:
            resolved[row.threshold_code] = (Decimal(str(row.threshold_pct)), "board_register")
    return resolved


def _previous_period_end(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> date | None:
    return db.scalar(
        select(BankReportingPeriod.period_end)
        .where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end < period.period_end,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


def _prudential_ratio_sections(
    current: dict[str, Decimal],
    previous: dict[str, Decimal] | None,
    thresholds: dict[str, tuple[Decimal, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    """LMTD Table 1 as two blocks: GHS inputs, then the percentage grid."""
    input_rows: list[dict[str, Any]] = []
    for key, label in _TABLE1_INPUT_LABELS:
        extras: dict[str, str] = {}
        if previous is not None:
            extras["previous_month_ghs"] = str(previous[key])
        input_rows.append(snapshot_row(key, label, current[key], **extras))

    ratio_rows: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    breaches = 0
    for code, label, numerator_key, denominator_key in _TABLE1_RATIOS:
        minimum, threshold_source = thresholds[code]
        denominator = current[denominator_key]
        extras = {
            "threshold_min_pct": str(minimum),
            "threshold_source": threshold_source,
        }
        if previous is not None and previous[denominator_key] > _ZERO:
            extras["previous_month_pct"] = str(
                _pct_of(previous[numerator_key], previous[denominator_key])
            )
        if denominator <= _ZERO:
            extras["status"] = "not_computable"
            ratio_rows.append(snapshot_row(code, label, _ZERO, **extras))
            continue
        ratio = _pct_of(current[numerator_key], denominator)
        below = ratio < minimum
        extras["status"] = "below_minimum" if below else "ok"
        ratio_rows.append(snapshot_row(code, label, ratio, **extras))
        if below:
            breaches += 1
            findings.append(
                _finding(
                    "lmt.prudential_ratio_below_minimum",
                    "WARNING",
                    f"{label}: {ratio}% is below the {minimum}% floor "
                    f"({threshold_source.replace('_', ' ')}).",
                )
            )

    sections = [
        snapshot_section(
            "prudential_ratio_inputs",
            "BOG Prudential Ratios",
            input_rows,
            snapshot_total(
                "narrow_liquid_assets_ghs",
                "Narrow Liquid Assets",
                current["narrow"],
                equals_sum_of_rows=False,
            ),
        ),
        snapshot_section(
            "prudential_ratio_percentages",
            "BOG Prudential Ratios (%)",
            ratio_rows,
        ),
    ]
    return sections, findings, breaches


# --- Significant currency (LMTD ¶30/¶41; built 2026-08-07) -----------------
#
# A currency is significant when liabilities denominated in it are ≥ 5% of
# total liabilities. (¶36 uses a different denominator — total available
# unencumbered collateral — for Table 9C; both live here so the two are never
# conflated.) Amounts are cedi equivalents throughout ("Values in Cedi" on
# the printed forms), i.e. the ingested balance_ghs conversions.
_SIGNIFICANT_CURRENCY_THRESHOLD = Decimal("0.05")
# Table 11's printed columns are fixed: Cedi | USD | Pound | Euro | Others.
# Ghana-factual literals are the documented exception inside BoG return
# artifacts; the "cedi" column maps to the bank's base currency.
_TABLE11_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cedi_ghs", "Cedi"),
    ("usd_ghs", "USD"),
    ("pound_ghs", "Pound"),
    ("euro_ghs", "Euro"),
    ("others_ghs", "Others"),
)
_TABLE11_ISO = {"USD": "usd_ghs", "GBP": "pound_ghs", "EUR": "euro_ghs"}
_THIRTY_DAYS = 30
_LCR_INFLOW_CAP = Decimal("0.75")


def _is_t2_liability(row: _CanonicalRow) -> bool:
    target = _table2_row_for(row)
    return target is not None and target[0] in ("6", "7", "8", "9")


def _is_t2_asset(row: _CanonicalRow) -> bool:
    target = _table2_row_for(row)
    return target is not None and target[0] in ("2", "3", "4")


def _liabilities_by_currency(rows: list[_CanonicalRow]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        target = _table2_row_for(row)
        if target is not None and target[0] in ("6", "7", "8", "9"):
            totals[row.currency] = totals.get(row.currency, _ZERO) + target[1]
    return totals


def _significant_currencies(rows: list[_CanonicalRow]) -> list[str]:
    """Currencies whose liabilities are ≥ 5% of total liabilities, largest first."""
    by_currency = _liabilities_by_currency(rows)
    total = sum(by_currency.values(), _ZERO)
    if total <= _ZERO:
        return []
    return [
        currency
        for currency, amount in sorted(by_currency.items(), key=lambda kv: (-kv[1], kv[0]))
        if amount / total >= _SIGNIFICANT_CURRENCY_THRESHOLD
    ]


def _table6_section(rows: list[_CanonicalRow]) -> tuple[dict[str, Any] | None, int]:
    """LMTD Table 6 — assets and liabilities per significant currency."""
    significant = _significant_currencies(rows)
    if not significant:
        return None, 0
    total_liabilities = sum(_liabilities_by_currency(rows).values(), _ZERO)
    assets_by_ccy: dict[str, Decimal] = {}
    liabilities_by_ccy: dict[str, Decimal] = {}
    for row in rows:
        target = _table2_row_for(row)
        if target is None:
            continue
        number, amount = target
        if number in ("2", "3", "4"):
            assets_by_ccy[row.currency] = assets_by_ccy.get(row.currency, _ZERO) + amount
        elif number in ("6", "7", "8", "9"):
            liabilities_by_ccy[row.currency] = (
                liabilities_by_ccy.get(row.currency, _ZERO) + amount
            )

    section_rows: list[dict[str, Any]] = []
    total_mismatch = _ZERO
    for currency in significant:
        assets = assets_by_ccy.get(currency, _ZERO)
        liabilities = liabilities_by_ccy.get(currency, _ZERO)
        mismatch = assets - liabilities
        total_mismatch += mismatch
        extras = {
            "assets_ghs": str(assets),
            "liabilities_ghs": str(liabilities),
            "mismatch_pct_total_liabilities": (
                str(_pct_of(mismatch, total_liabilities))
                if total_liabilities > _ZERO
                else "0"
            ),
        }
        section_rows.append(snapshot_row(currency, currency, mismatch, **extras))
    section = snapshot_section(
        "assets_liabilities_by_currency",
        "List of Assets and Liabilities by Significant Currencies",
        section_rows,
        snapshot_total(
            "currency_mismatch_total_ghs",
            "Total mismatch across significant currencies",
            total_mismatch,
            equals_sum_of_rows=True,
        ),
    )
    return section, len(significant)


def _table11_column(currency: str, base_currency: str) -> str:
    if currency == base_currency:
        return "cedi_ghs"
    return _TABLE11_ISO.get(currency, "others_ghs")


def _table11_section(  # noqa: PLR0914 - one linear grid assembly
    rows: list[_CanonicalRow], as_of: date, base_currency: str
) -> dict[str, Any]:
    """LMTD Table 11 — LCR by significant currency (fixed five columns).

    Honest calibration posture, documented on the template: HQLA Level 1 is
    computed from canonical classification (cash-family + unencumbered
    sovereign securities); Levels 2A/2B report zero because the canonical
    model carries no L2 taxonomy — never invented. Outflows take the most
    conservative contractual basis (liabilities maturing ≤ 30 days plus
    demand deposits in full) pending the unpublished LCR Directive's run-off
    calibration; inflows are contractual asset maturities ≤ 30 days subject
    to the Basel 75% cap. Net cash outflow is computed (1)−(2) — the printed
    form's "(2-1)" label inverts the LCR and is carried as a documented
    deviation, not reproduced.
    """
    column_keys = [key for key, _ in _TABLE11_COLUMNS]
    zero = dict.fromkeys(column_keys, _ZERO)
    l1 = dict(zero)
    outflow = dict(zero)
    inflow = dict(zero)
    for row in rows:
        column = _table11_column(row.currency, base_currency)
        days = (
            (row.contractual_maturity - as_of).days
            if row.contractual_maturity is not None
            else None
        )
        # HQLA Level 1: cash-family legs + unencumbered sovereign securities.
        if row.position_type == "CASH" or _is_sovereign_security(row) and _unencumbered(row):
            l1[column] += row.balance_ghs
        # Contractual 30-day outflows: maturing liabilities + demand deposits.
        target = _table2_row_for(row)
        if target is not None and target[0] in ("6", "7", "8", "9"):
            account_type = (row.deposit_account_type or "").upper()
            if account_type in _VOLATILE_DEPOSIT_TYPES or (
                days is not None and days <= _THIRTY_DAYS
            ):
                outflow[column] += target[1]
        # Contractual 30-day inflows: maturing assets (never HQLA double-count).
        elif (
            target is not None
            and target[0] in ("2", "3", "4")
            and days is not None
            and days <= _THIRTY_DAYS
            and row.position_type != "CASH"
            and not _is_sovereign_security(row)
        ):
            inflow[column] += target[1]

    def _grid_row(
        code: str, label: str, values: dict[str, Decimal], total: Decimal | None = None
    ) -> dict[str, Any]:
        row_total = total if total is not None else sum(values.values(), _ZERO)
        return snapshot_row(
            code, label, row_total, **{key: str(values[key]) for key in column_keys}
        )

    capped_inflow = {
        key: min(inflow[key], (outflow[key] * _LCR_INFLOW_CAP)) for key in column_keys
    }
    net = {key: outflow[key] - capped_inflow[key] for key in column_keys}
    ratio: dict[str, Decimal] = {}
    for key in column_keys:
        ratio[key] = _pct_of(l1[key], net[key]) if net[key] > _ZERO else _ZERO
    aggregate_l1 = sum(l1.values(), _ZERO)
    aggregate_net = sum(net.values(), _ZERO)
    aggregate_ratio = _pct_of(aggregate_l1, aggregate_net) if aggregate_net > _ZERO else _ZERO

    section_rows = [
        _grid_row("stock_of_hqla", "Stock of HQLA", l1),
        _grid_row("level_1", "Level 1", l1),
        _grid_row("level_2a", "Level 2A", dict(zero)),
        _grid_row("level_2b", "Level 2B", dict(zero)),
        _grid_row("total_adjusted_hqla", "A) Total adjusted HQLA", l1),
        _grid_row("total_cash_outflow", "Total Cash outflow (1)", outflow),
        _grid_row("total_cash_inflow", "Total Cash inflow (2)", capped_inflow),
        _grid_row("net_cash_outflow", "B) Net Cash Outflow (1-2)", net),
        _grid_row(
            "lcr_pct",
            "Liquidity Coverage Ratio (LCR) (A/B*100)",
            ratio,
            total=aggregate_ratio,
        ),
    ]
    return snapshot_section(
        "lcr_by_currency",
        "LCR by Significant Currency",
        section_rows,
    )


def _funding_concentration_section(
    deposit_rows: list[_CanonicalRow],
) -> tuple[dict[str, Any] | None, Decimal, Decimal, list[dict[str, str]]]:
    total_deposits = sum((row.balance_ghs for row in deposit_rows), _ZERO)
    by_counterparty: dict[UUID, dict[str, Any]] = {}
    unattributed = _ZERO
    for row in deposit_rows:
        if row.counterparty_id is None:
            unattributed += row.balance_ghs
            continue
        entry = by_counterparty.setdefault(
            row.counterparty_id,
            {
                "name": row.counterparty_name or str(row.counterparty_id),
                "type": row.counterparty_type or "",
                "amount": _ZERO,
            },
        )
        entry["amount"] += row.balance_ghs

    findings: list[dict[str, str]] = []
    if unattributed > _ZERO:
        findings.append(
            _finding(
                "lmt.unattributed_deposits",
                "INFO",
                f"Deposits of {unattributed} GHS carry no counterparty link; they are "
                "included in total deposit liabilities but cannot be ranked by "
                "depositor.",
            )
        )
    if not by_counterparty or total_deposits <= _ZERO:
        return None, total_deposits, _ZERO, findings

    ranked = sorted(by_counterparty.values(), key=lambda entry: (-entry["amount"], entry["name"]))
    top = ranked[:_TOP_DEPOSITORS]
    top_total = sum((entry["amount"] for entry in top), _ZERO)
    rows = [
        snapshot_row(
            str(index),
            entry["name"],
            entry["amount"],
            pct_total_deposits=str(_pct_of(entry["amount"], total_deposits)),
            counterparty_type=entry["type"],
        )
        for index, entry in enumerate(top, start=1)
    ]
    section = snapshot_section(
        "funding_concentration",
        "Concentration of Funding (Top 10 Depositors)",
        rows,
        snapshot_total("total_deposits_ghs", "Total deposit liabilities", total_deposits),
    )
    return section, total_deposits, _pct_of(top_total, total_deposits), findings


def _unencumbered_assets_section(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[dict[str, Any] | None, Decimal]:
    facts = list(
        db.scalars(
            select(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group == "securities",
                BankFinancialFact.hqla_level.is_not(None),
            )
            .order_by(BankFinancialFact.category)
        )
    )
    if not facts:
        return None, _ZERO
    rows = [
        snapshot_row(
            fact.category,
            fact.category.replace("_", " "),
            fact.amount,
            hqla_level=fact.hqla_level,
        )
        for fact in facts
    ]
    total = sum((Decimal(str(fact.amount)) for fact in facts), _ZERO)
    section = snapshot_section(
        "unencumbered_assets",
        "Available Unencumbered Assets",
        rows,
        snapshot_total(
            "unencumbered_assets_total_ghs",
            "Total HQLA-classified assets",
            total,
            equals_sum_of_rows=True,
        ),
    )
    return section, total


_LMT_TOOL_NOTES = [
    (
        "Prudential ratios (Table 1): six inputs computed leg-by-leg from the "
        "canonical book per the LMTD para 5 definitions, eight ratios against "
        "the Board threshold register (regulatory published minimums where no "
        "Board row is active). Classification is conservative: a leg counts "
        "only on its positive signal (residency, operational purpose, two-day "
        "redeemability, encumbrance), never by assumption. Current/call/"
        "savings deposits are short-term by nature regardless of stated "
        "maturity; behavioural lives never enter these ratios. Previous-month "
        "column reports only when a prior period's canonical book exists — "
        "never fabricated."
    ),
    (
        "Significant currency (Tables 6 and 11): a currency is significant when "
        "liabilities in it are >= 5% of total liabilities (LMTD para 30/41); all "
        "amounts are cedi equivalents from ingested conversions. Table 11 keeps "
        "the printed fixed columns (Cedi/USD/Pound/Euro/Others); Level 2A/2B "
        "report zero because the canonical model carries no L2 taxonomy; "
        "outflows take the most conservative contractual basis (maturing <= 30d "
        "+ demand deposits in full) pending the unpublished LCR Directive "
        "calibration; the printed form's Net Cash Outflow '(2-1)' label inverts "
        "the LCR and is implemented as (1-2) — a documented deviation."
    ),
    (
        "Maturity ladder: the full published Table 2 grid — 17 rows x 15 columns "
        "(13 dated buckets + Total + Non-contractual). Deposits split stable/"
        "volatile on the ingested deposit_account_type (volatile = current + call "
        "per LMTD para 5; unclassified deposits report as stable and are flagged "
        "at the ingestion gate). Derivative-family instruments split by carrying-"
        "value sign (asset MTM to row 3, liability MTM to row 8). Off-balance "
        "rows 13/15/16/17 classify on attributes['obs_category'] with documented "
        "defaults (undrawn commitments to row 15, LC/guarantees to row 17); "
        "amounts are unutilised notionals, not CCF-weighted equivalents. Bucket "
        "bounds are calendar-approximate day counts; undated positions report "
        "non-contractual, never Next Day."
    ),
    (
        "Funding concentration: top-10 depositors by canonical counterparty; the "
        "published Table 5 asks Top 20 and Top 100 — the derivation reports what "
        "the ingested counterparty links support."
    ),
    (
        "Available unencumbered assets: HQLA-classified securities facts; the "
        "canonical model carries no encumbrance flags, secondary-market haircuts "
        "or monetised values, so those Table 9 columns are omitted, not invented."
    ),
]


def _build_lmt_tool_block(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> _LmtToolBlock:
    sections: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []

    ladder_rows = _load_canonical_rows(db, ctx, bank, period.period_end, _TABLE2_POSITION_TYPES)
    if ladder_rows:
        # Table 1 — BOG Prudential Ratios (tool (a), first in the appendix).
        current_inputs = _table1_inputs(ladder_rows, period.period_end)
        previous_inputs: dict[str, Decimal] | None = None
        previous_end = _previous_period_end(db, ctx, bank, period)
        if previous_end is not None:
            previous_rows = _load_canonical_rows(
                db, ctx, bank, previous_end, _TABLE2_POSITION_TYPES
            )
            if previous_rows:
                previous_inputs = _table1_inputs(previous_rows, previous_end)
        thresholds = _table1_thresholds(db, ctx, bank, period.period_end)
        ratio_sections, ratio_findings, breaches = _prudential_ratio_sections(
            current_inputs, previous_inputs, thresholds
        )
        sections.extend(ratio_sections)
        findings.extend(ratio_findings)
        totals.append(
            snapshot_row(
                "narrow_liquid_assets_ghs",
                "Narrow Liquid Assets",
                current_inputs["narrow"],
                unit="ghs",
            )
        )
        totals.append(
            snapshot_row(
                "broad_liquid_assets_ghs",
                "Broad Liquid Assets",
                current_inputs["broad"],
                unit="ghs",
            )
        )
        totals.append(
            snapshot_row(
                "prudential_ratio_breaches",
                "Prudential ratios below their floor",
                breaches,
                unit="count",
            )
        )

        ladder_section, mismatch_total = _ladder_section(ladder_rows, period.period_end)
        sections.append(ladder_section)
        totals.append(
            snapshot_row(
                "on_balance_mismatch_total_ghs",
                "On-balance sheet contractual mismatch (item 1 less item 5)",
                mismatch_total,
                unit="ghs",
            )
        )

        # Tables 6 and 11 — the significant-currency splits.
        table6, significant_count = _table6_section(ladder_rows)
        if table6 is not None:
            sections.append(table6)
            totals.append(
                snapshot_row(
                    "significant_currencies",
                    "Significant currencies (liabilities >= 5% of total)",
                    significant_count,
                    unit="count",
                )
            )
        base_currency = (bank.currency or "GHS").strip().upper()
        sections.append(_table11_section(ladder_rows, period.period_end, base_currency))

    deposit_rows = [row for row in ladder_rows if row.position_type == "DEPOSIT"]
    concentration, total_deposits, top_share_pct, deposit_findings = _funding_concentration_section(
        deposit_rows
    )
    findings.extend(deposit_findings)
    if concentration is not None:
        sections.append(concentration)
        totals.append(
            snapshot_row(
                "total_deposits_ghs", "Total deposit liabilities", total_deposits, unit="ghs"
            )
        )
        totals.append(
            snapshot_row(
                "top10_depositor_share_pct",
                "Top-10 depositor share of total deposits",
                top_share_pct,
                unit="pct",
            )
        )

    unencumbered, unencumbered_total = _unencumbered_assets_section(db, ctx, bank, period)
    if unencumbered is not None:
        sections.append(unencumbered)
        totals.append(
            snapshot_row(
                "unencumbered_assets_ghs",
                "Available unencumbered (HQLA-classified) assets",
                unencumbered_total,
                unit="ghs",
            )
        )

    return _LmtToolBlock(
        sections=sections, totals=totals, findings=findings, notes=list(_LMT_TOOL_NOTES)
    )


def generate_lmt(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """LMT return: liquidity-run LCR subset + canonical monitoring tools.

    The three tool sections appear only when their underlying data exists —
    an LMT generated without canonical positions carries the LCR subset (and
    any HQLA fact section) exactly as before, never empty fabricated grids.
    """
    preview = regulatory_liquidity.get_bsd3_preview(db, ctx, bank.id, period.id)
    sections = lcr_snapshot_sections(preview)
    totals = lcr_snapshot_totals(preview)
    tools = _build_lmt_tool_block(db, ctx, bank, period)
    sections.extend(tools.sections)
    totals.extend(tools.totals)
    metadata = {
        **liquidity_snapshot_metadata(preview),
        "lmt_tool_notes": tools.notes,
        "generation_findings": tools.findings,
    }
    runs = latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_LIQUIDITY)
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[source_run_entry(run) for _, run in sorted(runs.items())],
    )


LE_GENERATORS = {
    "large_exposures": generate_large_exposures,
    "lmt": generate_lmt,
}

__all__ = [
    "LE_GENERATORS",
    "generate_large_exposures",
    "generate_lmt",
]
