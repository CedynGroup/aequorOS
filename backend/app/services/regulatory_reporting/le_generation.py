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
)
from app.services import regulatory_capital, regulatory_liquidity
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

# LMTD-derived contractual maturity ladder (condensed bucket set — the
# published Table 2 carries 15 columns; these buckets follow the plan spec
# and the fact_derivation repricing day-bounds). Undated positions land in
# the separate non-contractual bucket (Table 2's final column), never in
# overnight.
_LADDER_BUCKETS: tuple[tuple[str, str, int | None], ...] = (
    ("overnight", "Overnight", 1),
    ("2-7d", "2-7 days", 7),
    ("8-30d", "8-30 days", 30),
    ("1-3m", "1-3 months", 91),
    ("3-6m", "3-6 months", 182),
    ("6-12m", "6-12 months", 365),
    (">1y", "Over 1 year", None),
)
_NON_CONTRACTUAL_BUCKET = ("non_contractual", "Non-contractual (no stated maturity)")
_LADDER_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT", "CASH", "OTHER_ASSET")
_LADDER_LIABILITY_TYPES = ("DEPOSIT", "INTERBANK_BORROWING", "OTHER_LIABILITY")
_TOP_DEPOSITORS = 10


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
        ccf = _dec_or_none(attributes.get("credit_conversion_factor"))
        undrawn = notional_ghs * ccf if notional_ghs is not None and ccf is not None else _ZERO
        issuer = attributes.get("issuer")
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
    for code, _, upper in _LADDER_BUCKETS:
        if upper is None or days <= upper:
            return code
    return _LADDER_BUCKETS[-1][0]


def _ladder_section(rows: list[_CanonicalRow], as_of: date) -> tuple[dict[str, Any], Decimal]:
    assets: dict[str, Decimal] = {}
    liabilities: dict[str, Decimal] = {}
    for row in rows:
        bucket = _maturity_bucket(row.contractual_maturity, as_of)
        if row.position_type in _LADDER_ASSET_TYPES:
            assets[bucket] = assets.get(bucket, _ZERO) + row.balance_ghs
        else:
            liabilities[bucket] = liabilities.get(bucket, _ZERO) + row.balance_ghs

    ladder_rows: list[dict[str, Any]] = []
    cumulative = _ZERO
    for code, label, _ in _LADDER_BUCKETS:
        asset_total = assets.get(code, _ZERO)
        liability_total = liabilities.get(code, _ZERO)
        gap = asset_total - liability_total
        cumulative += gap
        ladder_rows.append(
            snapshot_row(
                code,
                f"Contractual mismatch — {label}",
                gap,
                assets_ghs=str(asset_total),
                liabilities_ghs=str(liability_total),
                cumulative_gap_ghs=str(cumulative),
            )
        )
    nc_code, nc_label = _NON_CONTRACTUAL_BUCKET
    nc_assets = assets.get(nc_code, _ZERO)
    nc_liabilities = liabilities.get(nc_code, _ZERO)
    # The non-contractual column sits outside the dated cumulative run
    # (LMTD Table 2 keeps it as a separate final column).
    ladder_rows.append(
        snapshot_row(
            nc_code,
            f"Contractual mismatch — {nc_label}",
            nc_assets - nc_liabilities,
            assets_ghs=str(nc_assets),
            liabilities_ghs=str(nc_liabilities),
        )
    )
    bucket_codes = [*(code for code, _, _ in _LADDER_BUCKETS), nc_code]
    total_gap = sum(
        (assets.get(code, _ZERO) - liabilities.get(code, _ZERO) for code in bucket_codes),
        _ZERO,
    )
    section = snapshot_section(
        "maturity_ladder",
        "Contractual Maturity Mismatch",
        ladder_rows,
        snapshot_total(
            "contractual_gap_total_ghs",
            "Total contractual mismatch (assets − liabilities)",
            total_gap,
            equals_sum_of_rows=True,
        ),
    )
    return section, total_gap


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
        "Maturity ladder: condensed bucket set (overnight / 2-7d / 8-30d / 1-3m / "
        "3-6m / 6-12m / >1y + non-contractual) derived from canonical contractual "
        "maturities; the published Table 2 carries 15 columns and an off-balance "
        "block the canonical data does not yet fill."
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

    ladder_rows = _load_canonical_rows(
        db, ctx, bank, period.period_end, (*_LADDER_ASSET_TYPES, *_LADDER_LIABILITY_TYPES)
    )
    if ladder_rows:
        ladder_section, total_gap = _ladder_section(ladder_rows, period.period_end)
        sections.append(ladder_section)
        totals.append(
            snapshot_row(
                "contractual_gap_total_ghs",
                "Total contractual mismatch",
                total_gap,
                unit="ghs",
            )
        )

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
