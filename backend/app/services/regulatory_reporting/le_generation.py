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
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    ParamLiquidityHaircut,
    ParamLiquidityThreshold,
    RegulatoryRun,
    RelatedParty,
)
from app.services import (
    jurisdictions,
    regulatory_capital,
    regulatory_liquidity,
    regulatory_parameters,
    sdi_capital,
)
from app.services.institution_types import institution_class as _resolve_institution_class
from app.services.liquidity_thresholds import BANK_MINIMUM_PCT as _TABLE1_BANK_MINIMUM_PCT
from app.services.params import get_active_params
from app.services.regulatory_reporting.common import (
    unvalidated_book_detail,
    unvalidated_book_finding,
    unvalidated_book_rows,
)
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
    # Table 8 negotiable-paper convention + counterparty-side related hint.
    funding_instrument: str | None
    counterparty_related_hint: bool
    # Tables 4/10 collateral-received conventions: the documented
    # ``collateral_*`` / ``own_debt_*`` attribute keys, present only when the
    # source supplies them (docs/API_INTEGRATION.md §3.4).
    collateral: dict[str, Any] | None
    product_name: str | None


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


def _unvalidated_disclosure(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[dict[str, dict[str, int]], list[dict[str, str]]]:
    """What ``_load_canonical_rows`` refused to read at ``as_of``, stated.

    Forensic re-audit 2026-08-22, D-4. Closing that finding gave every canonical
    reader in this package the engines' admitted scope
    (``INCLUDED_VALIDATION_STATUSES``), so the Large Exposures and LMT returns
    no longer report exposures the capital and liquidity engines refuse. The
    exclusion alone is only half of it: a return compiled while rows sit in
    ``pending``, ``error`` or ``blocked`` simply reports a smaller figure and
    says nothing, and on THESE two returns the understatement is a
    concentration limit and a maturity ladder — the reader cannot infer it from
    a subtotal, because the omitted counterparty has no row to be short.

    ``bound="on"`` because ``_load_canonical_rows`` matches ``as_of_date``
    EXACTLY (unlike the BoG-form resolvers, which take the latest snapshot on or
    before period end). Disclosing an earlier date's backlog here would name
    rows this return never looked at.
    """
    counts = unvalidated_book_rows(db, ctx, bank, as_of=as_of, bound="on")
    note = unvalidated_book_detail(counts, as_of=as_of, bound="on")
    return counts, unvalidated_book_finding(note)


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
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type.in_(position_types),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()

    base_currency = jurisdictions.base_currency(bank)
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
                counterparty_resident=(counterparty.resident if counterparty is not None else None),
                counterparty_rating=counterparty.rating if counterparty is not None else None,
                funding_instrument=(
                    str(attributes["funding_instrument"]).strip().lower()
                    if attributes.get("funding_instrument")
                    else None
                ),
                counterparty_related_hint=_counterparty_related_hint(counterparty),
                collateral=_collateral_attributes(attributes),
                product_name=product.name if product is not None else None,
            )
        )
    return rows


_COLLATERAL_KEYS = (
    "collateral_instrument",
    "collateral_asset_class",
    "collateral_received_ghs",
    "collateral_rehypothecable",
    "collateral_rehypothecated_ghs",
    "collateral_unavailable_ghs",
    "collateral_group_issued",
    "collateral_bog_eligible",
    "own_debt_available_ghs",
    "own_debt_unavailable_ghs",
)


def _collateral_attributes(attributes: dict[str, Any]) -> dict[str, Any] | None:
    present = {key: attributes[key] for key in _COLLATERAL_KEYS if attributes.get(key)}
    return present or None


def _counterparty_related_hint(counterparty: CanonicalCounterparty | None) -> bool:
    """True when the SOURCE marks the counterparty related/intragroup."""
    if counterparty is None:
        return False
    attributes = counterparty.attributes or {}
    for key in ("related_party", "intragroup"):
        value = attributes.get(key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, str) and value.strip().lower() in ("true", "yes", "y", "1"):
            return True
    return False


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


def _append_exposure_limit_findings(  # noqa: PLR0913 - findings accumulator + context
    db: Session,
    bank: Bank,
    as_of: date,
    entities: list[_Entity],
    nof: Decimal,
    findings: list[dict[str, str]],
) -> None:
    """Enforce the exposure limits (docs/sdi.md §4.3), resolved for the tenant's
    institution class from the regulatory-parameter control plane — never
    hardcoded. Two distinct limits apply at the connected-group level:

    * single-obligor limit — 25% of NOF for both classes (Act 930 s.62(1));
    * per-obligor large-exposure limit — 20% (bank) / 15% (SDI), Large Exposures
      Directive.

    A breach raises a WARNING naming the counterparty group, its percentage of
    NOF, the limit, and the source citation (regulatory defensibility). A limit
    whose value is still pending BoG confirmation says so in the finding. These
    findings only fire on an ACTUAL breach, so a book within limits is unchanged.
    """
    # try_resolve (not resolve) so an unseeded jurisdiction skips the check
    # gracefully instead of raising a 500 mid-generation (audit m6). The GH bank
    # and SDI classes are seeded, so the limits apply as intended.
    single_obligor = regulatory_parameters.try_resolve(
        db, bank, "single_obligor_limit_pct", as_of=as_of
    )
    large_exposure = regulatory_parameters.try_resolve(
        db, bank, "large_exposure_limit_pct", as_of=as_of
    )
    so_limit = single_obligor.value if single_obligor is not None else None
    le_limit = large_exposure.value if large_exposure is not None else None
    for entity in entities:
        pct = _pct_of(entity.total, nof)
        if so_limit is not None and pct > so_limit:
            findings.append(
                _finding(
                    "le.single_obligor_limit_exceeded",
                    "WARNING",
                    f"{entity.name} at {pct}% of Net Own Funds exceeds the single-obligor "
                    f"limit of {so_limit}% ({single_obligor.source_citation}).",  # type: ignore[union-attr]
                )
            )
        if le_limit is not None and pct > le_limit:
            pending = (
                " (limit value pending BoG confirmation)"
                if large_exposure is not None and large_exposure.is_pending
                else ""
            )
            findings.append(
                _finding(
                    "le.large_exposure_limit_exceeded",
                    "WARNING",
                    f"{entity.name} at {pct}% of Net Own Funds exceeds the large-exposure "
                    f"limit of {le_limit}%{pending} "
                    f"({large_exposure.source_citation}).",  # type: ignore[union-attr]
                )
            )


def _append_aggregate_exposure_finding(  # noqa: PLR0913 - findings accumulator + context
    db: Session,
    bank: Bank,
    as_of: date,
    large_exposures: list[_Entity],
    nof: Decimal,
    findings: list[dict[str, str]],
) -> None:
    """The aggregate large-exposure cap (docs/sdi.md §4.3): the sum of all large
    exposures may not exceed a multiple of NOF. The cap is a ``multiplier``
    parameter resolved from the control plane and is built DORMANT — it stays
    inactive until an operator confirms its value (``try_resolve`` returns None
    when unseeded, and a pending value emits an ADVISORY finding, not a breach)."""
    cap = regulatory_parameters.try_resolve(db, bank, "aggregate_large_exposure_cap", as_of=as_of)
    if cap is None or cap.value is None:
        return
    aggregate = sum((entity.total for entity in large_exposures), _ZERO)
    cap_ghs = nof * cap.value
    if aggregate > cap_ghs:
        severity = "INFO" if cap.is_pending else "WARNING"
        pending = " (cap value pending BoG confirmation — advisory)" if cap.is_pending else ""
        findings.append(
            _finding(
                "le.aggregate_large_exposure_cap_exceeded",
                severity,
                f"Aggregate large exposures of {aggregate} GHS exceed the "
                f"{cap.value}× Net Own Funds cap ({cap_ghs} GHS){pending} "
                f"({cap.source_citation}).",
            )
        )


def _append_related_party_finding(  # noqa: PLR0913 - findings accumulator + context
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    entities: list[_Entity],
    nof: Decimal,
    findings: list[dict[str, str]],
) -> None:
    """Aggregate related-party lending exposure vs the limit (docs/sdi.md §4.3).

    Links the ``RelatedParty`` register (master data) to the exposure book by
    connected-group / counterparty name, sums the exposure to related parties,
    and compares it to ``related_party_limit_pct`` of NOF. Built DORMANT — the
    limit value is pending BoG confirmation, so a breach emits an ADVISORY
    finding; ``try_resolve`` returning None (unseeded) skips the check entirely."""
    limit = regulatory_parameters.try_resolve(db, bank, "related_party_limit_pct", as_of=as_of)
    if limit is None or limit.value is None:
        return
    related_names = _related_party_names(db, ctx, bank)
    if not related_names:
        return
    related_exposure = sum(
        (entity.total for entity in entities if _normalized_name(entity.name) in related_names),
        _ZERO,
    )
    if related_exposure <= _ZERO:
        return
    pct = _pct_of(related_exposure, nof)
    if pct > limit.value:
        severity = "INFO" if limit.is_pending else "WARNING"
        pending = " (limit value pending BoG confirmation — advisory)" if limit.is_pending else ""
        findings.append(
            _finding(
                "le.related_party_limit_exceeded",
                severity,
                f"Aggregate related-party lending exposure is {pct}% of Net Own Funds, "
                f"above the {limit.value}% limit{pending} ({limit.source_citation}).",
            )
        )


@dataclass(frozen=True)
class _OwnFunds:
    """The Net Own Funds denominator, and where the regime says it comes from.

    The two institution classes answer this from different instruments — an SDI
    from the governed Act 930 s.29 calculation, a bank from the Tier 1 total of
    its baseline capital run — so the resolution is lifted out of the assembly
    below rather than inlined as a branch in the middle of it.

    ``tier1`` is bank-only and stays ``None`` for an SDI, which is what makes the
    Tier 1 totals row conditional without a second class test.
    """

    nof: Decimal
    tier1: Decimal | None
    capital_run: RegulatoryRun | None
    sdi_summary: sdi_capital.SdiCapitalSummary | None


def _resolve_own_funds(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    *,
    is_sdi: bool,
) -> _OwnFunds:
    """Net Own Funds for the exposure percentages, or a 409 naming why not."""
    if is_sdi:
        # A code-default RWA taxonomy or composition is useful as a live
        # diagnostic but cannot support a filed exposure return. The same
        # governed scope gate used by the official capital mint applies here.
        sdi_capital.assert_official_rwa_scope_governed(db, bank, period.period_end)
        summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, period.period_end)
        nof = summary.net_own_funds_ghs
        if not summary.computable or nof <= _ZERO:
            raise _conflict_409(
                "sdi_nof_not_computable",
                "Net Own Funds from the governed Act 930 s.29 SDI capital calculation is "
                "not computable or not positive; exposures cannot be expressed as a "
                "percentage of Net Own Funds.",
            )
        return _OwnFunds(nof=nof, tier1=None, capital_run=None, sdi_summary=summary)

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
    return _OwnFunds(nof=tier1, tier1=tier1, capital_run=capital_run, sdi_summary=None)


def generate_large_exposures(  # noqa: PLR0914 - one linear template assembly
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    is_sdi = definition.generator == "sdi_large_exposures"
    own_funds = _resolve_own_funds(db, ctx, bank, period, is_sdi=is_sdi)
    nof = own_funds.nof
    tier1 = own_funds.tier1
    capital_run = own_funds.capital_run
    sdi_summary = own_funds.sdi_summary

    rows = _load_canonical_rows(db, ctx, bank, period.period_end, _LE_POSITION_TYPES)
    unvalidated_rows, unvalidated_finding = _unvalidated_disclosure(
        db, ctx, bank, period.period_end
    )
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

    # Enforce the class-aware single-obligor + large-exposure limits (§4.3), and
    # the aggregate large-exposure cap (dormant until its value is confirmed).
    _append_exposure_limit_findings(db, bank, period.period_end, non_exempt, nof, findings)
    _append_aggregate_exposure_finding(db, bank, period.period_end, template_1, nof, findings)
    _append_related_party_finding(db, ctx, bank, period.period_end, non_exempt, nof, findings)

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
        snapshot_row(
            "nof_ghs",
            "Net Own Funds (Act 930 s.29 calculation)"
            if is_sdi
            else "Net Own Funds (Tier 1 proxy)",
            nof,
            unit="ghs",
        ),
        snapshot_row("large_exposure_count", "Exposures ≥10% of NOF", len(template_1)),
        snapshot_row("largest_exposure_pct_nof", "Largest exposure (% NOF)", largest_pct),
    ]
    if not is_sdi:
        # ``tier1`` is always bound on the bank path; the assert states the
        # invariant for the reader and the type checker in one line.
        assert tier1 is not None
        totals.insert(0, snapshot_row("tier1_ghs", "Tier 1 Capital", tier1, unit="ghs"))
    metadata = {
        "nof_basis": (
            "Net Own Funds from the governed Act 930 s.29 SDI capital calculation."
            if is_sdi
            else "Net Own Funds proxied by Tier 1 capital (CET1 + AT1) from the succeeded "
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
        "single_exposure_limit_pct_nof": str(
            regulatory_parameters.resolve(
                db, bank, "single_obligor_limit_pct", as_of=period.period_end
            ).value
        ),
        "threshold_ghs": str(threshold),
        "canonical_position_count": len(rows),
        # D-4: the rows the exposure tables refused to read, carried in the
        # immutable snapshot so the export path — which never recomputes —
        # states the same exclusion the approver cleared.
        "unvalidated_rows": unvalidated_rows,
        "generation_findings": [*findings, *unvalidated_finding],
    }
    if is_sdi and sdi_summary is not None:
        metadata.update(
            {
                "sdi_capital_as_of": sdi_summary.as_of.isoformat(),
                "sdi_total_rwa_ghs": str(sdi_summary.total_rwa_ghs),
                "sdi_car_pct": str(sdi_summary.car_pct)
                if sdi_summary.car_pct is not None
                else None,
                "sdi_car_min_pct": str(sdi_summary.car_min_pct),
                "sdi_rwa_scope": sdi_summary.rwa_scope_note,
                "sdi_rwa_taxonomy_source": sdi_summary.bucket_map_source,
                "sdi_rwa_composition_source": sdi_summary.composition_source,
                "sdi_pending_parameters": sdi_summary.pending_parameters,
            }
        )
    elif capital_run is not None:
        metadata["capital_baseline_run_id"] = str(capital_run.id)
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[source_run_entry(capital_run)] if capital_run is not None else [],
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
        if row.counterparty_resident is False and (row.counterparty_rating or "").startswith("AAA"):
            return row.balance_ghs  # (c) AAA non-resident FI placements
        if row.counterparty_resident is True:
            return row.balance_ghs  # (g) claims on other domestic banks
        return _ZERO
    if (
        kind == "SECURITY_HOLDING"
        and _unencumbered(row)
        and _within_one_year(row.contractual_maturity, as_of)
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
    """Ratio floor per code, resolved for the tenant's institution class
    (docs/sdi.md §4.1): the Board register row active for that class, else the
    class default from the regulatory-parameter control plane (bank 80/100/50/70/
    30/50/60/80 vs SDI 90/100/50/60/30/40/60/70), else the directive's published
    bank minimum as a defensive fallback. A savings-&-loans tenant therefore
    binds against the SDI floors, not the bank ones."""
    klass = _resolve_institution_class(db, bank)
    resolved: dict[str, tuple[Decimal, str]] = {}
    for code, bank_floor in _TABLE1_BANK_MINIMUM_PCT.items():
        param = regulatory_parameters.try_resolve(db, bank, code, as_of=as_of)
        # Normalise the control-plane value so its representation matches the
        # published floor byte-for-byte: a Numeric(18,6) round-trips 80 as
        # Decimal("80.000000") on Postgres, which would silently change the
        # generated return content + its content_digest (audit M1).
        normalized = param.normalized_value if param is not None else None
        if normalized is not None:
            default = normalized
        elif klass == "bank":
            # The directive's published bank floor is the robustness fallback —
            # byte-identical to HEAD when the control plane is unseeded.
            default = bank_floor
        else:
            # The binding SDI measure must NEVER silently fall back to the bank
            # floors when its own floors are unconfigured (audit M4, fail-loud).
            raise _conflict_409(
                "sdi_liquidity_floor_unseeded",
                f"The SDI liquidity floor '{code}' is not configured in the "
                "regulatory-parameter control plane for this institution's "
                "jurisdiction. Seed the SDI-class Table-1 floors before generating "
                "the liquidity return.",
            )
        resolved[code] = (default, "regulatory_default")
    jurisdiction = jurisdictions.jurisdiction_code(bank)
    for row in get_active_params(
        db, ctx.organization_id, jurisdiction, ParamLiquidityThreshold, as_of
    ):
        if row.institution_class == klass and row.threshold_code in resolved:
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
            liabilities_by_ccy[row.currency] = liabilities_by_ccy.get(row.currency, _ZERO) + amount

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
                str(_pct_of(mismatch, total_liabilities)) if total_liabilities > _ZERO else "0"
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

    capped_inflow = {key: min(inflow[key], (outflow[key] * _LCR_INFLOW_CAP)) for key in column_keys}
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


# --- Concentration of funding: Tables 5, 7 and 8 (rebuilt 2026-08-07) ------
#
# Table 5's selection rule is the directive's, not "Top N by size": every
# connected counterparty (Large Exposures Directive grouping, footnote 4)
# providing funding of MORE THAN 1% of Total Assets, plus Top-20/Top-100
# depositor totals with the ¶23 netting rule (deposits pledged to secure a
# facility deducted from both the numerator and the total-deposit
# denominator). Table 7 uses ¶31's five horizons; Table 8 uses its own nine
# printed buckets — the ¶31-vs-template discrepancy is recorded, and we build
# to the templates because those are what gets filed.
_SIGNIFICANT_FUNDING_FRACTION = Decimal("0.01")
_TOP20 = 20
_TOP100 = 100
_FIVE_YEARS_DAYS = 1825
_FI_COUNTERPARTY_TYPES = ("BANK_OECD", "BANK_NON_OECD", "NBFI")
_GOV_COUNTERPARTY_TYPES = ("SOVEREIGN", "GOVERNMENT_ENTITY", "CENTRAL_BANK")
_NEGOTIABLE_PAPER = "negotiable_paper"
_TABLE7_BUCKETS: tuple[tuple[str, str, int | None], ...] = (
    ("m_lt1_ghs", "<1", 30),
    ("m1_3_ghs", "1 - 3", 91),
    ("m3_6_ghs", "3 - 6", 182),
    ("m6_12_ghs", "6 - 12", 365),
    ("m_gt12_ghs", ">12", None),
)
_TABLE8_BUCKETS: tuple[tuple[str, str, int | None], ...] = (
    ("next_day_ghs", "Next day", 1),
    ("d2_7_ghs", "2 to 7 days", 7),
    ("d8_1m_ghs", "8 days to 1 month", 30),
    ("m1_2_ghs", "1 to 2 months", 61),
    ("m2_3_ghs", "2 to 3 months", 91),
    ("m3_6_ghs", "3 to 6 months", 182),
    ("m6_12_ghs", "6 to 12 months", 365),
    ("m_gt12_ghs", "> 12 months", None),
)


@dataclass
class _FundingEntity:
    """One funding provider: a single counterparty or a connected group."""

    key: str
    name: str
    related: bool
    is_financial: bool = False
    is_government: bool = False
    funding: Decimal = _ZERO
    deposits: Decimal = _ZERO
    pledged_deposits: Decimal = _ZERO
    rows: list[_CanonicalRow] = field(default_factory=list)


def _normalized_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _related_party_names(db: Session, ctx: TenantContext, bank: Bank) -> frozenset[str]:
    names = db.scalars(
        select(RelatedParty.full_name).where(
            RelatedParty.organization_id == ctx.organization_id,
            RelatedParty.bank_id == bank.id,
        )
    ).all()
    return frozenset(_normalized_name(name) for name in names)


def _funding_entities(
    rows: list[_CanonicalRow], related_names: frozenset[str]
) -> tuple[dict[str, _FundingEntity], Decimal, list[dict[str, str]]]:
    """Aggregate liability rows into connected funding entities.

    Related/intragroup = the source's own flag on the counterparty OR a
    normalized-name match against the institution's related-party register
    (the W4 governance build) — both documented, neither inferred.
    """
    entities: dict[str, _FundingEntity] = {}
    unattributed = _ZERO
    for row in rows:
        target = _table2_row_for(row)
        if target is None or target[0] not in ("6", "7", "8", "9"):
            continue
        amount = target[1]
        if row.counterparty_id is None:
            unattributed += amount
            continue
        key = row.counterparty_group or f"cp:{row.counterparty_id}"
        name = (
            f"Group: {row.counterparty_group}"
            if row.counterparty_group
            else (row.counterparty_name or str(row.counterparty_id))
        )
        entity = entities.setdefault(
            _normalized_name(key),
            _FundingEntity(key=key, name=name, related=False),
        )
        entity.funding += amount
        entity.rows.append(row)
        if row.counterparty_related_hint or (
            row.counterparty_name is not None
            and _normalized_name(row.counterparty_name) in related_names
        ):
            entity.related = True
        if row.counterparty_type in _FI_COUNTERPARTY_TYPES:
            entity.is_financial = True
        if row.counterparty_type in _GOV_COUNTERPARTY_TYPES:
            entity.is_government = True
        if row.position_type == "DEPOSIT":
            entity.deposits += amount
            if row.pledged_as_collateral is True:
                entity.pledged_deposits += amount

    findings: list[dict[str, str]] = []
    if unattributed > _ZERO:
        findings.append(
            _finding(
                "lmt.unattributed_funding",
                "INFO",
                f"Funding of {unattributed} GHS carries no counterparty link; it is "
                "included in totals but cannot be attributed to a provider for the "
                "concentration tables.",
            )
        )
    return entities, unattributed, findings


def _netted_pct(top: list[_FundingEntity], total_deposits: Decimal) -> Decimal:
    """¶23: pledged deposits leave both the numerator and the denominator."""
    top_deposits = sum((entity.deposits for entity in top), _ZERO)
    pledged = sum((entity.pledged_deposits for entity in top), _ZERO)
    denominator = total_deposits - pledged
    if denominator <= _ZERO:
        return _ZERO
    return _pct_of(top_deposits - pledged, denominator)


def _funding_concentration_section(  # noqa: PLR0914 - one linear table assembly
    entities: dict[str, _FundingEntity],
    total_assets: Decimal,
    total_liabilities: Decimal,
    total_deposits: Decimal,
) -> tuple[dict[str, Any] | None, dict[str, Decimal], list[_FundingEntity]]:
    """LMTD Table 5 — funding from significant counterparties (> 1% of assets)."""
    if not entities:
        return None, {}, []
    ranked = sorted(entities.values(), key=lambda e: (-e.funding, e.name))
    threshold = total_assets * _SIGNIFICANT_FUNDING_FRACTION
    significant = [entity for entity in ranked if entity.funding > threshold]
    by_deposits = sorted(entities.values(), key=lambda e: (-e.deposits, e.name))
    top20 = [entity for entity in by_deposits[:_TOP20] if entity.deposits > _ZERO]
    top100 = [entity for entity in by_deposits[:_TOP100] if entity.deposits > _ZERO]
    top20_total = sum((entity.deposits for entity in top20), _ZERO)
    top100_total = sum((entity.deposits for entity in top100), _ZERO)

    rows = [
        snapshot_row(
            str(index),
            entity.name,
            entity.funding,
            pct_total_liabilities=(
                str(_pct_of(entity.funding, total_liabilities))
                if total_liabilities > _ZERO
                else "0"
            ),
            related_party="Yes" if entity.related else "No",
        )
        for index, entity in enumerate(significant, start=1)
    ]
    significant_total = sum((entity.funding for entity in significant), _ZERO)
    rows.append(snapshot_row("total", "Total", significant_total, related_party="", unit="ghs"))
    rows.append(
        snapshot_row(
            "top20_total",
            "Total for Top 20 counterparties (depositors)",
            top20_total,
            unit="ghs",
        )
    )
    rows.append(
        snapshot_row(
            "top100_total",
            "Total for Top 100 counterparties (depositors)",
            top100_total,
            unit="ghs",
        )
    )
    top20_pct = _netted_pct(top20, total_deposits)
    top100_pct = _netted_pct(top100, total_deposits)
    rows.append(
        snapshot_row(
            "top20_pct_of_deposits",
            "Total 20 deposits as a percentage of total deposits",
            top20_pct,
            unit="pct",
        )
    )
    rows.append(
        snapshot_row(
            "top100_pct_of_deposits",
            "Total 100 deposits as a percentage of total deposits",
            top100_pct,
            unit="pct",
        )
    )
    section = snapshot_section(
        "funding_concentration",
        "Funding Liabilities Sourced from Significant Counterparties",
        rows,
        snapshot_total(
            "significant_funding_total_ghs",
            "Total funding from significant counterparties (> 1% of Total Assets)",
            significant_total,
        ),
    )
    metrics = {
        "top20_total": top20_total,
        "top100_total": top100_total,
        "top20_pct": top20_pct,
        "top100_pct": top100_pct,
    }
    return section, metrics, significant


def _bucket_for(
    row: _CanonicalRow, as_of: date, buckets: tuple[tuple[str, str, int | None], ...]
) -> str:
    """Concentration-table bucketing with documented undated rules.

    Demand-natured deposits (current/call/savings) and cash are callable on
    demand → the shortest bucket; any other undated position reports in the
    longest bucket — a maturity is never fabricated.
    """
    maturity = row.contractual_maturity
    if maturity is None:
        account_type = (row.deposit_account_type or "").upper()
        on_demand = row.position_type == "CASH" or (
            row.position_type == "DEPOSIT" and account_type in _SHORT_TERM_BY_NATURE
        )
        return buckets[0][0] if on_demand else buckets[-1][0]
    days = (maturity - as_of).days
    for code, _, upper in buckets:
        if upper is None or days <= upper:
            return code
    return buckets[-1][0]


def _bucket_vector(
    rows: list[_CanonicalRow],
    as_of: date,
    buckets: tuple[tuple[str, str, int | None], ...],
    *,
    amount_of: Any = None,
) -> dict[str, Decimal]:
    vector: dict[str, Decimal] = {code: _ZERO for code, _, _ in buckets}
    for row in rows:
        amount = amount_of(row) if amount_of is not None else row.balance_ghs
        if amount is None:
            continue
        vector[_bucket_for(row, as_of, buckets)] += amount
    return vector


def _grid_bucket_row(
    code: str,
    label: str,
    vector: dict[str, Decimal],
    buckets: tuple[tuple[str, str, int | None], ...],
) -> dict[str, Any]:
    total = sum(vector.values(), _ZERO)
    return snapshot_row(code, label, total, **{key: str(vector[key]) for key, _, _ in buckets})


def _table7_section(
    rows: list[_CanonicalRow],
    entities: dict[str, _FundingEntity],
    significant: list[_FundingEntity],
    as_of: date,
) -> dict[str, Any]:
    """LMTD Table 7 — time buckets of maturity of exposures (five horizons)."""
    by_deposits = sorted(entities.values(), key=lambda e: (-e.deposits, e.name))
    top20 = [entity for entity in by_deposits[:_TOP20] if entity.deposits > _ZERO]
    section_rows: list[dict[str, Any]] = []

    top20_deposit_rows = [
        row for entity in top20 for row in entity.rows if row.position_type == "DEPOSIT"
    ]
    section_rows.append(
        _grid_bucket_row(
            "A",
            "A. Top 20 Depositors",
            _bucket_vector(top20_deposit_rows, as_of, _TABLE7_BUCKETS),
            _TABLE7_BUCKETS,
        )
    )

    b_total = {code: _ZERO for code, _, _ in _TABLE7_BUCKETS}
    for index, entity in enumerate(significant, start=1):
        vector = _bucket_vector(entity.rows, as_of, _TABLE7_BUCKETS)
        for code, _, _unused in _TABLE7_BUCKETS:
            b_total[code] += vector[code]
        section_rows.append(_grid_bucket_row(f"B{index}", entity.name, vector, _TABLE7_BUCKETS))
    section_rows.append(
        _grid_bucket_row(
            "B_total",
            "B. Funding from Significant Counterparties — Total",
            b_total,
            _TABLE7_BUCKETS,
        )
    )

    significant_ccys = _significant_currencies(rows)
    c_total = {code: _ZERO for code, _, _ in _TABLE7_BUCKETS}
    d_total = {code: _ZERO for code, _, _ in _TABLE7_BUCKETS}
    for currency in significant_ccys:
        ccy_assets = [r for r in rows if r.currency == currency and _is_t2_asset(r)]
        ccy_liabilities = [r for r in rows if r.currency == currency and _is_t2_liability(r)]
        asset_vector = _bucket_vector(ccy_assets, as_of, _TABLE7_BUCKETS)
        liability_vector = _bucket_vector(ccy_liabilities, as_of, _TABLE7_BUCKETS)
        for code, _, _unused in _TABLE7_BUCKETS:
            c_total[code] += asset_vector[code]
            d_total[code] += liability_vector[code]
        section_rows.append(
            _grid_bucket_row(f"C_{currency}", f"Assets — {currency}", asset_vector, _TABLE7_BUCKETS)
        )
        section_rows.append(
            _grid_bucket_row(
                f"D_{currency}", f"Liabilities — {currency}", liability_vector, _TABLE7_BUCKETS
            )
        )
    section_rows.append(
        _grid_bucket_row(
            "C_total", "C. Assets by Significant Currency — Total", c_total, _TABLE7_BUCKETS
        )
    )
    section_rows.append(
        _grid_bucket_row(
            "D_total", "D. Liabilities by Significant Currency — Total", d_total, _TABLE7_BUCKETS
        )
    )
    return snapshot_section(
        "maturity_of_exposures",
        "Time Buckets of Maturity of Exposures",
        section_rows,
    )


def _table8_section(
    rows: list[_CanonicalRow],
    entities: dict[str, _FundingEntity],
    as_of: date,
) -> dict[str, Any]:
    """LMTD Table 8 — concentration of deposit funding (nine printed buckets)."""
    by_deposits = sorted(entities.values(), key=lambda e: (-e.deposits, e.name))
    top20_dep = [e for e in by_deposits[:_TOP20] if e.deposits > _ZERO]
    financial = sorted(
        (e for e in entities.values() if e.is_financial), key=lambda e: (-e.funding, e.name)
    )[:_TOP20]
    government = sorted(
        (e for e in entities.values() if e.is_government), key=lambda e: (-e.funding, e.name)
    )[:_TOP20]
    associates_rows = [row for entity in entities.values() if entity.related for row in entity.rows]
    top20_dep_rows = [row for e in top20_dep for row in e.rows if row.position_type == "DEPOSIT"]
    financial_rows = [row for e in financial for row in e.rows]
    government_rows = [row for e in government for row in e.rows]
    paper_rows = [
        row for row in rows if _is_t2_liability(row) and row.funding_instrument == _NEGOTIABLE_PAPER
    ]
    paper_short = [
        row
        for row in paper_rows
        if row.contractual_maturity is not None
        and (row.contractual_maturity - as_of).days <= _ONE_YEAR_DAYS
    ]
    paper_long = [
        row
        for row in paper_rows
        if row.contractual_maturity is not None
        and (row.contractual_maturity - as_of).days > _FIVE_YEARS_DAYS
    ]

    blocks: tuple[tuple[str, str, list[_CanonicalRow]], ...] = (
        (
            "associates",
            "Funding from associates of the reporting financial institution",
            associates_rows,
        ),
        ("top20_depositors", "Twenty largest depositors", top20_dep_rows),
        (
            "top20_financial",
            "Twenty largest financial institutions funding balances",
            financial_rows,
        ),
        (
            "top20_government",
            "Twenty largest government and parastatals funding balances",
            government_rows,
        ),
        ("negotiable_paper", "Negotiable paper funding instruments", paper_rows),
        (
            "negotiable_paper_lte_12m",
            "of which: issued for a period not exceeding twelve months",
            paper_short,
        ),
        (
            "negotiable_paper_gt_5y",
            "of which: issued for a period exceeding five years",
            paper_long,
        ),
    )
    section_rows = [
        _grid_bucket_row(
            code, label, _bucket_vector(block, as_of, _TABLE8_BUCKETS), _TABLE8_BUCKETS
        )
        for code, label, block in blocks
    ]
    return snapshot_section(
        "deposit_funding_concentration",
        "Concentration of Deposit Funding",
        section_rows,
    )


# --- Tables 3, 4, 9 (full) and 10 (built 2026-08-07) ------------------------

_HUNDRED_PCT = Decimal("100")
_TABLE10_CLASSES: tuple[tuple[str, str], ...] = (
    ("loans_advances", "Loans and advances"),
    ("equity", "Equity instruments"),
    ("debt_government", "Debt securities — Government issued"),
    ("debt_financial", "Debt securities — Issued by financial institutions"),
    ("debt_nonfinancial", "Debt securities — Issued by non-financial corporations"),
    ("other", "Other collateral received"),
)


def _haircut_schedule(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[tuple[str, Decimal]]:
    """Active liquidity-value rows, longest asset-class prefix first."""
    jurisdiction = jurisdictions.jurisdiction_code(bank)
    rows = get_active_params(db, ctx.organization_id, jurisdiction, ParamLiquidityHaircut, as_of)
    return sorted(
        ((row.asset_class.upper(), Decimal(str(row.haircut_pct))) for row in rows),
        key=lambda item: -len(item[0]),
    )


def _haircut_for(
    regulatory_category: str | None, schedule: list[tuple[str, Decimal]]
) -> Decimal | None:
    """Longest-prefix match against the schedule; None = no calibrated class."""
    category = (regulatory_category or "").upper()
    for asset_class, pct in schedule:
        if category.startswith(asset_class):
            return pct
    return None


def _instrument_label(row: _CanonicalRow) -> str:
    return row.product_name or row.issuer or row.position_type.replace("_", " ").title()


def _table3_section(rows: list[_CanonicalRow]) -> dict[str, Any] | None:
    """LMTD Table 3 — investments/items with no contractual maturity.

    Population: non-contractual positions EXCLUDING deposits (whose undated
    treatment lives in Table 2 rows 6/7 and the concentration tables),
    itemized by instrument label and aggregated.
    """
    amounts: dict[str, Decimal] = {}
    for row in rows:
        if row.contractual_maturity is not None or row.position_type == "DEPOSIT":
            continue
        target = _table2_row_for(row)
        if target is None:
            continue
        label = _instrument_label(row)
        amounts[label] = amounts.get(label, _ZERO) + target[1]
    if not amounts:
        return None
    section_rows = [
        snapshot_row(str(index), label, amount)
        for index, (label, amount) in enumerate(
            sorted(amounts.items(), key=lambda kv: (-kv[1], kv[0])), start=1
        )
    ]
    total = sum(amounts.values(), _ZERO)
    return snapshot_section(
        "items_no_contractual_maturity",
        "Investments and Items with No Contractual Maturity",
        section_rows,
        snapshot_total(
            "no_contractual_maturity_total_ghs", "Total", total, equals_sum_of_rows=True
        ),
    )


def _table4_section(rows: list[_CanonicalRow]) -> dict[str, Any] | None:
    """LMTD Table 4 — customer collateral which can be re-hypothecated."""
    entries: dict[str, tuple[Decimal, Decimal]] = {}
    for row in rows:
        collateral = row.collateral
        if not collateral:
            continue
        rehypothecable = str(collateral.get("collateral_rehypothecable", "")).strip().lower()
        if rehypothecable not in ("true", "yes", "y", "1"):
            continue
        instrument = str(collateral.get("collateral_instrument") or _instrument_label(row))
        received = _dec_or_none(collateral.get("collateral_received_ghs")) or _ZERO
        hypothecated = _dec_or_none(collateral.get("collateral_rehypothecated_ghs")) or _ZERO
        prior = entries.get(instrument, (_ZERO, _ZERO))
        entries[instrument] = (prior[0] + received, prior[1] + hypothecated)
    if not entries:
        return None
    section_rows = []
    total_a = total_b = _ZERO
    for index, (instrument, (received, hypothecated)) in enumerate(
        sorted(entries.items(), key=lambda kv: (-kv[1][0], kv[0])), start=1
    ):
        total_a += received
        total_b += hypothecated
        section_rows.append(
            snapshot_row(
                str(index),
                instrument,
                received - hypothecated,
                total_amounts_ghs=str(received),
                hypothecated_ghs=str(hypothecated),
            )
        )
    return snapshot_section(
        "collateral_rehypothecation",
        "Customer Collateral Received",
        section_rows,
        snapshot_total(
            "collateral_available_total_ghs",
            "Total available for re-hypothecation (A-B)",
            total_a - total_b,
            equals_sum_of_rows=True,
        ),
    )


def _is_bog_eligible_security(row: _CanonicalRow) -> bool:
    """BoG standing-facility eligibility: the LMTD's own eligible-collateral
    family — sovereign/central-bank instruments by regulatory category."""
    return _is_sovereign_security(row)


def _table9_row(
    index: int, row: _CanonicalRow, schedule: list[tuple[str, Decimal]]
) -> dict[str, Any]:
    haircut = _haircut_for(row.regulatory_category, schedule)
    effective = haircut if haircut is not None else _ZERO
    monetized = row.balance_ghs * (_HUNDRED_PCT - effective) / _HUNDRED_PCT
    return snapshot_row(
        str(index),
        _instrument_label(row),
        row.balance_ghs,
        asset_type=row.regulatory_category or row.position_type,
        location=row.asset_location or "",
        haircut_pct=str(effective),
        haircut_source="schedule" if haircut is not None else "unset",
        monetized_value_ghs=str(monetized),
    )


def _table9_section(
    rows: list[_CanonicalRow], schedule: list[tuple[str, Decimal]]
) -> tuple[dict[str, Any] | None, Decimal]:
    """LMTD Table 9 — available unencumbered assets, all seven columns.

    Section A: unencumbered securities marketable in secondary markets that
    are NOT in the BoG standing-facility family; Section B: the BoG-eligible
    family (sovereign/central-bank instruments); Section C: A∪B by
    significant currency, where significance uses ¶36's denominator — 5% of
    total available unencumbered collateral, not total liabilities.
    """
    unencumbered = [
        row for row in rows if row.position_type == "SECURITY_HOLDING" and _unencumbered(row)
    ]
    if not unencumbered:
        return None, _ZERO
    section_b = [row for row in unencumbered if _is_bog_eligible_security(row)]
    section_a = [row for row in unencumbered if not _is_bog_eligible_security(row)]

    section_rows: list[dict[str, Any]] = []
    total = _ZERO
    for block_code, block_label, block in (
        ("A", "A. Marketable as collateral in secondary market", section_a),
        ("B", "B. Eligible for BOG standing facilities", section_b),
    ):
        block_total = _ZERO
        for index, row in enumerate(
            sorted(block, key=lambda r: (-r.balance_ghs, _instrument_label(r))), start=1
        ):
            entry = _table9_row(index, row, schedule)
            entry["code"] = f"{block_code}{index}"
            section_rows.append(entry)
            block_total += row.balance_ghs
        section_rows.append(
            snapshot_row(f"{block_code}_total", f"{block_label} — Total", block_total)
        )
        total += block_total

    # Section C — by significant currency (¶36 denominator).
    by_currency: dict[str, Decimal] = {}
    monetized_by_currency: dict[str, Decimal] = {}
    for row in unencumbered:
        haircut = _haircut_for(row.regulatory_category, schedule) or _ZERO
        by_currency[row.currency] = by_currency.get(row.currency, _ZERO) + row.balance_ghs
        monetized_by_currency[row.currency] = (
            monetized_by_currency.get(row.currency, _ZERO)
            + row.balance_ghs * (_HUNDRED_PCT - haircut) / _HUNDRED_PCT
        )
    for currency, amount in sorted(by_currency.items(), key=lambda kv: (-kv[1], kv[0])):
        if total > _ZERO and amount / total >= _SIGNIFICANT_CURRENCY_THRESHOLD:
            section_rows.append(
                snapshot_row(
                    f"C_{currency}",
                    f"C. By significant currency — {currency}",
                    amount,
                    monetized_value_ghs=str(monetized_by_currency[currency]),
                )
            )

    section = snapshot_section(
        "unencumbered_assets",
        "Available Unencumbered Assets",
        section_rows,
        snapshot_total(
            "unencumbered_assets_total_ghs",
            "Total available unencumbered assets",
            total,
        ),
    )
    return section, total


def _table10_section(rows: list[_CanonicalRow]) -> dict[str, Any] | None:
    """LMTD Table 10 — collateral received by the reporting institution."""
    classes: dict[str, dict[str, Decimal]] = {
        code: {"total": _ZERO, "group": _ZERO, "bog": _ZERO, "unavailable": _ZERO}
        for code, _ in _TABLE10_CLASSES
    }
    own_debt = {"total": _ZERO, "group": _ZERO, "bog": _ZERO, "unavailable": _ZERO}
    seen = False
    for row in rows:
        collateral = row.collateral
        if not collateral:
            continue
        received = _dec_or_none(collateral.get("collateral_received_ghs"))
        unavailable = _dec_or_none(collateral.get("collateral_unavailable_ghs"))
        if received is not None or unavailable is not None:
            seen = True
            asset_class = str(collateral.get("collateral_asset_class") or "other").lower()
            bucket = classes.get(asset_class, classes["other"])
            amount = received or _ZERO
            bucket["total"] += amount
            bucket["unavailable"] += unavailable or _ZERO
            if str(collateral.get("collateral_group_issued", "")).strip().lower() in (
                "true",
                "yes",
                "y",
                "1",
            ):
                bucket["group"] += amount
            if str(collateral.get("collateral_bog_eligible", "")).strip().lower() in (
                "true",
                "yes",
                "y",
                "1",
            ):
                bucket["bog"] += amount
        own_available = _dec_or_none(collateral.get("own_debt_available_ghs"))
        own_unavailable = _dec_or_none(collateral.get("own_debt_unavailable_ghs"))
        if own_available is not None or own_unavailable is not None:
            seen = True
            own_debt["total"] += own_available or _ZERO
            own_debt["unavailable"] += own_unavailable or _ZERO
    if not seen:
        return None

    def _class_row(code: str, label: str, bucket: dict[str, Decimal]) -> dict[str, Any]:
        return snapshot_row(
            code,
            label,
            bucket["total"],
            group_issued_ghs=str(bucket["group"]),
            bog_eligible_ghs=str(bucket["bog"]),
            unavailable_ghs=str(bucket["unavailable"]),
        )

    section_rows = [_class_row(code, label, classes[code]) for code, label in _TABLE10_CLASSES]
    section_rows.append(_class_row("own_debt", "Own debt securities issued", own_debt))
    grand = {
        key: sum((classes[code][key] for code, _ in _TABLE10_CLASSES), _ZERO) + own_debt[key]
        for key in ("total", "group", "bog", "unavailable")
    }
    section_rows.append(
        _class_row(
            "grand_total",
            "Total Assets, Collateral Received and Own Debt Securities Issued",
            grand,
        )
    )
    return snapshot_section(
        "collateral_received",
        "Collateral Received by the Reporting Financial Institution",
        section_rows,
    )


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
        "Funding concentration (Tables 5/7/8): significant counterparty = "
        "connected group (Large Exposures grouping) providing funding of more "
        "than 1% of Total Assets — the directive's population, not Top-N. "
        "Top-20/Top-100 percentages apply the para-23 netting rule: deposits "
        "pledged to secure a facility leave both numerator and denominator. "
        "Related/intragroup = the source's own counterparty flag or a name "
        "match against the institution's related-party register — never "
        "inferred. Table 7 uses para-31's five horizons; Table 8 uses its nine "
        "printed buckets (the para-31 discrepancy is recorded as a directive "
        "question). Undated demand-natured funding reports in the shortest "
        "bucket, other undated in the longest — maturities are never invented. "
        "Table 8 negotiable paper fills from the documented "
        "attributes['funding_instrument'] = 'negotiable_paper' convention."
    ),
    (
        "Unencumbered assets (Table 9): itemized unencumbered securities across "
        "all seven printed columns and three sections — A secondary-market "
        "marketable (non-sovereign), B BoG standing-facility eligible (the "
        "directive's eligible-collateral family), C by significant currency "
        "using para-36's denominator (5% of total available unencumbered "
        "collateral). Haircuts and monetized values resolve from the "
        "institution's LRMD para-60-63 liquidity-value schedule "
        "(ParamLiquidityHaircut, longest-prefix match on regulatory category); "
        "an uncalibrated class reports haircut_source=unset with a zero "
        "haircut, never an invented number."
    ),
    (
        "Collateral received (Tables 4 and 10): filled from the documented "
        "collateral_* / own_debt_* position-attribute conventions "
        "(docs/API_INTEGRATION.md section 3.4) — instrument, asset class, fair "
        "value available for encumbrance, group-issued and BoG-eligible flags, "
        "re-hypothecation amounts, and nominal not available. Sections render "
        "only when a source supplies the data; empty grids are never "
        "fabricated."
    ),
    (
        "No-contractual-maturity items (Table 3): non-contractual positions "
        "excluding deposits (whose undated treatment lives in Table 2 rows 6-7 "
        "and the concentration tables), itemized by instrument."
    ),
]


def _append_liquidity_reserve_check(  # noqa: PLR0913 - the LMT block's accumulators
    db: Session,
    bank: Bank,
    as_of: date,
    rows: list[_CanonicalRow],
    total_deposits: Decimal,
    sections: list[dict[str, Any]],
    totals: list[dict[str, Any]],
    findings: list[dict[str, str]],
) -> None:
    """The SDI primary/secondary liquidity-reserve check (NBFI Business Rules 2000
    r.11; docs/sdi.md §2.2, §4.1). Distinct from the LMTD Table-1 ratios: the
    PRIMARY reserve is cash + balances at the central bank, the SECONDARY reserve
    is eligible government / central-bank securities. Floors come from the
    regulatory-parameter control plane, keyed by institution class — they are
    seeded only for the SDI class, so ``try_resolve`` returns None for a bank and
    this check is skipped entirely (bank output byte-identical).

    Reading (both floors editable in the console if BoG's is different): the
    primary reserve must be ≥ ``primary_liquidity_reserve_pct`` of total deposits,
    and the cumulative (primary + secondary) reserve ≥
    ``secondary_liquidity_reserve_pct``. A shortfall raises a WARNING; a
    non-computable denominator is shown as such, never a false green."""
    primary_floor = regulatory_parameters.try_resolve(
        db, bank, "primary_liquidity_reserve_pct", as_of=as_of
    )
    secondary_floor = regulatory_parameters.try_resolve(
        db, bank, "secondary_liquidity_reserve_pct", as_of=as_of
    )
    if primary_floor is None or secondary_floor is None or primary_floor.value is None:
        return  # not an SDI-class institution — the reserve regime does not apply
    if secondary_floor.value is None:
        return

    primary = sum(
        (
            row.balance_ghs
            for row in rows
            if row.position_type == "CASH"
            and (row.counterparty_type is None or row.counterparty_type == "CENTRAL_BANK")
        ),
        _ZERO,
    )
    secondary = sum(
        (
            row.balance_ghs
            for row in rows
            if row.position_type == "SECURITY_HOLDING"
            and _is_sovereign_security(row)
            and _unencumbered(row)
        ),
        _ZERO,
    )
    if total_deposits <= _ZERO:
        findings.append(
            _finding(
                "lmt.liquidity_reserve_not_computable",
                "INFO",
                "The primary/secondary liquidity-reserve ratios are not computable: "
                "total deposit liabilities are zero.",
            )
        )
        return

    primary_pct = _pct_of(primary, total_deposits)
    cumulative_pct = _pct_of(primary + secondary, total_deposits)
    totals.append(
        snapshot_row(
            "primary_liquidity_reserve_pct", "Primary liquidity reserve (% deposits)", primary_pct
        )
    )
    totals.append(
        snapshot_row(
            "secondary_liquidity_reserve_pct",
            "Cumulative primary+secondary reserve (% deposits)",
            cumulative_pct,
        )
    )
    if primary_pct < primary_floor.value:
        findings.append(
            _finding(
                "lmt.primary_liquidity_reserve_below_minimum",
                "WARNING",
                f"Primary liquidity reserve is {primary_pct}% of total deposits, below the "
                f"{primary_floor.value}% floor ({primary_floor.source_citation}).",
            )
        )
    if cumulative_pct < secondary_floor.value:
        findings.append(
            _finding(
                "lmt.secondary_liquidity_reserve_below_minimum",
                "WARNING",
                f"Cumulative primary+secondary liquidity reserve is {cumulative_pct}% of total "
                f"deposits, below the {secondary_floor.value}% floor "
                f"({secondary_floor.source_citation}).",
            )
        )


def _build_lmt_tool_block(  # noqa: PLR0915 - the eleven tables assemble linearly
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    *,
    include_lcr_by_currency: bool = True,
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

        # SDI primary/secondary liquidity-reserve check (NBFI r.11) — SDI-class
        # only; skipped for a bank, so bank output is unchanged.
        _append_liquidity_reserve_check(
            db,
            bank,
            period.period_end,
            ladder_rows,
            current_inputs["total_deposits"],
            sections,
            totals,
            findings,
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

        # Tables 3 and 4.
        table3 = _table3_section(ladder_rows)
        if table3 is not None:
            sections.append(table3)
        table4 = _table4_section(ladder_rows)
        if table4 is not None:
            sections.append(table4)

        # Tables 5, 7, 8 — funding concentration on the directive's rules.
        related_names = _related_party_names(db, ctx, bank)
        entities, _unattributed, funding_findings = _funding_entities(ladder_rows, related_names)
        findings.extend(funding_findings)
        total_liabilities = sum(_liabilities_by_currency(ladder_rows).values(), _ZERO)
        concentration, metrics, significant = _funding_concentration_section(
            entities,
            current_inputs["total_assets"],
            total_liabilities,
            current_inputs["total_deposits"],
        )
        if concentration is not None:
            sections.append(concentration)
            totals.append(
                snapshot_row(
                    "total_deposits_ghs",
                    "Total deposit liabilities",
                    current_inputs["total_deposits"],
                    unit="ghs",
                )
            )
            totals.append(
                snapshot_row(
                    "top20_deposits_pct",
                    "Top-20 deposits as % of total deposits (net of pledged, para 23)",
                    metrics["top20_pct"],
                    unit="pct",
                )
            )
            totals.append(
                snapshot_row(
                    "top100_deposits_pct",
                    "Top-100 deposits as % of total deposits (net of pledged, para 23)",
                    metrics["top100_pct"],
                    unit="pct",
                )
            )

        # Table 6 — assets/liabilities by significant currency.
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

        # Tables 7 and 8 — maturity and deposit-funding concentration grids.
        if concentration is not None:
            sections.append(_table7_section(ladder_rows, entities, significant, period.period_end))
            sections.append(_table8_section(ladder_rows, entities, period.period_end))

        # Tables 9 and 10 — unencumbered assets and collateral received.
        schedule = _haircut_schedule(db, ctx, bank, period.period_end)
        table9, unencumbered_total = _table9_section(ladder_rows, schedule)
        if table9 is not None:
            sections.append(table9)
            totals.append(
                snapshot_row(
                    "unencumbered_assets_ghs",
                    "Total available unencumbered assets",
                    unencumbered_total,
                    unit="ghs",
                )
            )
        table10 = _table10_section(ladder_rows)
        if table10 is not None:
            sections.append(table10)

        if include_lcr_by_currency:
            # Table 11 — LCR by significant currency (banks only).
            base_currency = jurisdictions.base_currency(bank)
            sections.append(_table11_section(ladder_rows, period.period_end, base_currency))

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
    # D-4, as for the Large Exposures return above. The LCR subset comes from a
    # sealed liquidity run whose engine already excluded these rows; the tool
    # tables read the canonical book directly. One disclosure covers both,
    # because both are compiled from the same admitted book.
    unvalidated_rows, unvalidated_finding = _unvalidated_disclosure(
        db, ctx, bank, period.period_end
    )
    metadata = {
        **liquidity_snapshot_metadata(preview),
        "lmt_tool_notes": tools.notes,
        "unvalidated_rows": unvalidated_rows,
        "generation_findings": [*tools.findings, *unvalidated_finding],
    }
    runs = latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_LIQUIDITY)
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[source_run_entry(run) for _, run in sorted(runs.items())],
    )


def generate_sdi_lmt(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Published LMTD Tables 1-10 for an SDI, without bank-only LCR reporting."""
    tools = _build_lmt_tool_block(db, ctx, bank, period, include_lcr_by_currency=False)
    if not tools.sections:
        raise _conflict_409(
            "no_canonical_positions",
            "No accepted canonical positions exist for the reporting period. Ingest the "
            "SDI balance-sheet and deposit book before generating the LMTD return.",
        )
    unvalidated_rows, unvalidated_finding = _unvalidated_disclosure(
        db, ctx, bank, period.period_end
    )
    metadata = {
        "report_scope": "SDI LMTD Appendix Tables 1-10; Table 11 excluded as banks-only.",
        "lmt_tool_notes": tools.notes,
        "unvalidated_rows": unvalidated_rows,
        "generation_findings": [*tools.findings, *unvalidated_finding],
    }
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, tools.sections, tools.totals, metadata),
        source_runs=[],
    )


def generate_sdi_large_exposures(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Published Large Exposures templates using the governed SDI NOF basis."""
    return generate_large_exposures(db, ctx, bank, period, definition)


LE_GENERATORS = {
    "large_exposures": generate_large_exposures,
    "lmt": generate_lmt,
    "sdi_large_exposures": generate_sdi_large_exposures,
    "sdi_lmt": generate_sdi_lmt,
}

__all__ = [
    "LE_GENERATORS",
    "generate_large_exposures",
    "generate_lmt",
    "generate_sdi_large_exposures",
    "generate_sdi_lmt",
]
