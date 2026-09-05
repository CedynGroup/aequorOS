from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    InstitutionType,
    Jurisdiction,
)
from app.schemas.banks import (
    BankFactRead,
    BankFactsRead,
    BankListRead,
    BankRead,
    BankReportingPeriodListRead,
    BankReportingPeriodRead,
    InstitutionTypeRead,
    JurisdictionRead,
)
from app.services import authorization, regulatory_parameters
from app.services.public_ids import normalize_public_id

_FACT_GROUP_FIELDS: dict[str, str] = {
    "balance_sheet": "balance_sheet",
    "loan_exposure": "loan_exposures",
    "securities": "securities",
    "off_balance": "off_balance",
    "lcr_inflow": "lcr_inflows",
    "market_risk": "market_risk",
    "operational_income": "operational_income",
    "cashflow": "cash_flows",
    "capital_component": "capital_components",
    "deposit_behavior": "deposit_behavior",
}


def _jurisdictions_by_code(db: Session, codes: set[str]) -> dict[str, JurisdictionRead]:
    """Resolve registry rows for the given codes (global reference data)."""
    if not codes:
        return {}
    rows = db.scalars(select(Jurisdiction).where(Jurisdiction.code.in_(codes)))
    return {row.code: JurisdictionRead.model_validate(row, from_attributes=True) for row in rows}


def _institution_types_by_code(
    db: Session, keys: set[tuple[str, str]]
) -> dict[tuple[str, str], InstitutionTypeRead]:
    """Resolve institution-type registry rows, keyed by ``(type_code, jurisdiction)``.

    Display-side resolution (like ``_jurisdictions_by_code``): a code with no
    registry row simply resolves to None on the payload — never a fabricated
    default. Calculation code that must not silently default goes through
    ``app/services/institution_types.py`` instead (fail-closed).

    Keyed by jurisdiction as well as licence code because the ENFORCED exposure
    limits it overlays are jurisdiction-scoped: resolving them without the bank's
    own jurisdiction showed Ghana's limits for every institution (enterprise audit
    2026-08-20 §6)."""
    if not keys:
        return {}
    rows = {
        row.type_code: row
        for row in db.scalars(
            select(InstitutionType).where(
                InstitutionType.type_code.in_({type_code for type_code, _ in keys})
            )
        )
    }
    result: dict[tuple[str, str], InstitutionTypeRead] = {}
    for type_code, jurisdiction in keys:
        row = rows.get(type_code)
        if row is None:
            continue
        detail = InstitutionTypeRead.model_validate(row, from_attributes=True)
        # Surface the ENFORCED exposure limits from the regulatory-parameter
        # control plane so the displayed value equals what the engine applies —
        # one source of truth once an operator edits them (audit M2). Falls back
        # to the registry column when the control-plane class row is unseeded.
        overrides: dict[str, Decimal] = {}
        for code in ("large_exposure_limit_pct", "single_obligor_limit_pct"):
            enforced = regulatory_parameters.resolve_class_value(
                db, row.institution_class, code, jurisdiction=jurisdiction
            )
            if enforced is not None:
                overrides[code] = enforced
        result[(type_code, jurisdiction)] = (
            detail.model_copy(update=overrides) if overrides else detail
        )
    return result


def _bank_read(
    bank: Bank,
    jurisdictions: dict[str, JurisdictionRead],
    institution_types: dict[tuple[str, str], InstitutionTypeRead],
    *,
    liquidity_monitoring_access: bool,
) -> BankRead:
    read = BankRead.model_validate(bank, from_attributes=True)
    return read.model_copy(
        update={
            "jurisdiction": jurisdictions.get(bank.jurisdiction_code),
            "institution_type_detail": institution_types.get(
                (bank.institution_type, bank.jurisdiction_code)
            ),
            "liquidity_monitoring_access": liquidity_monitoring_access,
        }
    )


def _liquidity_monitoring_access(
    db: Session,
    ctx: TenantContext,
    banks: list[Bank],
) -> dict[str, bool]:
    denied = dict.fromkeys((bank.id for bank in banks), False)
    if ctx.actor_user_id is None or ctx.authorization_version is None or not banks:
        return denied
    try:
        decisions = authorization.evaluate_liquidity_monitoring_views(
            db,
            organization_id=ctx.organization_id,
            principal_id=ctx.actor_user_id,
            institutions=banks,
            failure_surface="bank_access_summary",
        )
    except Exception:  # noqa: BLE001 - access presentation denies closed
        return denied
    return {bank_id: decision.allowed for bank_id, decision in decisions.items()}


def list_banks(db: Session, ctx: TenantContext) -> BankListRead:
    banks = list(
        db.scalars(
            select(Bank)
            .where(Bank.organization_id == ctx.organization_id)
            .order_by(Bank.name, Bank.id)
        )
    )
    jurisdictions = _jurisdictions_by_code(db, {bank.jurisdiction_code for bank in banks})
    institution_types = _institution_types_by_code(
        db, {(bank.institution_type, bank.jurisdiction_code) for bank in banks}
    )
    liquidity_access = _liquidity_monitoring_access(db, ctx, banks)
    return BankListRead(
        banks=[
            _bank_read(
                bank,
                jurisdictions,
                institution_types,
                liquidity_monitoring_access=liquidity_access[bank.id],
            )
            for bank in banks
        ]
    )


def get_bank(db: Session, ctx: TenantContext, bank_reference: str) -> BankRead:
    bank = resolve_bank_reference(db, ctx, bank_reference)
    jurisdictions = _jurisdictions_by_code(db, {bank.jurisdiction_code})
    institution_types = _institution_types_by_code(
        db, {(bank.institution_type, bank.jurisdiction_code)}
    )
    liquidity_access = _liquidity_monitoring_access(db, ctx, [bank])
    return _bank_read(
        bank,
        jurisdictions,
        institution_types,
        liquidity_monitoring_access=liquidity_access[bank.id],
    )


def list_reporting_periods(
    db: Session, ctx: TenantContext, bank_reference: str
) -> BankReportingPeriodListRead:
    bank = resolve_bank_reference(db, ctx, bank_reference)
    periods = list(
        db.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
    )
    return BankReportingPeriodListRead(
        bank_id=bank.id,
        periods=[
            BankReportingPeriodRead.model_validate(period, from_attributes=True)
            for period in periods
        ],
    )


def get_period_facts(
    db: Session, ctx: TenantContext, bank_reference: str, period_id: UUID
) -> BankFactsRead:
    bank = resolve_bank_reference(db, ctx, bank_reference)
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    facts = db.scalars(
        select(BankFinancialFact)
        .where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
        )
        .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
    )
    grouped: dict[str, list[BankFactRead]] = {field: [] for field in _FACT_GROUP_FIELDS.values()}
    for fact in facts:
        # Analytical-overlay groups (irr_*, fx_*, ftp_*) are surfaced by their own
        # module dashboards, not this canonical balance-sheet facts view.
        field = _FACT_GROUP_FIELDS.get(fact.fact_group)
        if field is None:
            continue
        grouped[field].append(BankFactRead.model_validate(fact, from_attributes=True))
    return BankFactsRead(
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        **grouped,
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def resolve_bank_reference(db: Session, ctx: TenantContext, reference: str) -> Bank:
    """Resolve a bank path token — the institution ID (BK-XXXXXXXX).

    Canonical form is uppercase; lowercase input from integrations is
    tolerated. Lookup is tenant-scoped.
    """
    return _get_bank_or_404(db, ctx, normalize_public_id(reference))
