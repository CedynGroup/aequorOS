"""Canonical → BankFinancialFact derivation: the Data Engine activation bridge.

Turns a bank's ingested canonical state (position snapshots, GL accounts,
products, and reference datasets) into the exact ``BankFinancialFact`` taxonomy
the six regulatory module engines consume for one reporting period. After a
derivation, every dashboard renders calculations on the uploaded data — no
seed required.

Ownership contract: the activation OWNS its target period's facts. Each run
deletes every existing ``BankFinancialFact`` for the (bank, period) and
re-derives from current canonical state, so re-activation is idempotent at the
fact level while regulatory runs remain immutable history.

Every derived fact carries ``attributes["source"] = "data_engine"`` plus a
short ``attributes["derived_from"]`` provenance note. The two HQLA cash-mirror
rows are the single exception: the liquidity engine identifies cash-derived
HQLA via ``attributes["source"] == "cash"``, so those rows keep the engine
contract and carry ``attributes["derived_by"] = "data_engine"`` instead.

Derivation heuristics (group → source → assumptions)
====================================================

balance_sheet
    GL accounts + position aggregates. Cash GLs are classified by account code
    (1001 → ``cash_vault``, 1002 → ``bog_required_reserves``, 1003 →
    ``bog_excess_reserves`` per the documented chart ranges) with a
    name-pattern fallback; when the required/excess split is unavailable the
    whole cash balance maps to ``cash_vault`` with a warning. Securities split
    ``securities_bog_bills`` vs ``securities_gog_bonds`` by product code
    (TBILL/BOND) with a ≤ 397-day remaining-maturity fallback. ``loans_gross``
    is Σ LOAN ``balance_ghs``. ``other_assets`` is the GL asset residual — all
    leaf ASSET GL balances not classified as cash, securities, or loan-book
    accounts (interbank placements, fixed assets, and sundry assets live here;
    the loan-loss provision contra sits in the loan GL range and is excluded
    with it, since the balance sheet carries loans gross like the seed).
    Deposits are classified by product:
    retail products split stable/less-stable via the ``DEPOSIT_STABILITY``
    behavioral assumption (products without an assumption default to fully
    less-stable — conservative); wholesale current accounts split operational
    /non-operational via the same assumption, non-operational current →
    ``wholesale_non_op_sme`` and wholesale term → ``wholesale_non_op_corporate``.
    Interbank borrowings ≤ 1y → ``secured_funding_l1``, > 1y →
    ``term_borrowings_gt_1y``. ``capital_total`` = Σ signed capital-structure
    amounts. The balance-sheet identity is then enforced: any gap between
    assets and liabilities+equity is plugged into ``other_assets`` (assets
    short) or ``term_borrowings_gt_1y`` (funding short); a gap above 0.5% of
    assets additionally emits a warning — uploaded books are imperfect and the
    plug is reported, not hidden.

loan_exposure
    LOAN positions partitioned by IFRS 9 stage and product
    ``regulatory_category``. Stage 3 → ``past_due_90`` (RW150). Category map:
    CORPORATE_UNRATED / CORPORATE_LOAN_UNRATED_100RW / AGRICULTURE →
    ``corporate_unrated`` (RW100), SME_UNRATED → ``sme_retail`` (RW75),
    RETAIL_UNSECURED → ``retail_other`` (RW75), RESIDENTIAL_MORTGAGE →
    ``residential_mortgage`` (RW35), COMMERCIAL_REAL_ESTATE →
    ``commercial_real_estate`` (RW100). Unknown/missing categories fall back
    to ``corporate_unrated`` with a warning. Σ exposures == ``loans_gross``
    by construction.

securities
    The balance-sheet bills/bonds split re-emitted as L1/RW0 HQLA rows, plus
    the two cash-mirror rows (``cash_vault_hqla``, ``bog_excess_reserves_hqla``)
    carrying ``source="cash"`` so stress haircuts skip them.

off_balance
    LC_GUARANTEE positions → ``committed_retail`` (RETAIL_INDIVIDUAL
    counterparty) or ``committed_corporate`` (default). Amount is Σ
    ``notional_ghs``; ``ccf_pct`` is the EAD-preserving weighted average of
    per-position ``credit_conversion_factor`` (default 50% when absent, with
    warning). Risk weights RW75 (retail) / RW100 (corporate).

lcr_inflow
    Positions maturing within 30 days of the as-of date:
    ``retail_loan_repayments`` (retail_other + residential_mortgage loans, 50%),
    ``corporate_sme_repayments`` (all other loans, 50%), ``interbank_maturing``
    (INTERBANK_PLACEMENT, 100%). If no loan carries a maturity date the
    documented fallback books 2% of each segment's gross balance and warns.

market_risk / fx_position
    Per non-GHS currency: assets (LOAN, SECURITY_HOLDING, INTERBANK_PLACEMENT)
    minus liabilities (DEPOSIT, INTERBANK_BORROWING), both in original currency
    and in GHS via the ingested ``balance_ghs``, plus the signed FX_HEDGE
    notional deltas: a hedge's sell leg subtracts its notional from the sold
    currency's net and its buy leg adds ``notional × contract_rate`` to the
    bought currency's net (GHS legs are ignored — GHS is the base currency, so
    only foreign-currency exposure moves). The delta per currency is carried
    as ``net_derivatives_ccy`` in the fact attributes, mirroring the seed.
    Spot from ``fx_rates_current``. LC_GUARANTEE is off-balance and excluded
    from the NOP. A currency without a daily return history is excluded from
    ``fx_position`` (the VaR engine requires a history) and warned.
    ``net_long_fx`` / ``net_short_fx`` are the long/short sums over the
    included currencies' post-hedge nets.

fx_return_history
    ``fx_rates_historical`` per currency, chronological: simple daily returns
    ``r_t = S_t / S_(t-1) - 1`` (rounded to 6 dp), most recent 250 kept.

fx_hedge
    FX_HEDGE positions → one fact per hedge (category = hedge id, amount =
    ``mtm_ghs``, mirroring the seed): instrument lowercased onto the engine
    vocabulary (forward | cross_currency_swap | option), pair, sell-leg
    notional, contract rate, ``maturity_days`` = contractual maturity − as-of,
    and the IFRS 9 effectiveness measures (``prospective_r2``,
    ``dollar_offset_ratio``; a hedge missing either defaults it to 0 —
    conservatively ineffective — with a warning). Skipped with a note when no
    hedge positions exist (the FX engine tolerates an empty hedge book).

operational_income
    Up to three trailing 12-month windows of ``historical_financials``
    (gross income = net_interest_income + non_interest_income per month),
    labelled by window-end year. Fewer than three full windows warns.

capital_component
    ``capital_structure`` rows → categories (component name lower-cased),
    tier from the payload tier (CET1 / AT1 / TIER2, ``*_DEDUCTION`` or a
    negative amount marks a deduction; amounts stored as absolute values).

irr_position
    Rate-sensitive positions bucketed into the nine canonical IRRBB buckets by
    repricing horizon: FLOATING → ``next_repricing_date``, FIXED →
    ``contractual_maturity``. Non-maturity deposits: zero-rate current
    accounts are the behaviorally non-rate-sensitive core and are EXCLUDED;
    interest-bearing savings reprice administratively at the 3-6m bucket;
    interest-bearing wholesale current accounts reprice at their
    ``NMD_DURATION`` behavioral horizon (default overnight). Subordinated debt
    from the capital structure prices as a 5y+ fixed liability at the long end
    of the ingested GHS yield curve. Positions aggregate by
    (side, family, bucket) with balance-weighted average rates;
    ``fixed_or_float`` is the majority side by balance. Bucket midpoints use
    the canonical values so the parameter-table discount curve keys match.

irr_swap
    INTEREST_RATE_SWAP positions → one fact per swap (category = swap id,
    amount = GHS notional) shaped exactly like the seed: ``pay_rate_pct``
    (always the swap's fixed rate — the template column keeps its pay-fixed
    name), ``receive_index``, ``tenor_years``, ``direction``, and the engine's
    leg placement — the floating leg buckets at the index reset tenor
    (91d T-Bill → 1-3m) and the fixed leg at the remaining maturity, with
    midpoints from the nine canonical buckets so the parameter-table discount
    curve keys match. ``receive_bucket``/``pay_bucket`` locate the legs the
    bank receives/pays: pay-fixed swaps receive the floating leg; receive-fixed
    swaps are the mirror image (fixed leg received, floating leg paid). Any
    other direction is skipped with a warning. Skipped with a note when no
    swap positions exist.

ftp_curve_point
    The ingested GHS sovereign yield curve, with a documented liquidity-premium
    and funding-spread schedule by tenor (0→50 bps and 40→60 bps respectively,
    stepping up with tenor, mirroring the BoG baseline shape);
    ``ftp_rate = base + (liquidity_bps + funding_bps) / 100``.

ftp_product
    Product families (loan segments, government securities, deposit segments)
    with Σ ``balance_ghs``, balance-weighted customer rates, and
    balance-weighted remaining-maturity tenors (NMD families use the
    ``NMD_DURATION`` behavioral tenor). The FTP transfer rate is re-derived
    from the derived curve with the engine's own interpolation so product and
    curve stay aligned. Operating-cost / capital-charge defaults are
    documented constants per family; expected credit loss is the actual
    Σ ``ecl_provision_ghs`` / Σ balance for loan families.

ftp_branch
    LOAN and DEPOSIT positions grouped by ``branch_id`` joined to the
    ``business_units`` reference names. Positions without a branch are not
    branch-booked (treasury/central) and stay out of the branch table.

ftp_nmd
    Non-maturity deposit segments (retail current, savings, wholesale
    current): core % from ``DEPOSIT_STABILITY`` (default 50% with warning),
    effective duration from ``NMD_DURATION`` (default 12 months with warning).

All money values quantize to 4 dp; every numeric parse goes through
``Decimal(str(...))``. The derivation is deterministic for a fixed canonical
state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ftp.engine import CurvePoint, CurveResult, build_curve
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
)
from app.services.market_data import (
    CurveView,
    get_fx_spot,
    get_fx_spot_history,
    get_yield_curve,
    list_fx_base_currencies,
)

MONEY = Decimal("0.0001")
RATE = Decimal("0.0001")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_TWELVE = Decimal("12")

SOURCE_TAG = "data_engine"
_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")
# |assets - liabilities - equity| above this fraction of assets warns.
BALANCE_GAP_WARN_FRACTION = Decimal("0.005")
# Bills vs bonds fallback split: remaining maturity at or under 397 days is a bill.
_BILL_MAX_REMAINING_DAYS = 397
_LCR_WINDOW_DAYS = 30
_LCR_FALLBACK_FRACTION = Decimal("0.02")
_FX_RETURN_WINDOW = 250
# A canonical FX spot history replaces the legacy reference-row history for a
# currency only when it is deep enough to feed a meaningful VaR return series.
_MARKET_FX_HISTORY_MIN_OBSERVATIONS = 30
# The base currency all module FX facts are expressed against.
_BASE_CURRENCY = "GHS"
_DEFAULT_CCF_PCT = Decimal("50")
_DEFAULT_NMD_CORE_PCT = Decimal("50")
_DEFAULT_NMD_DURATION_MONTHS = Decimal("12")

# The nine canonical IRRBB buckets: (name, upper bound in days, midpoint years).
_IRR_BUCKETS: tuple[tuple[str, int | None, str], ...] = (
    ("overnight", 1, "0.003"),
    ("1-7d", 7, "0.014"),
    ("8-30d", 30, "0.06"),
    ("1-3m", 91, "0.17"),
    ("3-6m", 182, "0.38"),
    ("6-12m", 365, "0.75"),
    ("1-3y", 1095, "1.9"),
    ("3-5y", 1825, "4.0"),
    ("5y+", None, "7.0"),
)
_BUCKET_MIDPOINT = {name: midpoint for name, _, midpoint in _IRR_BUCKETS}
_SAVINGS_REPRICING_BUCKET = "3-6m"

# Loan regulatory-category → (seed loan_exposure category, risk weight code).
_LOAN_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "CORPORATE_UNRATED": ("corporate_unrated", "RW100"),
    "CORPORATE_LOAN_UNRATED_100RW": ("corporate_unrated", "RW100"),
    "AGRICULTURE": ("corporate_unrated", "RW100"),
    "SME_UNRATED": ("sme_retail", "RW75"),
    "SME_RETAIL": ("sme_retail", "RW75"),
    "RETAIL_UNSECURED": ("retail_other", "RW75"),
    "RETAIL_OTHER": ("retail_other", "RW75"),
    "RESIDENTIAL_MORTGAGE": ("residential_mortgage", "RW35"),
    "COMMERCIAL_REAL_ESTATE": ("commercial_real_estate", "RW100"),
}
_PAST_DUE_CATEGORY = ("past_due_90", "RW150")
_RETAIL_LOAN_CATEGORIES = ("retail_other", "residential_mortgage")

# Loan seed category → IRR/FTP family label.
_LOAN_FAMILY = {
    "corporate_unrated": "corporate_loans",
    "sme_retail": "sme_loans",
    "retail_other": "retail_loans",
    "residential_mortgage": "mortgages",
    "commercial_real_estate": "cre_loans",
    "past_due_90": "corporate_loans",
}

# FTP documented cost defaults (percent) per product family kind.
_FTP_ASSET_LOAN_OPEX_PCT = Decimal("0.5")
_FTP_ASSET_LOAN_CAPITAL_PCT = Decimal("0.1")
_FTP_SECURITIES_OPEX_PCT = Decimal("0.05")
_FTP_LIABILITY_OPEX_PCT = Decimal("0.3")

# FTP liquidity-premium / funding-spread schedules in bps by tenor (years).
# Mirrors the BoG baseline shape: premia rise with tenor.
_FTP_LIQUIDITY_PREMIUM_STEPS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0.5"), Decimal("0")),
    (Decimal("1"), Decimal("5")),
    (Decimal("2"), Decimal("10")),
    (Decimal("3"), Decimal("20")),
    (Decimal("5"), Decimal("30")),
    (Decimal("7"), Decimal("40")),
)
_FTP_LIQUIDITY_PREMIUM_CAP = Decimal("50")
_FTP_FUNDING_SPREAD_STEPS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0.25"), Decimal("40")),
    (Decimal("1"), Decimal("45")),
    (Decimal("3"), Decimal("50")),
    (Decimal("7"), Decimal("55")),
)
_FTP_FUNDING_SPREAD_CAP = Decimal("60")

type GroupStatus = Literal["derived", "skipped"]


class DerivationError(Exception):
    """The canonical state cannot support a derivation (no data at as-of)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GroupResult:
    group: str
    status: GroupStatus
    rows: int = 0
    warnings: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass(frozen=True)
class DerivationResult:
    bank_id: UUID
    reporting_period_id: UUID
    period_label: str
    as_of_date: date
    period_created: bool
    facts_deleted: int
    facts_created: int
    groups: tuple[GroupResult, ...]

    @property
    def warnings(self) -> list[str]:
        return [warning for group in self.groups for warning in group.warnings]


@dataclass(frozen=True)
class _PositionRow:
    """One current-generation snapshot flattened to the fields derivation uses."""

    source_reference: str
    position_type: str
    currency: str
    balance: Decimal
    balance_ghs: Decimal
    interest_rate: Decimal | None
    rate_type: str | None
    contractual_maturity: date | None
    next_repricing_date: date | None
    ifrs9_stage: int | None
    product_code: str | None
    regulatory_category: str | None
    counterparty_type: str | None
    branch_id: str | None
    ecl_ghs: Decimal
    notional_ghs: Decimal
    ccf: Decimal | None
    # The raw snapshot attributes: hedge/swap instrument terms live here.
    attributes: dict[str, Any]


@dataclass(frozen=True)
class _Canonical:
    as_of: date
    base_currency: str
    positions: list[_PositionRow]
    gl_accounts: list[CanonicalGlAccount]
    refs: dict[str, list[dict[str, Any]]]
    # Canonical market data (vendor-blind, via app.services.market_data).
    # When present it wins over the legacy reference-row datasets; the
    # reference rows remain the fallback so uploads without market data
    # connections keep deriving exactly as before.
    market_curve: CurveView | None = None
    market_spots: dict[str, Decimal] = field(default_factory=dict)
    market_fx_history: dict[str, list[tuple[date, Decimal]]] = field(default_factory=dict)

    def by_type(self, *position_types: str) -> list[_PositionRow]:
        return [row for row in self.positions if row.position_type in position_types]


@dataclass
class _DepositSplit:
    retail_stable: Decimal = _ZERO
    retail_less_stable: Decimal = _ZERO
    wholesale_operational: Decimal = _ZERO
    wholesale_non_op_sme: Decimal = _ZERO
    wholesale_non_op_corporate: Decimal = _ZERO


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _dec(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None or value == "":
        if default is None:
            raise DerivationError("invalid_value", "A required numeric value is missing.")
        return default
    return Decimal(str(value))


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def derive_facts(
    db: Session, ctx: TenantContext, bank_id: UUID, as_of_date: date
) -> DerivationResult:
    """Derive the full module fact set for ``as_of_date`` from canonical data."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    canonical = _load_canonical(db, ctx, bank, as_of_date)
    if not canonical.positions:
        raise DerivationError(
            "no_canonical_data",
            f"No accepted canonical position snapshots exist for {as_of_date.isoformat()}. "
            "Ingest position data for this as-of date before activating.",
        )

    period, period_created = _ensure_period(db, ctx, bank, as_of_date)
    facts_deleted = _delete_period_facts(db, ctx, bank, period)

    groups: list[GroupResult] = []
    facts: list[BankFinancialFact] = []

    balance_sheet, loan_rows, cash_amounts, securities_split = _derive_balance_sheet_block(
        canonical, groups
    )
    facts.extend(_fact(bank, period, spec) for spec in balance_sheet)
    facts.extend(_fact(bank, period, spec) for spec in _derive_loan_exposure(loan_rows, groups))
    facts.extend(
        _fact(bank, period, spec)
        for spec in _derive_securities(securities_split, cash_amounts, groups)
    )
    facts.extend(_fact(bank, period, spec) for spec in _derive_off_balance(canonical, groups))
    facts.extend(
        _fact(bank, period, spec) for spec in _derive_lcr_inflows(canonical, loan_rows, groups)
    )
    fx_specs, fx_currencies = _derive_fx_positions(canonical, groups)
    facts.extend(_fact(bank, period, spec) for spec in fx_specs)
    facts.extend(
        _fact(bank, period, spec) for spec in _derive_fx_returns(canonical, fx_currencies, groups)
    )
    facts.extend(_fact(bank, period, spec) for spec in _derive_fx_hedges(canonical, groups))
    facts.extend(
        _fact(bank, period, spec) for spec in _derive_operational_income(canonical, groups)
    )
    capital_specs = _derive_capital_components(canonical, groups)
    facts.extend(_fact(bank, period, spec) for spec in capital_specs)
    facts.extend(
        _fact(bank, period, spec) for spec in _derive_irr_positions(canonical, loan_rows, groups)
    )
    facts.extend(_fact(bank, period, spec) for spec in _derive_irr_swaps(canonical, groups))
    curve = _derive_ftp_curve(canonical, groups)
    ftp_curve_specs = curve[0]
    facts.extend(_fact(bank, period, spec) for spec in ftp_curve_specs)
    facts.extend(
        _fact(bank, period, spec)
        for spec in _derive_ftp_products(canonical, loan_rows, curve[1], groups)
    )
    facts.extend(_fact(bank, period, spec) for spec in _derive_ftp_branches(canonical, groups))
    facts.extend(_fact(bank, period, spec) for spec in _derive_ftp_nmd(canonical, groups))

    db.add_all(facts)
    db.flush()
    return DerivationResult(
        bank_id=bank.id,
        reporting_period_id=period.id,
        period_label=period.label,
        as_of_date=as_of_date,
        period_created=period_created,
        facts_deleted=facts_deleted,
        facts_created=len(facts),
        groups=tuple(groups),
    )


# ---------------------------------------------------------------------------
# Canonical loading
# ---------------------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _load_canonical(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> _Canonical:
    rows = db.execute(
        select(
            CanonicalPositionSnapshot, CanonicalPosition, CanonicalProduct, CanonicalCounterparty
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
    ).all()

    base_currency = (bank.currency or _BASE_CURRENCY).strip().upper()
    positions = [
        _position_row(snapshot, position, product, counterparty, base_currency)
        for snapshot, position, product, counterparty in rows
    ]

    gl_accounts = list(
        db.scalars(
            select(CanonicalGlAccount).where(
                CanonicalGlAccount.organization_id == ctx.organization_id,
                CanonicalGlAccount.bank_id == bank.id,
                CanonicalGlAccount.as_of_date <= as_of,
                CanonicalGlAccount.superseded_by.is_(None),
                CanonicalGlAccount.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
                CanonicalGlAccount.balance.is_not(None),
            )
        )
    )
    # Keep only the latest generation per account code at or before the as-of.
    latest_by_code: dict[str, CanonicalGlAccount] = {}
    for account in gl_accounts:
        current = latest_by_code.get(account.account_code)
        if current is None or account.as_of_date > current.as_of_date:
            latest_by_code[account.account_code] = account

    refs: dict[str, list[dict[str, Any]]] = {}
    # Latest ingestion batch per dataset kind. Postgres has no max(uuid), so the
    # winner is picked in Python: newest created_at, then the batch UUIDv7 text
    # (time-ordered) as the tie-break.
    batch_rows = db.execute(
        select(
            CanonicalReferenceRow.dataset_kind,
            CanonicalReferenceRow.ingestion_batch_id,
            func.max(CanonicalReferenceRow.created_at),
        )
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank.id,
            CanonicalReferenceRow.as_of_date <= as_of,
        )
        .group_by(
            CanonicalReferenceRow.dataset_kind,
            CanonicalReferenceRow.ingestion_batch_id,
        )
    ).all()
    latest_batches: dict[str, tuple[Any, str, UUID]] = {}
    for kind, batch_id, created_at in batch_rows:
        candidate = (created_at, str(batch_id), batch_id)
        current = latest_batches.get(kind)
        if current is None or candidate[:2] > current[:2]:
            latest_batches[kind] = candidate
    for kind, (_, _, batch_id) in latest_batches.items():
        payloads = db.scalars(
            select(CanonicalReferenceRow.payload)
            .where(
                CanonicalReferenceRow.organization_id == ctx.organization_id,
                CanonicalReferenceRow.bank_id == bank.id,
                CanonicalReferenceRow.dataset_kind == kind,
                CanonicalReferenceRow.ingestion_batch_id == batch_id,
            )
            .order_by(CanonicalReferenceRow.row_index)
        ).all()
        refs[kind] = list(payloads)

    market_curve, market_spots, market_fx_history = _load_market_data(db, ctx, bank, as_of)
    return _Canonical(
        as_of=as_of,
        base_currency=base_currency,
        positions=positions,
        gl_accounts=sorted(latest_by_code.values(), key=lambda account: account.account_code),
        refs=refs,
        market_curve=market_curve,
        market_spots=market_spots,
        market_fx_history=market_fx_history,
    )


def _load_market_data(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[CurveView | None, dict[str, Decimal], dict[str, list[tuple[date, Decimal]]]]:
    """Canonical market data by business scope (vendor-blind, §15 arbitration).

    The GHS curve feeds FTP; per-currency GHS spots overlay the legacy
    ``fx_rates_current`` dataset; spot histories deep enough for a VaR return
    series replace ``fx_rates_historical`` per currency. Everything absent
    falls back to the legacy reference rows.
    """
    base_ccy = (bank.currency or _BASE_CURRENCY).strip().upper()
    market_curve = get_yield_curve(db, ctx.organization_id, bank.id, base_ccy, as_of)
    market_spots: dict[str, Decimal] = {}
    market_fx_history: dict[str, list[tuple[date, Decimal]]] = {}
    for currency in list_fx_base_currencies(db, ctx.organization_id, bank.id, base_ccy, as_of):
        spot = get_fx_spot(db, ctx.organization_id, bank.id, currency, base_ccy, as_of)
        if spot is not None:
            market_spots[currency] = spot.rate
        history = get_fx_spot_history(db, ctx.organization_id, bank.id, currency, base_ccy, as_of)
        if len(history) >= _MARKET_FX_HISTORY_MIN_OBSERVATIONS:
            market_fx_history[currency] = history
    return market_curve, market_spots, market_fx_history


def _position_row(
    snapshot: CanonicalPositionSnapshot,
    position: CanonicalPosition,
    product: CanonicalProduct | None,
    counterparty: CanonicalCounterparty | None,
    base_currency: str,
) -> _PositionRow:
    attributes = snapshot.attributes or {}
    balance = _dec(snapshot.balance, _ZERO)
    balance_ghs = _dec_or_none(attributes.get("balance_ghs"))
    if balance_ghs is None:
        # Base-currency books carry no explicit conversion; fall back to balance.
        balance_ghs = balance if position.currency == base_currency else _ZERO
    return _PositionRow(
        source_reference=snapshot.source_reference,
        position_type=position.position_type,
        currency=position.currency,
        balance=balance,
        balance_ghs=balance_ghs,
        interest_rate=_dec_or_none(snapshot.interest_rate),
        rate_type=snapshot.rate_type,
        contractual_maturity=snapshot.contractual_maturity,
        next_repricing_date=snapshot.next_repricing_date,
        ifrs9_stage=snapshot.ifrs9_stage,
        product_code=product.product_code if product is not None else None,
        regulatory_category=product.regulatory_category if product is not None else None,
        counterparty_type=counterparty.counterparty_type if counterparty is not None else None,
        branch_id=attributes.get("branch_id"),
        ecl_ghs=_dec(attributes.get("ecl_provision_ghs"), _ZERO),
        notional_ghs=_dec(attributes.get("notional_ghs"), _ZERO),
        ccf=_dec_or_none(attributes.get("credit_conversion_factor")),
        attributes=attributes,
    )


def _ensure_period(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[BankReportingPeriod, bool]:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end == as_of,
        )
    )
    if period is not None:
        return period, False
    period = BankReportingPeriod(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        period_start=as_of.replace(day=1),
        period_end=as_of,
        label=f"{as_of.year:04d}-{as_of.month:02d}",
        status="open",
    )
    db.add(period)
    db.flush()
    return period, True


def _delete_period_facts(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> int:
    existing = (
        db.scalar(
            select(func.count())
            .select_from(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
            )
        )
        or 0
    )
    db.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
        )
    )
    db.flush()
    return int(existing)


# ---------------------------------------------------------------------------
# Fact assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FactSpec:
    fact_group: str
    category: str
    amount: Decimal
    derived_from: str
    currency: str | None = None
    risk_weight_code: str | None = None
    hqla_level: str | None = None
    ccf_pct: Decimal | None = None
    rate_pct: Decimal | None = None
    income_year: int | None = None
    capital_tier: str | None = None
    is_deduction: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)
    source_tag: str = SOURCE_TAG


def _fact(bank: Bank, period: BankReportingPeriod, spec: _FactSpec) -> BankFinancialFact:
    attributes = dict(spec.attributes)
    attributes["source"] = spec.source_tag
    if spec.source_tag != SOURCE_TAG:
        attributes["derived_by"] = SOURCE_TAG
    attributes["derived_from"] = spec.derived_from
    return BankFinancialFact(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        fact_group=spec.fact_group,
        category=spec.category,
        amount=money(spec.amount),
        currency=spec.currency or bank.currency,
        risk_weight_code=spec.risk_weight_code,
        hqla_level=spec.hqla_level,
        ccf_pct=spec.ccf_pct,
        rate_pct=spec.rate_pct,
        income_year=spec.income_year,
        capital_tier=spec.capital_tier,
        is_deduction=spec.is_deduction,
        attributes=attributes,
    )


# ---------------------------------------------------------------------------
# balance_sheet + loan classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LoanRow:
    row: _PositionRow
    category: str
    risk_weight_code: str


def _classify_loans(canonical: _Canonical, warnings: list[str]) -> list[_LoanRow]:
    loans: list[_LoanRow] = []
    unknown: set[str] = set()
    for row in canonical.by_type("LOAN"):
        if row.ifrs9_stage == 3:
            category, code = _PAST_DUE_CATEGORY
        else:
            mapped = _LOAN_CATEGORY_MAP.get((row.regulatory_category or "").upper())
            if mapped is None:
                unknown.add(row.regulatory_category or row.product_code or "<unmapped>")
                mapped = _LOAN_CATEGORY_MAP["CORPORATE_UNRATED"]
            category, code = mapped
        loans.append(_LoanRow(row=row, category=category, risk_weight_code=code))
    if unknown:
        warnings.append(
            "Loan products without a mapped regulatory category defaulted to "
            f"corporate_unrated (RW100): {', '.join(sorted(unknown))}."
        )
    return loans


def _classify_gl_assets(
    canonical: _Canonical, warnings: list[str]
) -> tuple[dict[str, Decimal], Decimal]:
    """Classify leaf ASSET GLs → (cash rows by category, other-assets residual)."""
    cash = {"cash_vault": _ZERO, "bog_required_reserves": _ZERO, "bog_excess_reserves": _ZERO}
    other = _ZERO
    have_reserve_split = False
    for account in canonical.gl_accounts:
        if account.account_class != "ASSET" or account.balance is None:
            continue
        balance = _dec(account.balance)
        name = account.name.lower()
        code = account.account_code.strip()
        if code == "1001" or ("cash" in name and "flow" not in name):
            cash["cash_vault"] += balance
        elif code == "1002" or "statutory" in name or "required" in name:
            cash["bog_required_reserves"] += balance
            have_reserve_split = True
        elif code == "1003" or (("bog" in name or "central bank" in name) and "bill" not in name):
            cash["bog_excess_reserves"] += balance
            have_reserve_split = True
        elif _is_securities_gl(code, name) or _is_loan_gl(code, name):
            continue  # covered by position-level lines
        else:
            other += balance
    if (
        not have_reserve_split
        and (cash["bog_required_reserves"] + cash["bog_excess_reserves"]) == 0
    ):
        warnings.append(
            "The central-bank required/excess reserve split is unavailable in the GL; the full "
            "cash balance is carried as cash_vault."
        )
    return cash, other


def _is_securities_gl(code: str, name: str) -> bool:
    if code.isdigit() and 1200 <= int(code) <= 1299:
        return True
    return "t-bill" in name or "tbill" in name or "bond" in name


def _is_loan_gl(code: str, name: str) -> bool:
    if code.isdigit() and 1300 <= int(code) <= 1399:
        return True
    return "loan" in name or "mortgage" in name


def _is_retail_deposit_product(row: _PositionRow) -> bool:
    code = (row.product_code or "").upper()
    if ".CORP" in code or "CORPORATE" in code:
        return False
    return row.counterparty_type not in ("CORPORATE", "SME", "NBFI")


def _stability_by_product(canonical: _Canonical) -> dict[str, Decimal]:
    stability: dict[str, Decimal] = {}
    for payload in canonical.refs.get("behavioral_assumptions", ()):
        if str(payload.get("assumption_type", "")).upper() != "DEPOSIT_STABILITY":
            continue
        product_code = str(payload.get("product_code", "")).strip()
        value = _dec_or_none(payload.get("value"))
        if product_code and value is not None:
            stability[product_code] = value
    return stability


def _nmd_duration_months(canonical: _Canonical) -> dict[str, Decimal]:
    durations: dict[str, Decimal] = {}
    for payload in canonical.refs.get("behavioral_assumptions", ()):
        if str(payload.get("assumption_type", "")).upper() != "NMD_DURATION":
            continue
        product_code = str(payload.get("product_code", "")).strip()
        value = _dec_or_none(payload.get("value"))
        if product_code and value is not None:
            durations[product_code] = value
    return durations


def _prepayment_rate_by_product(canonical: _Canonical) -> dict[str, Decimal]:
    """Annual CPR per loan product from the behavioral_assumptions dataset.

    Populated by the loan-prepayment ML model (assumption_type PREPAYMENT_RATE);
    empty until a model batch is applied, in which case prepayment inflows are 0.
    """
    rates: dict[str, Decimal] = {}
    for payload in canonical.refs.get("behavioral_assumptions", ()):
        if str(payload.get("assumption_type", "")).upper() != "PREPAYMENT_RATE":
            continue
        product_code = str(payload.get("product_code", "")).strip()
        value = _dec_or_none(payload.get("value"))
        if product_code and value is not None:
            rates[product_code] = value
    return rates


def _expected_prepaid_30d(balance_ghs: Decimal, annual_cpr: Decimal) -> Decimal:
    """30-day expected prepaid principal from an annual CPR (SMM_30 x balance)."""
    if annual_cpr <= _ZERO:
        return _ZERO
    smm_30 = Decimal(str(1.0 - (1.0 - float(annual_cpr)) ** (30.0 / 365.0)))
    return balance_ghs * smm_30


def _split_deposits(canonical: _Canonical, warnings: list[str]) -> _DepositSplit:
    stability = _stability_by_product(canonical)
    split = _DepositSplit()
    missing_retail: set[str] = set()
    for row in canonical.by_type("DEPOSIT"):
        product_code = row.product_code or "<no-product>"
        share = stability.get(product_code)
        if _is_retail_deposit_product(row):
            if share is None:
                missing_retail.add(product_code)
                share = _ZERO  # conservative: all less-stable
            stable = row.balance_ghs * share
            split.retail_stable += stable
            split.retail_less_stable += row.balance_ghs - stable
        else:
            is_term = row.contractual_maturity is not None
            if is_term:
                split.wholesale_non_op_corporate += row.balance_ghs
            else:
                operational_share = share if share is not None else _ZERO
                operational = row.balance_ghs * operational_share
                split.wholesale_operational += operational
                split.wholesale_non_op_sme += row.balance_ghs - operational
    if missing_retail:
        warnings.append(
            "Retail deposit products without a DEPOSIT_STABILITY assumption were "
            f"treated as fully less-stable: {', '.join(sorted(missing_retail))}."
        )
    return split


def _derive_balance_sheet_block(
    canonical: _Canonical, groups: list[GroupResult]
) -> tuple[list[_FactSpec], list[_LoanRow], dict[str, Decimal], tuple[Decimal, Decimal]]:
    warnings: list[str] = []
    loan_rows = _classify_loans(canonical, warnings)
    cash, gl_other_assets = _classify_gl_assets(canonical, warnings)
    deposit_split = _split_deposits(canonical, warnings)

    bills = _ZERO
    bonds = _ZERO
    for row in canonical.by_type("SECURITY_HOLDING"):
        if _is_bill(row, canonical.as_of):
            bills += row.balance_ghs
        else:
            bonds += row.balance_ghs
    loans_gross = sum((loan.row.balance_ghs for loan in loan_rows), _ZERO)

    secured_funding = _ZERO
    term_borrowings = _ZERO
    one_year_out = canonical.as_of + timedelta(days=365)
    for row in canonical.by_type("INTERBANK_BORROWING"):
        if row.contractual_maturity is not None and row.contractual_maturity > one_year_out:
            term_borrowings += row.balance_ghs
        else:
            secured_funding += row.balance_ghs

    capital_total = sum(
        (
            _dec(payload.get("amount_ghs"), _ZERO)
            for payload in canonical.refs.get("capital_structure", ())
        ),
        _ZERO,
    )
    if capital_total == _ZERO:
        warnings.append(
            "No capital_structure reference rows were found; capital_total is zero and "
            "capital-dependent modules will fail until capital data is ingested."
        )

    other_assets = gl_other_assets
    assets_total = sum(cash.values(), _ZERO) + bills + bonds + loans_gross + other_assets
    funding_total = (
        deposit_split.retail_stable
        + deposit_split.retail_less_stable
        + deposit_split.wholesale_operational
        + deposit_split.wholesale_non_op_sme
        + deposit_split.wholesale_non_op_corporate
        + secured_funding
        + term_borrowings
        + capital_total
    )
    gap = funding_total - assets_total
    plug_note: str | None = None
    if gap > 0:
        other_assets += gap
        plug_note = f"balance plug +{money(gap)} {canonical.base_currency} added to other_assets"
    elif gap < 0:
        term_borrowings += -gap
        plug_note = (
            f"balance plug +{money(-gap)} {canonical.base_currency} added to term_borrowings_gt_1y"
        )
    if assets_total > 0 and abs(gap) > assets_total * BALANCE_GAP_WARN_FRACTION:
        warnings.append(
            f"Balance-sheet identity gap of {money(abs(gap))} {canonical.base_currency} "
            f"({(abs(gap) / assets_total * _HUNDRED).quantize(Decimal('0.01'))}% of assets) "
            f"was plugged ({plug_note}). Uploaded GL and sub-ledgers do not fully "
            "reconcile — review the reconciliation report."
        )

    def bs(category: str, amount: Decimal, side: str, derived_from: str) -> _FactSpec:
        return _FactSpec(
            fact_group="balance_sheet",
            category=category,
            amount=amount,
            derived_from=derived_from,
            attributes={"side": side},
        )

    specs = [
        bs("cash_vault", cash["cash_vault"], "asset", "GL cash accounts"),
        bs(
            "bog_required_reserves", cash["bog_required_reserves"], "asset", "GL statutory reserves"
        ),
        bs("bog_excess_reserves", cash["bog_excess_reserves"], "asset", "GL central-bank balances"),
        bs("securities_bog_bills", bills, "asset", "SECURITY_HOLDING positions (bills)"),
        bs("securities_gog_bonds", bonds, "asset", "SECURITY_HOLDING positions (bonds)"),
        bs("loans_gross", loans_gross, "asset", "LOAN positions Σ balance_ghs"),
        bs(
            "other_assets",
            other_assets,
            "asset",
            "GL asset residual (placements, fixed and sundry assets)"
            + (f"; {plug_note}" if plug_note and gap > 0 else ""),
        ),
        bs(
            "retail_deposits_stable",
            deposit_split.retail_stable,
            "liability",
            "retail DEPOSIT positions × DEPOSIT_STABILITY",
        ),
        bs(
            "retail_deposits_less_stable",
            deposit_split.retail_less_stable,
            "liability",
            "retail DEPOSIT positions × (1 − DEPOSIT_STABILITY)",
        ),
        bs(
            "wholesale_operational",
            deposit_split.wholesale_operational,
            "liability",
            "wholesale current DEPOSIT positions × operational share",
        ),
        bs(
            "wholesale_non_op_sme",
            deposit_split.wholesale_non_op_sme,
            "liability",
            "wholesale current DEPOSIT positions × non-operational share",
        ),
        bs(
            "wholesale_non_op_corporate",
            deposit_split.wholesale_non_op_corporate,
            "liability",
            "wholesale term DEPOSIT positions",
        ),
        bs("secured_funding_l1", secured_funding, "liability", "INTERBANK_BORROWING ≤ 1y"),
        bs(
            "term_borrowings_gt_1y",
            term_borrowings,
            "liability",
            "INTERBANK_BORROWING > 1y" + (f"; {plug_note}" if plug_note and gap < 0 else ""),
        ),
        bs("capital_total", capital_total, "equity", "capital_structure Σ signed amounts"),
    ]
    groups.append(
        GroupResult(group="balance_sheet", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs, loan_rows, cash, (bills, bonds)


def _is_bill(row: _PositionRow, as_of: date) -> bool:
    code = (row.product_code or "").upper()
    if "TBILL" in code or "BILL" in code:
        return True
    if "BOND" in code:
        return False
    if row.contractual_maturity is None:
        return False
    return (row.contractual_maturity - as_of).days <= _BILL_MAX_REMAINING_DAYS


# ---------------------------------------------------------------------------
# loan_exposure / securities / off_balance / lcr_inflow
# ---------------------------------------------------------------------------


def _derive_loan_exposure(loan_rows: list[_LoanRow], groups: list[GroupResult]) -> list[_FactSpec]:
    totals: dict[str, tuple[Decimal, str]] = {}
    for loan in loan_rows:
        amount, code = totals.get(loan.category, (_ZERO, loan.risk_weight_code))
        totals[loan.category] = (amount + loan.row.balance_ghs, code)
    specs = [
        _FactSpec(
            fact_group="loan_exposure",
            category=category,
            amount=amount,
            risk_weight_code=code,
            derived_from="LOAN positions by product regulatory category and IFRS 9 stage",
        )
        for category, (amount, code) in sorted(totals.items())
    ]
    groups.append(GroupResult(group="loan_exposure", status="derived", rows=len(specs)))
    return specs


def _derive_securities(
    securities_split: tuple[Decimal, Decimal],
    cash_amounts: dict[str, Decimal],
    groups: list[GroupResult],
) -> list[_FactSpec]:
    bills, bonds = securities_split
    specs = [
        _FactSpec(
            fact_group="securities",
            category="bog_bills",
            amount=bills,
            hqla_level="L1",
            risk_weight_code="RW0",
            derived_from="SECURITY_HOLDING positions (bills)",
        ),
        _FactSpec(
            fact_group="securities",
            category="gog_bonds",
            amount=bonds,
            hqla_level="L1",
            risk_weight_code="RW0",
            derived_from="SECURITY_HOLDING positions (bonds)",
        ),
        # Cash-derived HQLA mirrors: the liquidity engine recognizes these via
        # attributes.source == "cash" (stress haircuts skip them), so the
        # engine contract wins over the data_engine source tag here.
        _FactSpec(
            fact_group="securities",
            category="cash_vault_hqla",
            amount=cash_amounts["cash_vault"],
            hqla_level="L1",
            risk_weight_code="RW0",
            derived_from="mirror of balance_sheet cash_vault",
            source_tag="cash",
        ),
        _FactSpec(
            fact_group="securities",
            category="bog_excess_reserves_hqla",
            amount=cash_amounts["bog_excess_reserves"],
            hqla_level="L1",
            risk_weight_code="RW0",
            derived_from="mirror of balance_sheet bog_excess_reserves",
            source_tag="cash",
        ),
    ]
    groups.append(GroupResult(group="securities", status="derived", rows=len(specs)))
    return specs


def _derive_off_balance(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    warnings: list[str] = []
    totals: dict[str, tuple[Decimal, Decimal]] = {}  # category -> (Σ notional, Σ notional×ccf)
    missing_ccf = 0
    for row in canonical.by_type("LC_GUARANTEE"):
        notional = row.notional_ghs if row.notional_ghs > _ZERO else row.balance_ghs
        category = (
            "committed_retail"
            if row.counterparty_type == "RETAIL_INDIVIDUAL"
            else "committed_corporate"
        )
        ccf_pct = row.ccf * _HUNDRED if row.ccf is not None else None
        if ccf_pct is None:
            missing_ccf += 1
            ccf_pct = _DEFAULT_CCF_PCT
        amount, weighted = totals.get(category, (_ZERO, _ZERO))
        totals[category] = (amount + notional, weighted + notional * ccf_pct)
    if missing_ccf:
        warnings.append(
            f"{missing_ccf} off-balance positions carried no credit_conversion_factor; "
            f"the {_DEFAULT_CCF_PCT}% default CCF was applied."
        )
    specs = [
        _FactSpec(
            fact_group="off_balance",
            category=category,
            amount=amount,
            ccf_pct=(weighted / amount).quantize(RATE) if amount > 0 else _DEFAULT_CCF_PCT,
            risk_weight_code="RW75" if category == "committed_retail" else "RW100",
            derived_from="LC_GUARANTEE positions Σ notional_ghs; CCF is the "
            "EAD-preserving weighted average of position CCFs",
        )
        for category, (amount, weighted) in sorted(totals.items())
    ]
    if not specs:
        groups.append(
            GroupResult(
                group="off_balance",
                status="skipped",
                note="No LC/guarantee positions exist at this as-of date.",
            )
        )
        return []
    groups.append(
        GroupResult(group="off_balance", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


def _derive_lcr_inflows(  # noqa: PLR0912
    canonical: _Canonical, loan_rows: list[_LoanRow], groups: list[GroupResult]
) -> list[_FactSpec]:
    warnings: list[str] = []
    window_end = canonical.as_of + timedelta(days=_LCR_WINDOW_DAYS)
    any_loan_maturity = any(loan.row.contractual_maturity is not None for loan in loan_rows)

    retail = _ZERO
    corporate = _ZERO
    if any_loan_maturity:
        for loan in loan_rows:
            maturity = loan.row.contractual_maturity
            if maturity is None or maturity > window_end:
                continue
            if loan.category in _RETAIL_LOAN_CATEGORIES:
                retail += loan.row.balance_ghs
            else:
                corporate += loan.row.balance_ghs
        derived_from = "LOAN positions maturing within 30 days"
    else:
        for loan in loan_rows:
            if loan.category in _RETAIL_LOAN_CATEGORIES:
                retail += loan.row.balance_ghs * _LCR_FALLBACK_FRACTION
            else:
                corporate += loan.row.balance_ghs * _LCR_FALLBACK_FRACTION
        derived_from = (
            f"documented fallback: {_LCR_FALLBACK_FRACTION * _HUNDRED}% of gross loan "
            "balances (no contractual maturities available)"
        )
        warnings.append(
            "No loan positions carry contractual maturities; 30-day loan repayment "
            f"inflows use the documented {_LCR_FALLBACK_FRACTION * _HUNDRED}% fallback."
        )

    # Expected 30-day prepaid principal (loan-prepayment ML model). Folded into
    # the existing repayment categories so it flows through both the live LCR and
    # the 5-year forecast (which scales lcr inflows by loan growth) with no new
    # category or engine change. Empty until a PREPAYMENT_RATE batch is applied.
    prepay_rates = _prepayment_rate_by_product(canonical)
    prepaid_total = _ZERO
    if prepay_rates:
        for loan in loan_rows:
            cpr = prepay_rates.get(loan.row.product_code or "")
            if cpr is None:
                continue
            expected = _expected_prepaid_30d(loan.row.balance_ghs, cpr)
            prepaid_total += expected
            if loan.category in _RETAIL_LOAN_CATEGORIES:
                retail += expected
            else:
                corporate += expected
        if prepaid_total > _ZERO:
            derived_from += (
                f"; +{money(prepaid_total)} {canonical.base_currency} expected 30-day "
                "prepayment (PREPAYMENT_RATE model)"
            )

    interbank = sum(
        (
            row.balance_ghs
            for row in canonical.by_type("INTERBANK_PLACEMENT")
            if row.contractual_maturity is not None and row.contractual_maturity <= window_end
        ),
        _ZERO,
    )
    specs = [
        _FactSpec(
            fact_group="lcr_inflow",
            category="retail_loan_repayments",
            amount=retail,
            rate_pct=Decimal("50"),
            derived_from=derived_from,
        ),
        _FactSpec(
            fact_group="lcr_inflow",
            category="corporate_sme_repayments",
            amount=corporate,
            rate_pct=Decimal("50"),
            derived_from=derived_from,
        ),
        _FactSpec(
            fact_group="lcr_inflow",
            category="interbank_maturing",
            amount=interbank,
            rate_pct=Decimal("100"),
            derived_from="INTERBANK_PLACEMENT positions maturing within 30 days",
        ),
    ]
    groups.append(
        GroupResult(group="lcr_inflow", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------

_FX_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT")
_FX_LIABILITY_TYPES = ("DEPOSIT", "INTERBANK_BORROWING")


def _spot_rates(canonical: _Canonical) -> dict[str, Decimal]:
    spots: dict[str, Decimal] = {}
    for payload in canonical.refs.get("fx_rates_current", ()):
        currency = str(payload.get("currency", "")).strip().upper()
        rate = _dec_or_none(payload.get("spot_rate"))
        if currency and rate is not None:
            spots[currency] = rate
    # Canonical market data wins per currency; reference rows fill the rest.
    spots.update(canonical.market_spots)
    return spots


def _historical_currencies(canonical: _Canonical) -> set[str]:
    legacy = {
        str(payload.get("currency", "")).strip().upper()
        for payload in canonical.refs.get("fx_rates_historical", ())
        if payload.get("currency")
    }
    return legacy | set(canonical.market_fx_history)


def _fx_hedge_deltas(canonical: _Canonical, warnings: list[str]) -> dict[str, Decimal]:
    """Signed per-currency notional deltas from the FX_HEDGE book.

    Convention (documented in the mapping template): a hedge's ``balance`` is
    its notional in the SELL currency; the sell leg subtracts that notional
    from the sold currency's net, the buy leg adds ``notional × contract_rate``
    (buy-currency units per sell-currency unit) to the bought currency's net.
    GHS legs are ignored — GHS is the base currency, so only foreign-currency
    exposure moves.
    """
    deltas: dict[str, Decimal] = {}
    for row in canonical.by_type("FX_HEDGE"):
        attributes = row.attributes
        hedge_id = str(attributes.get("hedge_id") or row.source_reference)
        sell = str(attributes.get("sell_currency") or row.currency).strip().upper()
        buy = str(attributes.get("buy_currency") or canonical.base_currency).strip().upper()
        notional = abs(row.balance)
        if sell != canonical.base_currency:
            deltas[sell] = deltas.get(sell, _ZERO) - notional
        if buy != canonical.base_currency:
            rate = _dec_or_none(attributes.get("contract_rate"))
            if rate is None or rate <= _ZERO:
                warnings.append(
                    f"Hedge {hedge_id}: the {buy} buy leg carries no positive "
                    "contract_rate to convert the sell-leg notional; the buy leg "
                    "was excluded from the FX nets."
                )
            else:
                deltas[buy] = deltas.get(buy, _ZERO) + notional * rate
    return deltas


def _derive_fx_positions(
    canonical: _Canonical, groups: list[GroupResult]
) -> tuple[list[_FactSpec], set[str]]:
    warnings: list[str] = []
    spots = _spot_rates(canonical)
    with_history = _historical_currencies(canonical)

    assets_ccy: dict[str, Decimal] = {}
    liabilities_ccy: dict[str, Decimal] = {}
    assets_ghs: dict[str, Decimal] = {}
    liabilities_ghs: dict[str, Decimal] = {}
    for row in canonical.positions:
        if row.currency == canonical.base_currency or row.position_type == "LC_GUARANTEE":
            continue
        if row.position_type in _FX_ASSET_TYPES:
            assets_ccy[row.currency] = assets_ccy.get(row.currency, _ZERO) + row.balance
            assets_ghs[row.currency] = assets_ghs.get(row.currency, _ZERO) + row.balance_ghs
        elif row.position_type in _FX_LIABILITY_TYPES:
            liabilities_ccy[row.currency] = liabilities_ccy.get(row.currency, _ZERO) + row.balance
            liabilities_ghs[row.currency] = (
                liabilities_ghs.get(row.currency, _ZERO) + row.balance_ghs
            )
    hedge_deltas = _fx_hedge_deltas(canonical, warnings)

    currencies = sorted(set(assets_ccy) | set(liabilities_ccy) | set(hedge_deltas))
    specs: list[_FactSpec] = []
    included: set[str] = set()
    net_long = _ZERO
    net_short = _ZERO
    for currency in currencies:
        if currency not in with_history:
            warnings.append(
                f"{currency} positions were excluded from the FX book: no daily return "
                "history was ingested for this currency (VaR requires one)."
            )
            continue
        base_ccy = assets_ccy.get(currency, _ZERO) - liabilities_ccy.get(currency, _ZERO)
        base_ghs = assets_ghs.get(currency, _ZERO) - liabilities_ghs.get(currency, _ZERO)
        # The spot resolves from the on-balance book before hedge deltas apply,
        # so an implied fallback rate stays consistent with the position data.
        spot = _resolve_spot(currency, spots.get(currency), base_ccy, base_ghs, warnings)
        delta = hedge_deltas.get(currency, _ZERO)
        net_ccy = base_ccy + delta
        net_ghs = base_ghs + delta * spot
        included.add(currency)
        if net_ghs >= _ZERO:
            net_long += net_ghs
        else:
            net_short += -net_ghs
        specs.append(
            _FactSpec(
                fact_group="fx_position",
                category=currency,
                amount=net_ghs,
                derived_from="per-currency net of position balance_ghs "
                "(assets − liabilities + signed FX_HEDGE notional deltas; "
                "LC/guarantees excluded as off-balance)",
                attributes={
                    "currency": currency,
                    "side": "long" if net_ghs >= _ZERO else "short",
                    "spot_ghs": str(spot),
                    "net_ccy": str(money(net_ccy)),
                    "assets_ccy": str(money(assets_ccy.get(currency, _ZERO))),
                    "liabilities_ccy": str(money(liabilities_ccy.get(currency, _ZERO))),
                    "net_derivatives_ccy": str(money(delta)),
                    "net_ghs": str(money(net_ghs)),
                },
            )
        )

    market_specs = [
        _FactSpec(
            fact_group="market_risk",
            category="net_long_fx",
            amount=net_long,
            derived_from="Σ long per-currency FX nets",
        ),
        _FactSpec(
            fact_group="market_risk",
            category="net_short_fx",
            amount=net_short,
            derived_from="|Σ short per-currency FX nets|",
        ),
    ]
    groups.append(GroupResult(group="market_risk", status="derived", rows=len(market_specs)))
    if specs:
        groups.append(
            GroupResult(group="fx_position", status="derived", rows=len(specs), warnings=warnings)
        )
    else:
        groups.append(
            GroupResult(
                group="fx_position",
                status="skipped",
                warnings=warnings,
                note="No foreign-currency positions with return histories exist; the FX module "
                "will report no open positions.",
            )
        )
    return market_specs + specs, included


def _resolve_spot(
    currency: str,
    spot: Decimal | None,
    net_ccy: Decimal,
    net_ghs: Decimal,
    warnings: list[str],
) -> Decimal:
    if spot is not None:
        return spot
    if net_ccy != _ZERO:
        implied = (net_ghs / net_ccy).quantize(Decimal("0.000001"))
        warnings.append(
            f"No current spot rate was ingested for {currency}; the implied rate "
            f"{implied} from the position book was used."
        )
        return implied
    return _ONE


def _derive_fx_returns(
    canonical: _Canonical, currencies: set[str], groups: list[GroupResult]
) -> list[_FactSpec]:
    series: dict[str, list[tuple[str, Decimal]]] = {}
    for payload in canonical.refs.get("fx_rates_historical", ()):
        currency = str(payload.get("currency", "")).strip().upper()
        rate = _dec_or_none(payload.get("spot_rate"))
        day = str(payload.get("date", ""))
        if currency and rate is not None and rate > _ZERO and day:
            series.setdefault(currency, []).append((day, rate))
    # A canonical spot history deep enough for VaR replaces the legacy
    # reference-row history for that currency (persisted spot pulls, §5.2).
    for currency, history in canonical.market_fx_history.items():
        series[currency] = [(day.isoformat(), rate) for day, rate in history if rate > _ZERO]

    del currencies  # histories derive for every currency; the engine ignores extras
    specs: list[_FactSpec] = []
    for currency in sorted(series):
        points = sorted(series[currency])
        returns: list[float] = []
        for (_, previous), (_, current) in zip(points, points[1:], strict=False):
            returns.append(round(float(current / previous - _ONE), 6))
        returns = returns[-_FX_RETURN_WINDOW:]
        if not returns:
            continue
        source_dataset = (
            "canonical market data spot history"
            if currency in canonical.market_fx_history
            else "fx_rates_historical"
        )
        specs.append(
            _FactSpec(
                fact_group="fx_return_history",
                category=currency,
                amount=Decimal(len(returns)),
                derived_from="daily simple returns S_t/S_(t-1) − 1 from "
                f"{source_dataset} (most recent 250)",
                attributes={"currency": currency, "returns": returns},
            )
        )
    if specs:
        groups.append(GroupResult(group="fx_return_history", status="derived", rows=len(specs)))
    else:
        groups.append(
            GroupResult(
                group="fx_return_history",
                status="skipped",
                note="No historical FX rates were ingested.",
            )
        )
    return specs


# The FX engine's hedge vocabulary and the synonyms sources commonly use.
_HEDGE_INSTRUMENTS = ("forward", "cross_currency_swap", "option")
_HEDGE_INSTRUMENT_SYNONYMS = {
    "fx_forward": "forward",
    "fwd": "forward",
    "ndf": "forward",
    "ccs": "cross_currency_swap",
    "cross_currency": "cross_currency_swap",
    "currency_swap": "cross_currency_swap",
    "fx_option": "option",
    "currency_option": "option",
}


def _hedge_instrument(raw: Any, hedge_id: str, warnings: list[str]) -> str:
    slug = str(raw or "forward").strip().lower().replace(" ", "_").replace("-", "_")
    slug = _HEDGE_INSTRUMENT_SYNONYMS.get(slug, slug)
    if slug not in _HEDGE_INSTRUMENTS:
        warnings.append(
            f"Hedge {hedge_id}: instrument {str(raw)!r} is outside the engine vocabulary "
            f"({', '.join(_HEDGE_INSTRUMENTS)}); carried through as {slug!r}."
        )
    return slug


def _derive_fx_hedges(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    rows = canonical.by_type("FX_HEDGE")
    if not rows:
        groups.append(
            GroupResult(
                group="fx_hedge",
                status="skipped",
                note="No FX hedge positions exist at this as-of date; the FX module "
                "reports an empty hedge book.",
            )
        )
        return []

    warnings: list[str] = []
    specs: list[_FactSpec] = []
    used_categories: set[str] = set()
    missing_effectiveness = 0
    for row in sorted(rows, key=lambda item: item.source_reference):
        attributes = row.attributes
        hedge_id = str(attributes.get("hedge_id") or row.source_reference)
        category = hedge_id if hedge_id not in used_categories else row.source_reference
        used_categories.add(category)
        instrument = _hedge_instrument(attributes.get("instrument"), hedge_id, warnings)
        pair = str(attributes.get("currency_pair") or f"{row.currency}/GHS").strip().upper()
        rate = _dec_or_none(attributes.get("contract_rate")) or _ZERO
        mtm = _dec(attributes.get("mtm_ghs"), _ZERO)
        r2 = _dec_or_none(attributes.get("prospective_r2"))
        offset = _dec_or_none(attributes.get("dollar_offset_ratio"))
        if r2 is None or offset is None:
            missing_effectiveness += 1
            r2 = r2 if r2 is not None else _ZERO
            offset = offset if offset is not None else _ZERO
        maturity_days = 0
        if row.contractual_maturity is not None:
            maturity_days = max((row.contractual_maturity - canonical.as_of).days, 0)
        specs.append(
            _FactSpec(
                fact_group="fx_hedge",
                category=category,
                amount=mtm,
                derived_from="FX_HEDGE position: sell-leg notional with IFRS 9 "
                "effectiveness measures; amount is the hedge MtM in GHS",
                attributes={
                    "hedge_id": hedge_id,
                    "instrument": instrument,
                    "pair": pair,
                    "notional_ccy": str(money(abs(row.balance))),
                    "rate": str(rate),
                    "maturity_days": str(maturity_days),
                    "mtm_ghs": str(money(mtm)),
                    "prospective_r2": str(r2),
                    "dollar_offset_ratio": str(offset),
                },
            )
        )
    if missing_effectiveness:
        warnings.append(
            f"{missing_effectiveness} FX hedges carried no prospective_r2 or "
            "dollar_offset_ratio; the missing measures defaulted to 0 "
            "(conservatively ineffective)."
        )
    groups.append(
        GroupResult(group="fx_hedge", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


# ---------------------------------------------------------------------------
# operational_income / capital_component
# ---------------------------------------------------------------------------


def _derive_operational_income(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    warnings: list[str] = []
    months: list[tuple[date, Decimal]] = []
    for payload in canonical.refs.get("historical_financials", ()):
        period_end = str(payload.get("period_end", ""))
        nii = _dec_or_none(payload.get("net_interest_income_ghs"))
        fees = _dec_or_none(payload.get("non_interest_income_ghs"))
        if not period_end or nii is None:
            continue
        months.append((date.fromisoformat(period_end), nii + (fees or _ZERO)))
    months.sort()

    specs: list[_FactSpec] = []
    remaining = months
    for _ in range(3):
        if len(remaining) < 12:
            break
        window = remaining[-12:]
        remaining = remaining[:-12]
        year = window[-1][0].year
        gross = sum((amount for _, amount in window), _ZERO)
        specs.append(
            _FactSpec(
                fact_group="operational_income",
                category=f"gross_income_{year}",
                amount=gross,
                income_year=year,
                derived_from="trailing 12-month gross income (net interest + "
                "non-interest) from historical_financials",
            )
        )
    specs.reverse()
    if not specs:
        groups.append(
            GroupResult(
                group="operational_income",
                status="skipped",
                note="No monthly historical financials were ingested; the capital "
                "module's BIA charge cannot be derived.",
                warnings=[
                    "operational_income could not be derived — capital runs will fail "
                    "until historical financials are ingested."
                ],
            )
        )
        return []
    if len(specs) < 3:
        warnings.append(
            f"Only {len(specs)} full trailing 12-month income windows were available; "
            "the BIA average uses fewer than three years."
        )
    groups.append(
        GroupResult(
            group="operational_income", status="derived", rows=len(specs), warnings=warnings
        )
    )
    return specs


def _capital_tier(raw_tier: str) -> tuple[str, bool]:
    tier = raw_tier.strip().upper()
    is_deduction = tier.endswith("_DEDUCTION")
    tier = tier.removesuffix("_DEDUCTION")
    if tier in ("TIER2", "T2"):
        return "T2", is_deduction
    if tier == "AT1":
        return "AT1", is_deduction
    return "CET1", is_deduction


def _derive_capital_components(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    totals: dict[str, tuple[Decimal, str, bool]] = {}
    for payload in canonical.refs.get("capital_structure", ()):
        component = str(payload.get("capital_component", "")).strip()
        amount = _dec_or_none(payload.get("amount_ghs"))
        if not component or amount is None:
            continue
        tier, is_deduction = _capital_tier(str(payload.get("tier", "CET1")))
        if amount < _ZERO:
            is_deduction = True
        category = component.lower()
        previous, _, _ = totals.get(category, (_ZERO, tier, is_deduction))
        totals[category] = (previous + abs(amount), tier, is_deduction)
    specs = [
        _FactSpec(
            fact_group="capital_component",
            category=category,
            amount=amount,
            capital_tier=tier,
            is_deduction=is_deduction,
            derived_from="capital_structure reference row",
        )
        for category, (amount, tier, is_deduction) in sorted(totals.items())
    ]
    if not specs:
        groups.append(
            GroupResult(
                group="capital_component",
                status="skipped",
                note="No capital_structure reference rows were ingested; capital, IRR "
                "and FX runs will fail without Tier 1 capital.",
            )
        )
        return []
    groups.append(GroupResult(group="capital_component", status="derived", rows=len(specs)))
    return specs


# ---------------------------------------------------------------------------
# IRR
# ---------------------------------------------------------------------------


def _bucket_for_days(days: int) -> str:
    for name, upper, _ in _IRR_BUCKETS:
        if upper is None or days <= upper:
            return name
    return _IRR_BUCKETS[-1][0]  # pragma: no cover - the 5y+ bucket is unbounded


def _repricing_bucket(row: _PositionRow, as_of: date) -> str | None:
    horizon: date | None
    if row.rate_type == "FLOATING" and row.next_repricing_date is not None:
        horizon = row.next_repricing_date
    else:
        horizon = row.contractual_maturity or row.next_repricing_date
    if horizon is None:
        return None
    return _bucket_for_days(max((horizon - as_of).days, 0))


@dataclass
class _IrrCell:
    balance: Decimal = _ZERO
    weighted_rate: Decimal = _ZERO
    fixed_balance: Decimal = _ZERO

    def add(self, balance: Decimal, rate: Decimal, is_fixed: bool) -> None:
        self.balance += balance
        self.weighted_rate += balance * rate
        if is_fixed:
            self.fixed_balance += balance


def _derive_irr_positions(
    canonical: _Canonical,
    loan_rows: list[_LoanRow],
    groups: list[GroupResult],
) -> list[_FactSpec]:
    warnings: list[str] = []
    durations = _nmd_duration_months(canonical)
    cells: dict[tuple[str, str, str], _IrrCell] = {}
    excluded_core = _ZERO

    def add(side: str, family: str, bucket: str, row: _PositionRow) -> None:
        rate = (row.interest_rate or _ZERO) * _HUNDRED
        is_fixed = row.rate_type != "FLOATING"
        cells.setdefault((side, family, bucket), _IrrCell()).add(row.balance_ghs, rate, is_fixed)

    for loan in loan_rows:
        bucket = _repricing_bucket(loan.row, canonical.as_of) or _IRR_BUCKETS[-1][0]
        add("asset", _LOAN_FAMILY[loan.category], bucket, loan.row)
    for row in canonical.by_type("SECURITY_HOLDING"):
        bucket = _repricing_bucket(row, canonical.as_of) or _IRR_BUCKETS[-1][0]
        add("asset", "securities", bucket, row)
    for row in canonical.by_type("INTERBANK_PLACEMENT"):
        bucket = _repricing_bucket(row, canonical.as_of) or "overnight"
        add("asset", "interbank_placements", bucket, row)

    for row in canonical.by_type("DEPOSIT"):
        placement = _deposit_irr_placement(row, canonical.as_of, durations)
        if placement is None:
            excluded_core += row.balance_ghs  # zero-rate NMD core: non-rate-sensitive
            continue
        family, bucket = placement
        add("liability", family, bucket, row)
    for row in canonical.by_type("INTERBANK_BORROWING"):
        bucket = _repricing_bucket(row, canonical.as_of) or "overnight"
        add("liability", "interbank_borrowings", bucket, row)

    # Subordinated debt prices as a long fixed liability at the ingested curve's
    # long end (the canonical model carries no instrument-level terms for it).
    sub_debt = sum(
        (
            _dec(payload.get("amount_ghs"), _ZERO)
            for payload in canonical.refs.get("capital_structure", ())
            if "SUBORDINATED" in str(payload.get("capital_component", "")).upper()
        ),
        _ZERO,
    )
    if sub_debt > _ZERO:
        long_rate = _long_curve_rate(canonical)
        cells.setdefault(("liability", "subordinated_debt", "5y+"), _IrrCell()).add(
            sub_debt, long_rate, True
        )
    if excluded_core > _ZERO:
        warnings.append(
            f"{money(excluded_core)} {canonical.base_currency} of zero-rate non-maturity "
            "deposits were excluded from the rate-sensitive book as the behavioral core."
        )

    specs: list[_FactSpec] = []
    for (side, family, bucket), cell in sorted(cells.items()):
        if cell.balance <= _ZERO:
            continue
        rate_pct = (cell.weighted_rate / cell.balance).quantize(RATE)
        fixed_or_float = "fixed" if cell.fixed_balance * 2 >= cell.balance else "float"
        slug = bucket.replace("-", "_").replace("+", "plus")
        specs.append(
            _FactSpec(
                fact_group="irr_position",
                category=f"{family}_{slug}",
                amount=cell.balance,
                derived_from="positions aggregated by repricing bucket "
                "(float → next repricing, fixed → maturity, NMD → behavioral)",
                attributes={
                    "side": side,
                    "bucket": bucket,
                    "fixed_or_float": fixed_or_float,
                    "rate_pct": str(rate_pct),
                    "midpoint_years": _BUCKET_MIDPOINT[bucket],
                },
            )
        )
    groups.append(
        GroupResult(group="irr_position", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


def _deposit_irr_placement(
    row: _PositionRow, as_of: date, durations: dict[str, Decimal]
) -> tuple[str, str] | None:
    """(family, bucket) for one deposit, or None for the non-rate-sensitive core."""
    bucket = _repricing_bucket(row, as_of)
    if bucket is not None:
        family = (
            "term_deposits_retail" if _is_retail_deposit_product(row) else "term_deposits_wholesale"
        )
        return family, bucket
    if (row.interest_rate or _ZERO) <= _ZERO:
        return None
    if _is_retail_deposit_product(row):
        return "savings_repricing", _SAVINGS_REPRICING_BUCKET
    months = durations.get(row.product_code or "")
    bucket = _bucket_for_days(int(months * Decimal("30.44"))) if months is not None else "overnight"
    return "wholesale_call", bucket


_INDEX_RESET = re.compile(r"^(\d+)\s*([dmy])")
_DEFAULT_INDEX_RESET_DAYS = 91  # the 91-day T-Bill, Ghana's standard floating index
_DAYS_PER_MONTH = Decimal("30.44")


def _index_reset_days(receive_index: str) -> int:
    """The floating leg's reset tenor in days, parsed from the index name.

    ``91d_tbill`` → 91, ``6m_libor`` → 182; anything unparseable falls back to
    the 91-day T-Bill reset.
    """
    match = _INDEX_RESET.match(receive_index.strip().lower())
    if match is None:
        return _DEFAULT_INDEX_RESET_DAYS
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return int(Decimal(value) * _DAYS_PER_MONTH)
    if unit == "y":
        return value * 365
    return value


def _derive_irr_swaps(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    rows = canonical.by_type("INTEREST_RATE_SWAP")
    if not rows:
        groups.append(
            GroupResult(
                group="irr_swap",
                status="skipped",
                note="No interest-rate swap positions exist at this as-of date; IRR "
                "runs on the unhedged repricing book.",
            )
        )
        return []

    warnings: list[str] = []
    specs: list[_FactSpec] = []
    used_categories: set[str] = set()
    for row in sorted(rows, key=lambda item: item.source_reference):
        attributes = row.attributes
        swap_id = str(attributes.get("swap_id") or row.source_reference)
        direction = str(attributes.get("direction") or "pay_fixed").strip().lower()
        if direction not in ("pay_fixed", "receive_fixed"):
            warnings.append(
                f"Swap {swap_id}: direction {direction!r} is not supported (the IRR "
                "engine decomposes pay-fixed and receive-fixed swaps only); the swap "
                "was excluded."
            )
            continue
        notional = row.notional_ghs if row.notional_ghs > _ZERO else row.balance_ghs
        if notional <= _ZERO:
            warnings.append(
                f"Swap {swap_id}: no positive {canonical.base_currency} notional; "
                "the swap was excluded."
            )
            continue
        pay_rate = _dec_or_none(attributes.get("pay_rate_pct"))
        if pay_rate is None and row.interest_rate is not None:
            pay_rate = row.interest_rate * _HUNDRED
        if pay_rate is None:
            warnings.append(f"Swap {swap_id}: no pay_rate_pct; the swap was excluded.")
            continue
        receive_index = str(attributes.get("receive_index") or "91d_tbill").strip().lower()
        floating_bucket = _bucket_for_days(_index_reset_days(receive_index))
        if row.contractual_maturity is not None:
            remaining_days = max((row.contractual_maturity - canonical.as_of).days, 0)
        else:
            tenor = _dec(attributes.get("tenor_years"), _ZERO)
            remaining_days = int(tenor * Decimal("365"))
        fixed_bucket = _bucket_for_days(remaining_days)
        tenor_years = _dec_or_none(attributes.get("tenor_years"))
        if tenor_years is None:
            tenor_years = (Decimal(remaining_days) / Decimal("365")).quantize(Decimal("0.01"))
        # receive_bucket/pay_bucket locate the legs the bank receives/pays:
        # a pay-fixed swap receives the floating leg (index reset bucket) and
        # pays the fixed leg (remaining-maturity bucket); receive-fixed swaps
        # are the mirror image.
        if direction == "pay_fixed":
            receive_bucket, pay_bucket = floating_bucket, fixed_bucket
            derived_from = (
                "INTEREST_RATE_SWAP position: pay-fixed swap decomposed by the IRR "
                "engine into a floating receive leg (index reset bucket) and a fixed "
                "pay leg (remaining-maturity bucket)"
            )
        else:
            receive_bucket, pay_bucket = fixed_bucket, floating_bucket
            derived_from = (
                "INTEREST_RATE_SWAP position: receive-fixed swap decomposed by the IRR "
                "engine into a fixed receive leg (remaining-maturity bucket) and a "
                "floating pay leg (index reset bucket)"
            )
        category = swap_id if swap_id not in used_categories else row.source_reference
        used_categories.add(category)
        specs.append(
            _FactSpec(
                fact_group="irr_swap",
                category=category,
                amount=notional,
                derived_from=derived_from,
                attributes={
                    "notional": str(money(notional)),
                    "pay_rate_pct": str(pay_rate),
                    "receive_index": receive_index,
                    "tenor_years": str(tenor_years),
                    "direction": direction,
                    "receive_bucket": receive_bucket,
                    "receive_midpoint_years": _BUCKET_MIDPOINT[receive_bucket],
                    "pay_bucket": pay_bucket,
                    "pay_midpoint_years": _BUCKET_MIDPOINT[pay_bucket],
                },
            )
        )
    if not specs:
        groups.append(
            GroupResult(
                group="irr_swap",
                status="skipped",
                warnings=warnings,
                note="No supported interest-rate swaps could be derived; IRR runs on "
                "the unhedged repricing book.",
            )
        )
        return []
    groups.append(
        GroupResult(group="irr_swap", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


def _long_curve_rate(canonical: _Canonical) -> Decimal:
    if canonical.market_curve is not None and canonical.market_curve.points:
        # Points are sorted by tenor; the longest tenor's rate anchors the leg.
        return canonical.market_curve.points[-1][1] * _HUNDRED
    best_months = _ZERO
    best_rate = _ZERO
    for payload in canonical.refs.get("yield_curve", ()):
        if str(payload.get("currency", "")).strip().upper() not in ("", canonical.base_currency):
            continue
        months = _dec_or_none(payload.get("tenor_months"))
        rate = _dec_or_none(payload.get("rate"))
        if months is None or rate is None:
            continue
        if months > best_months:
            best_months = months
            best_rate = rate
    return best_rate * _HUNDRED


# ---------------------------------------------------------------------------
# FTP
# ---------------------------------------------------------------------------


def _step_schedule(
    tenor_years: Decimal, steps: tuple[tuple[Decimal, Decimal], ...], cap: Decimal
) -> Decimal:
    for upper, value in steps:
        if tenor_years < upper:
            return value
    return cap


def _tenor_label(months: Decimal) -> str:
    if months % _TWELVE == 0:
        return f"{int(months / _TWELVE)}y"
    return f"{int(months)}m"


def _derive_ftp_curve(
    canonical: _Canonical, groups: list[GroupResult]
) -> tuple[list[_FactSpec], CurveResult | None]:
    by_months: dict[Decimal, Decimal] = {}
    curve_warnings: list[str] = []
    market_curve = canonical.market_curve
    if market_curve is not None and market_curve.points:
        # Canonical market data wins over the legacy yield_curve reference rows.
        for tenor_months, rate in market_curve.points:
            by_months[Decimal(tenor_months)] = rate * _HUNDRED
        base_source = (
            f"canonical {canonical.base_currency} market yield curve {market_curve.curve_name} "
            f"({market_curve.attribution.source_system})"
        )
        if market_curve.attribution.stale:
            # Stale data is usable but never silent (§15): attribute it.
            curve_warnings.append(
                f"The canonical {canonical.base_currency} yield curve is stale "
                f"(ingested {market_curve.attribution.ingested_at.isoformat()}); "
                "FTP curve points were derived from stale market data."
            )
    else:
        base_source = f"ingested {canonical.base_currency} yield curve"
        for payload in canonical.refs.get("yield_curve", ()):
            currency = str(payload.get("currency", "")).strip().upper()
            curve_name = str(payload.get("curve_name", "")).upper()
            if currency != canonical.base_currency and canonical.base_currency not in curve_name:
                continue
            months = _dec_or_none(payload.get("tenor_months"))
            rate = _dec_or_none(payload.get("rate"))
            if months is None or rate is None or months <= _ZERO:
                continue
            by_months[months] = rate * _HUNDRED  # last row wins on duplicate tenors
    points = sorted(by_months.items())
    if not points:
        groups.append(
            GroupResult(
                group="ftp_curve_point",
                status="skipped",
                note="No GHS yield curve was ingested; FTP runs will fail without a "
                "transfer curve.",
                warnings=["ftp_curve_point could not be derived — no GHS yield curve."],
            )
        )
        return [], None

    specs: list[_FactSpec] = []
    curve_points: list[CurvePoint] = []
    for months, base_pct in points:
        tenor_years = (months / _TWELVE).quantize(RATE)
        liquidity_bps = _step_schedule(
            tenor_years, _FTP_LIQUIDITY_PREMIUM_STEPS, _FTP_LIQUIDITY_PREMIUM_CAP
        )
        funding_bps = _step_schedule(
            tenor_years, _FTP_FUNDING_SPREAD_STEPS, _FTP_FUNDING_SPREAD_CAP
        )
        ftp_rate = base_pct + (liquidity_bps + funding_bps) / _HUNDRED
        label = _tenor_label(months)
        curve_points.append(
            CurvePoint(
                tenor_label=label,
                tenor_years=tenor_years,
                base_yield_pct=base_pct,
                liquidity_premium_bps=liquidity_bps,
                funding_spread_bps=funding_bps,
                ftp_rate_pct=ftp_rate,
            )
        )
        specs.append(
            _FactSpec(
                fact_group="ftp_curve_point",
                category=label,
                amount=ftp_rate,
                derived_from=f"{base_source} + documented liquidity-premium "
                "and funding-spread schedules",
                attributes={
                    "tenor_label": label,
                    "tenor_years": str(tenor_years),
                    "base_yield_pct": str(base_pct),
                    "liquidity_premium_bps": str(liquidity_bps),
                    "funding_spread_bps": str(funding_bps),
                    "ftp_rate_pct": str(ftp_rate),
                },
            )
        )
    curve = build_curve(curve_points)
    groups.append(
        GroupResult(
            group="ftp_curve_point",
            status="derived",
            rows=len(specs),
            warnings=curve_warnings,
        )
    )
    return specs, curve


@dataclass
class _FtpBook:
    balance: Decimal = _ZERO
    weighted_rate: Decimal = _ZERO
    weighted_tenor_days: Decimal = _ZERO
    tenor_weight: Decimal = _ZERO
    ecl: Decimal = _ZERO

    def add(self, row: _PositionRow, as_of: date) -> None:
        self.balance += row.balance_ghs
        self.weighted_rate += row.balance_ghs * (row.interest_rate or _ZERO) * _HUNDRED
        if row.contractual_maturity is not None:
            days = Decimal(max((row.contractual_maturity - as_of).days, 0))
            self.weighted_tenor_days += row.balance_ghs * days
            self.tenor_weight += row.balance_ghs
        self.ecl += row.ecl_ghs


def _deposit_ftp_segment(row: _PositionRow) -> str:
    is_term = row.contractual_maturity is not None
    if _is_retail_deposit_product(row):
        code = (row.product_code or "").upper()
        if is_term:
            return "term_deposits_retail"
        if "SAV" in code or "saving" in (row.product_code or "").lower():
            return "savings"
        return "current_accounts"
    return "term_deposits_wholesale" if is_term else "wholesale_current"


def _derive_ftp_products(
    canonical: _Canonical,
    loan_rows: list[_LoanRow],
    curve: CurveResult | None,
    groups: list[GroupResult],
) -> list[_FactSpec]:
    if curve is None:
        groups.append(
            GroupResult(
                group="ftp_product",
                status="skipped",
                note="No FTP curve could be derived; product pricing is impossible.",
            )
        )
        return []
    durations = _nmd_duration_months(canonical)
    books: dict[tuple[str, str], _FtpBook] = {}  # (category, product) -> book

    for loan in loan_rows:
        books.setdefault(("asset", _LOAN_FAMILY[loan.category]), _FtpBook()).add(
            loan.row, canonical.as_of
        )
    for row in canonical.by_type("SECURITY_HOLDING"):
        books.setdefault(("asset", "gov_securities"), _FtpBook()).add(row, canonical.as_of)
    for row in canonical.by_type("DEPOSIT"):
        books.setdefault(("liability", _deposit_ftp_segment(row)), _FtpBook()).add(
            row, canonical.as_of
        )

    nmd_default_tenor = {
        "current_accounts": durations.get("DEP.RET.CUR"),
        "savings": durations.get("DEP.RET.SAV"),
        "wholesale_current": durations.get("DEP.CORP.CUR"),
    }
    min_tenor = curve.points[0].tenor_years
    max_tenor = curve.points[-1].tenor_years

    specs: list[_FactSpec] = []
    for (category, product), book in sorted(books.items()):
        if book.balance <= _ZERO:
            continue
        if book.tenor_weight > _ZERO:
            tenor_years = (book.weighted_tenor_days / book.tenor_weight / Decimal("365")).quantize(
                RATE
            )
        else:
            months = nmd_default_tenor.get(product) or _DEFAULT_NMD_DURATION_MONTHS
            tenor_years = (months / _TWELVE).quantize(RATE)
        tenor_years = min(max(tenor_years, min_tenor), max_tenor)
        customer_rate = (book.weighted_rate / book.balance).quantize(RATE)
        ftp_rate = curve.rate_at(tenor_years)
        if category == "asset":
            opex = (
                _FTP_SECURITIES_OPEX_PCT
                if product == "gov_securities"
                else _FTP_ASSET_LOAN_OPEX_PCT
            )
            ecl_pct = (
                (book.ecl / book.balance * _HUNDRED).quantize(RATE)
                if product != "gov_securities"
                else _ZERO
            )
            capital_pct = _ZERO if product == "gov_securities" else _FTP_ASSET_LOAN_CAPITAL_PCT
            net_margin = customer_rate - ftp_rate - opex - ecl_pct - capital_pct
        else:
            opex = _FTP_LIABILITY_OPEX_PCT
            ecl_pct = _ZERO
            capital_pct = _ZERO
            net_margin = ftp_rate - customer_rate - opex
        specs.append(
            _FactSpec(
                fact_group="ftp_product",
                category=product,
                amount=book.balance,
                derived_from="positions grouped by product family; balance-weighted "
                "customer rate and remaining-maturity tenor; documented cost defaults",
                attributes={
                    "product": product,
                    "category": category,
                    "balance_ghs": str(money(book.balance)),
                    "tenor_years": str(tenor_years),
                    "customer_rate_pct": str(customer_rate),
                    "ftp_rate_pct": str(ftp_rate),
                    "operating_cost_pct": str(opex),
                    "expected_credit_loss_pct": str(ecl_pct),
                    "capital_charge_pct": str(capital_pct),
                    "net_margin_pct": str(net_margin),
                },
            )
        )
    groups.append(GroupResult(group="ftp_product", status="derived", rows=len(specs)))
    return specs


def _business_unit_names(canonical: _Canonical) -> dict[str, str]:
    names: dict[str, str] = {}
    for payload in canonical.refs.get("business_units", ()):
        unit_id = str(payload.get("business_unit_id", "")).strip()
        name = str(payload.get("business_unit_name", "")).strip()
        if unit_id and name:
            names[unit_id] = name.lower().replace(" ", "_")
    return names


def _derive_ftp_branches(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    names = _business_unit_names(canonical)
    deposits: dict[str, Decimal] = {}
    loans: dict[str, Decimal] = {}
    for row in canonical.positions:
        if row.branch_id is None:
            continue
        branch = names.get(row.branch_id, row.branch_id.lower().replace("-", "_"))
        if row.position_type == "DEPOSIT":
            deposits[branch] = deposits.get(branch, _ZERO) + row.balance_ghs
        elif row.position_type == "LOAN":
            loans[branch] = loans.get(branch, _ZERO) + row.balance_ghs
    branches = sorted(set(deposits) | set(loans))
    if not branches:
        groups.append(
            GroupResult(
                group="ftp_branch",
                status="skipped",
                note="No positions carry a branch identifier; branch profitability is unavailable.",
            )
        )
        return []
    specs = [
        _FactSpec(
            fact_group="ftp_branch",
            category=branch,
            amount=deposits.get(branch, _ZERO),
            derived_from="LOAN/DEPOSIT positions grouped by branch_id × business_units",
            attributes={
                "branch": branch,
                "deposits_ghs": str(money(deposits.get(branch, _ZERO))),
                "loans_ghs": str(money(loans.get(branch, _ZERO))),
            },
        )
        for branch in branches
    ]
    groups.append(GroupResult(group="ftp_branch", status="derived", rows=len(specs)))
    return specs


def _derive_ftp_nmd(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    warnings: list[str] = []
    stability = _stability_by_product(canonical)
    durations = _nmd_duration_months(canonical)

    @dataclass
    class _Segment:
        balance: Decimal = _ZERO
        weighted_core: Decimal = _ZERO
        weighted_duration: Decimal = _ZERO
        defaulted: bool = False

    segments: dict[str, _Segment] = {}
    for row in canonical.by_type("DEPOSIT"):
        if row.contractual_maturity is not None:
            continue  # term deposits are not NMDs
        segment_name = _deposit_ftp_segment(row)
        segment = segments.setdefault(segment_name, _Segment())
        share = stability.get(row.product_code or "")
        months = durations.get(row.product_code or "")
        if share is None:
            share = _DEFAULT_NMD_CORE_PCT / _HUNDRED
            segment.defaulted = True
        if months is None:
            months = _DEFAULT_NMD_DURATION_MONTHS
            segment.defaulted = True
        segment.balance += row.balance_ghs
        segment.weighted_core += row.balance_ghs * share * _HUNDRED
        segment.weighted_duration += row.balance_ghs * months / _TWELVE

    specs: list[_FactSpec] = []
    for name, segment in sorted(segments.items()):
        if segment.balance <= _ZERO:
            continue
        core_pct = (segment.weighted_core / segment.balance).quantize(RATE)
        duration = (segment.weighted_duration / segment.balance).quantize(RATE)
        if segment.defaulted:
            warnings.append(
                f"NMD segment '{name}' used documented defaults "
                f"({_DEFAULT_NMD_CORE_PCT}% core / {_DEFAULT_NMD_DURATION_MONTHS}-month "
                "duration) for products without behavioral assumptions."
            )
        specs.append(
            _FactSpec(
                fact_group="ftp_nmd",
                category=name,
                amount=segment.balance,
                derived_from="non-maturity DEPOSIT positions × DEPOSIT_STABILITY core "
                "share and NMD_DURATION effective duration",
                attributes={
                    "segment": name,
                    "balance_ghs": str(money(segment.balance)),
                    "core_pct": str(core_pct),
                    "volatile_pct": str((_HUNDRED - core_pct).quantize(RATE)),
                    "effective_duration_years": str(duration),
                },
            )
        )
    if not specs:
        groups.append(
            GroupResult(
                group="ftp_nmd",
                status="skipped",
                note="No non-maturity deposits exist at this as-of date.",
            )
        )
        return []
    groups.append(
        GroupResult(group="ftp_nmd", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs
