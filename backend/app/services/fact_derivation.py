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
    name-pattern fallback; the central-bank name forms in that fallback come
    from the bank's jurisdiction registry row (``_CentralBankNames``), never a
    country literal. When the required/excess split is unavailable the whole
    cash balance maps to ``cash_vault`` with a warning. Securities split
    ``securities_bog_bills`` vs ``securities_gog_bonds`` by product code
    (TBILL/BOND) with a ≤ 397-day remaining-maturity fallback. ``loans_gross``
    is Σ LOAN ``balance_ghs`` over the positions that HAVE one — a
    foreign-currency position with no ingested conversion is excluded and
    counted in the group warnings, never taken at zero. ``other_assets`` is the
    GL asset residual — all leaf ASSET GL balances not classified as cash and
    not COVERED by a sub-ledger aggregate — plus the INTERBANK_PLACEMENT
    sub-ledger. A GL block
    is skipped only when the sub-ledger genuinely carries positions of the
    covering type (``_GlCoverage``), so a bank that keeps a block in the GL
    without the matching sub-ledger never loses the balance, and a bank with
    both never counts it twice. The loan-loss allowance is never covered — the
    loan sub-ledger is gross — so it stays in ``other_assets`` and total assets
    are stated net of impairment. Deposits are classified by product:
    retail products split stable/less-stable via the ``DEPOSIT_STABILITY``
    behavioral assumption (products without an assumption default to fully
    less-stable — conservative); wholesale current accounts split operational
    /non-operational via the same assumption, non-operational current →
    ``wholesale_non_op_sme`` and wholesale term → ``wholesale_non_op_corporate``.
    Interbank borrowings ≤ 1y → ``secured_funding_l1``, > 1y →
    ``term_borrowings_gt_1y``; a GL borrowing block with no sub-ledger behind it
    joins the ≤ 1y bucket, the same rule already applied to a borrowing position
    with no contractual maturity. ``capital_total`` is the LEDGER's equity (Σ GL
    EQUITY balances), falling back to the capital register only when no GL
    equity block was ingested: ``capital_structure`` is the regulatory capital
    register — Tier 2 subordinated debt plus regulatory deductions — and using
    it as the balance sheet's equity line measured accounting assets against
    regulatory capital. GL liabilities that no derived line carries (payables,
    accruals, sundry provisions) are NOT bucketed anywhere: they are named in
    the derivation warnings and widen the identity gap, because guessing a home
    for them would put an unchosen number on a regulatory return. The GL itself
    is read as a chart, not as a pile of rows: see ``_resolve_gl_chart`` for the
    retirement-versus-sparse-reporting rule. The balance-sheet identity is then
    a FAIL-CLOSED control
    (``app/services/reconciliation.py``; enterprise audit 2026-08-20 P0-10):
    a gap within the GOVERNED tolerance (control-plane
    ``balance_identity_tolerance_pct``, effective-dated) is plugged into
    ``other_assets`` (assets short) or ``term_borrowings_gt_1y`` (funding
    short) and the plug is recorded in that line's provenance at ANY size; a
    larger gap needs an approved, dated reconciliation exception, and without
    one ``derive_facts`` REFUSES — no period, no facts, no run, no filing.
    Every official check writes an ``audit_events`` row. The live plane keeps
    materialising (the operator has to see the broken book) and REPORTS the
    block: ``derive_current_facts`` returns the verdict on
    ``CurrentDerivationResult.reconciliation``, ``pipeline.recompute_live``
    stamps every ``live_metrics`` row it writes ``pipeline_state="blocked"``
    with the control's message, and ``live_view.get_live_summary`` carries the
    verdict on the payload. That reporting half did not exist until 2026-08-22
    (forensic re-audit D-1): the verdict was computed here and discarded, so a
    tenant 3.68% out of balance served a CAR marked ``ready``.
    SECURITY_HOLDING rows reach ``securities_bog_bills`` /
    ``securities_gog_bonds`` only on positive evidence of sovereign or
    central-bank issuance; anything else is carried in ``other_assets``. Those
    two lines are unaffected by the HQLA level test below — a demotion out of
    Level 1 moves no balance-sheet figure.

loan_exposure
    LOAN positions partitioned by IFRS 9 stage and product
    ``regulatory_category``. Stage 3 → ``past_due_90`` (RW150). Category map:
    CORPORATE_UNRATED / CORPORATE_LOAN_UNRATED_100RW / AGRICULTURE →
    ``corporate_unrated`` (RW100), SME_UNRATED → ``sme_retail`` (RW75),
    RETAIL_UNSECURED → ``retail_other`` (RW75), RESIDENTIAL_MORTGAGE →
    ``residential_mortgage`` (RW35), COMMERCIAL_REAL_ESTATE →
    ``commercial_real_estate`` (RW100). An unknown or missing category gets NO
    risk weight: it is exposed as ``unclassified_<category>`` with a null
    ``risk_weight_code``, and ``capital.engine.resolve_risk_weight`` refuses the
    capital run naming it. 100% is a regulatory determination as much as 0% is,
    and no directive licenses one from a product label. Σ exposures ==
    ``loans_gross`` by construction.

securities
    The HQLA stock: the balance-sheet bills/bonds split re-emitted one row per
    established Basel HQLA level, plus the two cash-mirror rows
    (``cash_vault_hqla``, ``bog_excess_reserves_hqla``) carrying
    ``source="cash"`` so stress haircuts skip them. Two independent gates:

    * **Issuer.** Only SOVEREIGN / central-bank paper reaches these rows —
      ``_is_sovereign_security`` (typed ``counterparty_type``, the documented
      ``attributes.instrument`` / ``issuer_class`` conventions, a sovereign
      product code, or an issuer named in the jurisdiction registry). Paper with
      none of those signals is not HQLA and is not zero-risk-weighted.
    * **Level.** ``_classify_security_hqla`` then establishes L1 / L2A / L2B
      from the evidence, or refuses. Until 2026-08-22 all four emission sites
      stamped a literal ``"L1"`` (forensic re-audit D-6), so no Level-2 fact
      could exist and the haircuts and 40%/15% caps built for P0-8 were
      unreachable. A holding whose level cannot be established is emitted as
      ``hqla_unclassified`` with ``hqla_level=None`` — the LCR filters it out
      of the stock, while the amount, the risk weight and therefore every
      capital, NSFR and stress figure stay exactly where they were.

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
    (INTERBANK_PLACEMENT, 100%). If no loan carries a maturity date the loan
    repayment inflows are NOT COMPUTABLE and book zero: an assumed inflow
    reduces net outflows and therefore RAISES the LCR, so absence of evidence
    of a repayment is never treated as a repayment.

market_risk / fx_position
    Per non-GHS currency: assets (LOAN, SECURITY_HOLDING, INTERBANK_PLACEMENT)
    minus liabilities (DEPOSIT, INTERBANK_BORROWING), both in original currency
    and in GHS via the ingested ``balance_ghs``, plus the signed FX_HEDGE
    notional deltas: a hedge's sell leg subtracts its notional from the sold
    currency's net and its buy leg adds ``notional × contract_rate`` to the
    bought currency's net (GHS legs are ignored — GHS is the base currency, so
    only foreign-currency exposure moves). The delta per currency is carried
    as ``net_derivatives_ccy`` in the fact attributes, mirroring the seed.
    Spot from ``fx_rates_current``, else implied from the position book (warned
    and stamped ``spot_source``); a rate is never invented, so a currency whose
    hedge legs cannot be converted is excluded from the book entirely rather
    than valued at par, and the implied fallback is withdrawn altogether once
    part of a currency's book carries no conversion. A position with no ingested
    ``balance_ghs`` is EXCLUDED from the reporting-currency leg and COUNTED
    (``unconverted_position_count`` on the fact) rather than converted to zero;
    the currency leg still carries it in full, so the two legs describe
    different books and ``regulatory_fx`` refuses the run. LC_GUARANTEE is
    off-balance and excluded from the NOP.
    A currency without a daily return history has no ``fx_position`` row (the
    VaR engine requires a history) but still counts in the net open position.
    ``net_long_fx`` / ``net_short_fx`` are the long/short sums over EVERY
    currency's post-hedge net — the capital charge and the NOP limits cover the
    whole book even where the VaR row cannot exist.

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
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import NotComputable, OutcomeDetail
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
    CurrentFinancialFact,
    IngestionBatch,
)
from app.services import jurisdictions, market_data_sources, reconciliation
from app.services.market_data import (
    CurveView,
    desk_projection_curve_name,
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
# Bills vs bonds fallback split: remaining maturity at or under 397 days is a bill.
_BILL_MAX_REMAINING_DAYS = 397
_LCR_WINDOW_DAYS = 30
_FX_RETURN_WINDOW = 250
# A canonical FX spot history replaces the legacy reference-row history for a
# currency only when it is deep enough to feed a meaningful VaR return series.
_MARKET_FX_HISTORY_MIN_OBSERVATIONS = 30
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
_PAST_DUE_CATEGORY: tuple[str, str] = ("past_due_90", "RW150")
_RETAIL_LOAN_CATEGORIES = ("retail_other", "residential_mortgage")

#: The IRR/FTP family for an exposure whose regulatory class is unrecognised.
#: Rate risk is measured on the whole book, so the balance is NOT dropped —
#: dropping it would understate the repricing gap and the funding-cost base.
#: It gets its own label rather than joining ``corporate_loans``, because the
#: platform does not know that it is corporate.
_UNCLASSIFIED_FAMILY = "unclassified_loans"

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


class ReconciliationBlockedError(DerivationError, NotComputable):
    """The official derivation is refused by a data-integrity control (P0-10).

    Doubly typed like ``sdi_capital.SdiCapitalPolicyUnresolved``: every existing
    ``except DerivationError`` handler keeps working (the activation API's 409,
    the pipeline's job failure, the history loader's per-period rollback), and a
    boundary that already handles WS-A's fail-closed outcomes handles this one
    identically through ``NotComputable`` — with the full ``OutcomeDetail``
    (metric, reason, failing items, gap/tolerance/exception context) instead of
    a bare string.

    ``DerivationError.__init__`` is deliberately not called: ``super()`` inside
    it would follow this class's MRO into ``NotComputable`` with a plain string.
    """

    def __init__(self, code: str, message: str, detail: OutcomeDetail) -> None:
        NotComputable.__init__(self, detail)
        self._code = code
        self.message = message

    @property
    def code(self) -> str:
        """The derivation error code, not ``NotComputable``'s state code.

        ``NotComputable`` exposes ``code`` as the outcome state; every existing
        ``DerivationError`` consumer reads it as the machine-readable refusal
        code it puts on the 409 payload, so the ``DerivationError`` meaning wins
        here. The outcome state is still reachable as ``.state``.
        """
        return self._code


@dataclass
class GroupResult:
    group: str
    status: GroupStatus
    rows: int = 0
    warnings: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass(frozen=True)
class DerivationResult:
    bank_id: str
    reporting_period_id: UUID
    period_label: str
    as_of_date: date
    period_created: bool
    facts_deleted: int
    facts_created: int
    groups: tuple[GroupResult, ...]
    #: The balance-sheet identity verdict for this derivation, with its governed
    #: tolerance and any applied exception. Always present on the official plane.
    reconciliation: reconciliation.BalanceIdentityOutcome | None = None
    #: Which source systems duplicated each other's books, if any. Diagnostic:
    #: it explains the identity verdict above and never overrides it.
    source_overlap: reconciliation.SourceOverlapOutcome | None = None

    @property
    def warnings(self) -> list[str]:
        return [warning for group in self.groups for warning in group.warnings]


@dataclass(frozen=True)
class CurrentDerivationResult:
    """Current live materialisation outcome; deliberately has no period ID."""

    bank_id: str
    as_of_date: date
    generation: int
    facts_replaced: int
    facts_created: int
    groups: tuple[GroupResult, ...]
    #: The live plane keeps deriving on an unreconciled book (the operator needs
    #: to SEE the broken book to fix it), but the failure is carried here and in
    #: the group warnings — it is never presented as a clean balance sheet.
    reconciliation: reconciliation.BalanceIdentityOutcome | None = None
    #: Which source systems duplicated each other's books, if any (diagnostic).
    source_overlap: reconciliation.SourceOverlapOutcome | None = None

    @property
    def warnings(self) -> list[str]:
        return [warning for group in self.groups for warning in group.warnings]


@dataclass(frozen=True)
class _PositionRow:
    """One current-generation snapshot flattened to the fields derivation uses."""

    source_reference: str
    #: Which of the bank's ingestion channels pushed this row. Supersession is
    #: scoped per (bank, source_system, source_ref) by design, so two systems
    #: can each hold a complete, live book for the same positions; this is the
    #: field ``reconciliation.detect_source_overlap`` reads to say so.
    source_system: str
    position_type: str
    currency: str
    balance: Decimal
    #: The position restated in the institution's reporting currency, or ``None``
    #: when NO such amount has been established: a foreign-currency position whose
    #: snapshot carries no ingested ``balance_ghs``. It is deliberately optional
    #: rather than zero (forensic audit 2026-08-22 D-21). Zero is a CLAIM — it
    #: says the exposure does not exist — and substituting it here understated one
    #: leg of every reporting-currency total while the native-currency leg carried
    #: the position in full. On the FX book that asymmetry reversed the direction
    #: of a filed net open position: a book short USD 144.7m reported LONG 21.0m
    #: cedis. Every consumer therefore EXCLUDES an unstated row and COUNTS it
    #: (:class:`_Unconverted`); none of them may take it at face value, and none of
    #: them may substitute a zero.
    balance_ghs: Decimal | None
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
class _Unconverted:
    """Positions left out of a reporting-currency total for want of a conversion.

    Deliberately the same shape ``sdi_capital._Exposures`` reports as
    ``unconverted_position_count`` / ``unconverted_currencies``, and that
    ``regulatory_reporting.bog_forms.sources_ext.bsd13`` reuses as
    ``_Unconverted`` (forensic audit 2026-08-22 D-21): an amount that cannot be
    stated in the reporting currency is EXCLUDED and COUNTED — never taken at
    face value, and never quietly dropped to zero.

    What happens NEXT is the difference between the three, and this one carries
    the least authority on purpose. The SDI summary carries its count to a
    filing blocker and a BoG cell refuses outright; a derived fact has nowhere
    to refuse, because derivation produces evidence rather than verdicts. So
    this counter makes the omission VISIBLE — in the group warnings an operator
    reads, and (for the FX book, where the omission changes a filed figure) in
    the fact's own attributes — and leaves the refusal to the engine that files
    the number. ``regulatory_fx._unstatable_position`` is that engine.
    """

    counts: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def over(cls, rows: Iterable[_PositionRow]) -> Self:
        counts: dict[str, int] = {}
        for row in rows:
            if row.balance_ghs is None:
                counts[row.currency] = counts.get(row.currency, 0) + 1
        return cls(counts=counts)

    @property
    def count(self) -> int:
        return sum(self.counts.values())

    @property
    def currencies(self) -> tuple[str, ...]:
        return tuple(sorted(self.counts))

    def __bool__(self) -> bool:
        return bool(self.counts)

    def note(self, subject: str, base_currency: str) -> str:
        """The warning naming what this total leaves out, and how to repair it."""
        listed = ", ".join(f"{currency} x{self.counts[currency]}" for currency in self.currencies)
        return (
            f"{self.count} foreign-currency {subject} carry no ingested "
            f"{base_currency} balance ({listed}), so they are EXCLUDED from the "
            f"{base_currency} total rather than counted at zero — zero would state that "
            "the exposure does not exist. The total is therefore incomplete by those "
            "rows. Ingest attributes.balance_ghs on them, or the rate behind it."
        )


def _stated(rows: Iterable[_PositionRow]) -> Iterator[tuple[_PositionRow, Decimal]]:
    """Every row carrying a stated reporting-currency amount, paired with it.

    The single reader of ``_PositionRow.balance_ghs`` that aggregation sites are
    expected to use. A foreign-currency position with no ingested conversion is
    skipped here because ``_position_row`` no longer invents a zero for it;
    callers that need to say how much they left out build an
    :class:`_Unconverted` over the same rows.
    """
    for row in rows:
        amount = row.balance_ghs
        if amount is not None:
            yield row, amount


@dataclass(frozen=True)
class _CentralBankNames:
    """How THIS jurisdiction's central bank is named in a chart of accounts.

    Registry-driven (``jurisdictions``), never a country literal — CLAUDE.md's
    jurisdiction rule. The pre-fix classifier tested ``"bog" in name``, which
    missed the SDI's ``GL-1020 "Balances with Bank of Ghana"`` (44.7m of central-
    bank money booked to ``other_assets``, understating HQLA) and could never
    have matched a Nigerian or Kenyan tenant naming its own central bank.

    The two forms are matched differently on purpose:

    * ``full`` ("bank of ghana", "central bank of nigeria") is a multi-word
      phrase; a substring test is safe.
    * ``short`` ("bog", "cbn", "cbk", "sarb") is three or four letters and would
      otherwise match inside ordinary words, so it is matched as a WHOLE WORD.

    ``country_name`` is deliberately absent. "Government of Ghana bonds" is
    sovereign paper, not a balance held at the central bank, and a country token
    would sweep the securities book into the cash line.
    """

    full: tuple[str, ...] = ()
    short: tuple[str, ...] = ()

    @classmethod
    def from_registry(cls, central_bank_name: str | None, regulator_short: str | None) -> Self:
        full = (central_bank_name or "").strip().lower()
        short = (regulator_short or "").strip().lower()
        return cls(full=(full,) if full else (), short=(short,) if short else ())

    def matches(self, name: str) -> bool:
        """Does this lower-cased GL account name identify the central bank?

        ``central bank`` is included as a generic English form: it is a
        jurisdiction-NEUTRAL phrase (every country has one), not a country
        literal, so it keeps working for a tenant whose registry row is missing.
        """
        if _CENTRAL_BANK_GENERIC in name:
            return True
        if any(token in name for token in self.full):
            return True
        return any(re.search(rf"\b{re.escape(token)}\b", name) for token in self.short)


#: The jurisdiction-neutral generic form. Not a country literal: it names the
#: institution type, and reads correctly in every jurisdiction's chart.
_CENTRAL_BANK_GENERIC = "central bank"


def _central_bank_names(db: Session, bank: Bank) -> _CentralBankNames:
    """The bank's own central-bank name forms, from the jurisdiction registry."""
    row = jurisdictions.get_jurisdiction(db, bank)
    if row is None:
        return _CentralBankNames()
    return _CentralBankNames.from_registry(row.central_bank_name, row.regulator_short)


@dataclass(frozen=True)
class _Canonical:
    as_of: date
    base_currency: str
    positions: list[_PositionRow]
    gl_accounts: list[CanonicalGlAccount]
    refs: dict[str, list[dict[str, Any]]]
    #: The as-of date of the chart of accounts in force (``_resolve_gl_chart``).
    #: A GL row dated earlier than this is a balance CARRIED FORWARD into the
    #: current chart, which the balance-sheet block reports rather than hides.
    gl_chart_as_of: date | None = None
    #: ``(code, name, last balance)`` for every account code the current chart
    #: has retired. Dropped from the book, never silently — the balance-sheet
    #: block names them and their amounts in its warnings.
    gl_retired: tuple[tuple[str, str, Decimal], ...] = ()
    # The governed data-integrity policy for this (bank, as-of): the effective
    # balance-sheet identity tolerance and any approved exception. Resolved once
    # at load time so the derivation itself stays pure.
    reconciliation: reconciliation.ReconciliationPolicy = field(
        default_factory=lambda: reconciliation.ReconciliationPolicy(
            tolerance=reconciliation.ResolvedTolerance(
                fraction=reconciliation.MODULE_DEFAULT_TOLERANCE_PCT / _HUNDRED,
                percent=reconciliation.MODULE_DEFAULT_TOLERANCE_PCT,
                source="module_default",
                param_code=reconciliation.TOLERANCE_PARAM_CODE,
                module_default_version=reconciliation.MODULE_DEFAULT_VERSION,
            ),
            exception=None,
        )
    )
    # Whether two or more of the bank's source systems are carrying the SAME
    # position types at this as-of. Diagnostic only: it never changes a figure
    # and never blocks — it explains a balance-sheet identity failure that the
    # identity control can only report as a percentage. Resolved at load time
    # (it needs the governed tolerance) so the derivation stays pure.
    source_overlap: reconciliation.SourceOverlapOutcome | None = None
    # Lower-cased sovereign / central-bank issuer names for this bank's
    # jurisdiction (registry-driven, never a country literal) — the last of the
    # sovereign-paper signals in ``_is_sovereign_security``.
    sovereign_issuer_names: tuple[str, ...] = ()
    # How this bank's OWN central bank is named in its chart of accounts
    # (registry-driven, never a country literal) — the GL cash classifier's
    # central-bank test. See ``_CentralBankNames``.
    central_bank_names: _CentralBankNames = field(default_factory=_CentralBankNames)
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
    db: Session, ctx: TenantContext, bank_id: str, as_of_date: date
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

    # The reconciliation control runs BEFORE any period or fact is written: a
    # book that fails it must leave no trace of a "successful" official
    # derivation behind (audit P0-10).
    specs, groups, identity = _derive_specs(canonical)
    # ``record_check`` commits the refusal itself, so it survives the caller's
    # rollback / 409.
    reconciliation.record_check(
        db, ctx, bank, as_of_date, identity, source_overlap=canonical.source_overlap
    )
    if identity.blocks_filing:
        raise ReconciliationBlockedError(
            reconciliation.BALANCE_IDENTITY_BLOCK_CODE,
            identity.message(canonical.base_currency),
            reconciliation.balance_identity_detail(bank, as_of_date, identity),
        )

    period, period_created = _ensure_period(db, ctx, bank, as_of_date)
    facts_deleted = _delete_period_facts(db, ctx, bank, period)
    facts = [_fact(bank, period, spec) for spec in specs]

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
        reconciliation=identity,
        source_overlap=canonical.source_overlap,
    )


def derive_current_facts(
    db: Session, ctx: TenantContext, bank_id: str, as_of_date: date
) -> CurrentDerivationResult:
    """Replace the bank's current live facts from accepted canonical state.

    This is the live-plane derivation entry point. It never creates or mutates
    a reporting period, `BankFinancialFact`, `RegulatoryRun`, or package.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    canonical = _load_canonical(db, ctx, bank, as_of_date)
    if not canonical.positions:
        raise DerivationError(
            "no_canonical_data",
            f"No accepted canonical position snapshots exist for {as_of_date.isoformat()}.",
        )
    specs, groups, identity = _derive_specs(canonical, live=True)
    previous = list(
        db.scalars(
            select(CurrentFinancialFact).where(
                CurrentFinancialFact.organization_id == ctx.organization_id,
                CurrentFinancialFact.bank_id == bank.id,
            )
        )
    )
    generation = max((row.source_generation for row in previous), default=0) + 1
    db.execute(
        delete(CurrentFinancialFact).where(
            CurrentFinancialFact.organization_id == ctx.organization_id,
            CurrentFinancialFact.bank_id == bank.id,
        )
    )
    db.add_all(_current_fact(bank, as_of_date, generation, spec) for spec in specs)
    db.flush()
    return CurrentDerivationResult(
        bank_id=bank.id,
        as_of_date=as_of_date,
        generation=generation,
        facts_replaced=len(previous),
        facts_created=len(specs),
        groups=tuple(groups),
        reconciliation=identity,
        source_overlap=canonical.source_overlap,
    )


def _derive_specs(
    canonical: _Canonical, *, live: bool = False
) -> tuple[list[_FactSpec], list[GroupResult], reconciliation.BalanceIdentityOutcome]:
    """Build deterministic facts once for either live or official materialisation."""
    groups: list[GroupResult] = []
    specs: list[_FactSpec] = []
    (
        balance_sheet,
        loan_rows,
        cash_amounts,
        securities,
        identity,
    ) = _derive_balance_sheet_block(canonical, groups, live=live)
    specs.extend(balance_sheet)
    specs.extend(_derive_loan_exposure(loan_rows, groups))
    specs.extend(_derive_ecl_exposure(loan_rows, groups))
    specs.extend(_derive_crm_collateral(loan_rows, groups))
    specs.extend(_derive_securities(securities, cash_amounts, groups))
    specs.extend(_derive_off_balance(canonical, groups))
    specs.extend(_derive_lcr_inflows(canonical, loan_rows, groups))
    fx_specs, fx_currencies = _derive_fx_positions(canonical, groups)
    specs.extend(fx_specs)
    specs.extend(_derive_fx_returns(canonical, fx_currencies, groups))
    specs.extend(_derive_fx_hedges(canonical, groups))
    specs.extend(_derive_operational_income(canonical, groups))
    specs.extend(_derive_cashflow_summary(canonical, groups))
    specs.extend(_derive_capital_components(canonical, groups))
    specs.extend(_derive_irr_positions(canonical, loan_rows, groups))
    specs.extend(_derive_irr_swaps(canonical, groups))
    curve_specs, curve = _derive_ftp_curve(canonical, groups)
    specs.extend(curve_specs)
    specs.extend(_derive_ftp_products(canonical, loan_rows, curve, groups))
    specs.extend(_derive_ftp_branches(canonical, groups))
    specs.extend(_derive_ftp_nmd(canonical, groups))
    return specs, groups, identity


# ---------------------------------------------------------------------------
# Canonical loading
# ---------------------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _load_position_rows(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date, base_currency: str
) -> list[_PositionRow]:
    """The current, accepted position book at ``as_of``, flattened.

    One query, one base-currency resolution, one definition of "the book the
    derivation used" — the source-overlap diagnosis reads exactly these rows,
    so it can never be measured over a different population than the balance
    sheet it explains.
    """
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
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
    ).all()
    return [
        _position_row(snapshot, position, product, counterparty, base_currency)
        for snapshot, position, product, counterparty in rows
    ]


def _source_overlap(
    positions: list[_PositionRow], policy: reconciliation.ReconciliationPolicy
) -> reconciliation.SourceOverlapOutcome:
    """Diagnose duplicated source books over the rows the derivation will use.

    Measured over the STATED rows only, in the same reporting-currency units as
    the balance-sheet identity it exists to explain: a position with no
    reporting-currency amount contributes to no balance-sheet line, so counting
    it here — at a fabricated zero — would have inflated one system's row count
    against the other's while adding nothing to either total.
    """
    return reconciliation.detect_source_overlap(
        reconciliation.tally_source_books(
            (row.source_system, row.position_type, amount) for row, amount in _stated(positions)
        ),
        tolerance=policy.tolerance,
    )


def diagnose_source_overlap(
    db: Session, ctx: TenantContext, bank_id: str, as_of_date: date
) -> reconciliation.SourceOverlapOutcome:
    """Report whether this bank's source systems duplicate each other at ``as_of_date``.

    Read-only and side-effect free: it writes nothing, derives no fact and
    blocks nothing. An as-of with no accepted position data comes back
    ``determined=False`` with ``MISSING_REQUIRED_INPUT`` — "not assessed" is
    never dressed up as "no overlap".
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    positions = _load_position_rows(db, ctx, bank, as_of_date, jurisdictions.base_currency(bank))
    policy = reconciliation.load_policy(db, ctx.organization_id, bank, as_of_date)
    return _source_overlap(positions, policy)


def evaluate_balance_identity(
    db: Session, ctx: TenantContext, bank: Bank, as_of_date: date
) -> reconciliation.BalanceIdentityOutcome | None:
    """The identity verdict for the CURRENT canonical book at ``as_of_date``.

    Read-only and side-effect free: it loads the same canonical state
    :func:`derive_facts` would, runs only the balance-sheet block, and returns
    that block's verdict. No period, fact, audit event or run is written, so a
    filing-plane caller can ask "does this bank's book balance *now*?" without
    re-deriving anything (audit 2026-08-22 D-3).

    ``None`` means NOT ASSESSED, never "reconciled": an as-of with no accepted
    position snapshots has no canonical book for the control to weigh, and
    dressing that up as a pass is precisely the silent substitution this module
    exists to stop. Callers must treat ``None`` as the absence of an answer.
    """
    canonical = _load_canonical(db, ctx, bank, as_of_date)
    if not canonical.positions:
        return None
    # Only the balance-sheet block: it owns the identity, and running the other
    # groups would spend the whole derivation's work on a question none of them
    # answer.
    groups: list[GroupResult] = []
    *_rest, identity = _derive_balance_sheet_block(canonical, groups)
    return identity


def current_reconciliation_record(
    db: Session, ctx: TenantContext, bank_id: str
) -> dict[str, Any] | None:
    """The balance-sheet control's record stamped on the CURRENT live facts.

    The live derivation writes ``BalanceIdentityOutcome.provenance()`` onto the
    balance-sheet line that received the plug, so the live plane's verdict —
    gap, tolerance, tolerance source, exception, ``status`` — is recoverable
    from the fact set itself with one query and no new column.

    ``None`` means the control had nothing to record: either no live facts
    exist, or the book balanced exactly and no plug was applied. A plug of any
    size is recorded, so ``None`` can never hide one.
    """
    rows = db.scalars(
        select(CurrentFinancialFact.attributes).where(
            CurrentFinancialFact.organization_id == ctx.organization_id,
            CurrentFinancialFact.bank_id == bank_id,
            CurrentFinancialFact.fact_group == "balance_sheet",
        )
    )
    for attributes in rows:
        record = (attributes or {}).get("reconciliation")
        if isinstance(record, dict):
            return record
    return None


def _load_canonical(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> _Canonical:
    base_currency = jurisdictions.base_currency(bank)
    positions = _load_position_rows(db, ctx, bank, as_of, base_currency)

    # The whole current GL history at or before the as-of, INCLUDING rows with
    # no balance: a balance-less row still proves the code is on the chart, and
    # the chart is what decides whether an older balance may be carried forward.
    gl_rows = db.execute(
        select(CanonicalGlAccount, IngestionBatch.extraction_mode)
        .join(IngestionBatch, CanonicalGlAccount.ingestion_batch_id == IngestionBatch.id)
        .where(
            CanonicalGlAccount.organization_id == ctx.organization_id,
            CanonicalGlAccount.bank_id == bank.id,
            CanonicalGlAccount.as_of_date <= as_of,
            CanonicalGlAccount.superseded_by.is_(None),
            CanonicalGlAccount.withdrawn_at.is_(None),
            CanonicalGlAccount.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
    ).all()
    gl_accounts, gl_chart_as_of, gl_retired = _resolve_gl_chart(
        [(account, mode) for account, mode in gl_rows]
    )

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
    policy = reconciliation.load_policy(db, ctx.organization_id, bank, as_of)
    return _Canonical(
        as_of=as_of,
        base_currency=base_currency,
        positions=positions,
        gl_accounts=gl_accounts,
        gl_chart_as_of=gl_chart_as_of,
        gl_retired=gl_retired,
        refs=refs,
        reconciliation=policy,
        source_overlap=_source_overlap(positions, policy),
        sovereign_issuer_names=_sovereign_issuer_names(db, bank),
        central_bank_names=_central_bank_names(db, bank),
        market_curve=market_curve,
        market_spots=market_spots,
        market_fx_history=market_fx_history,
    )


def _resolve_gl_chart(
    rows: list[tuple[CanonicalGlAccount, str]],
) -> tuple[list[CanonicalGlAccount], date | None, tuple[tuple[str, str, Decimal], ...]]:
    """The chart of accounts in force, with balances carried forward inside it.

    Two legitimate behaviours have to be told apart, and the pre-fix rule
    conflated them by keeping the newest row per account code across ALL
    history:

    * **Sparse reporting.** An account that is not re-reported in a period
      keeps its last reported balance. Legitimate — many cores only send the
      accounts that moved.
    * **Retirement.** An account code removed from the chart stops existing.
      Under a per-code rule nothing newer ever carries that code, so its last
      balance survives forever. That is how ``1000 Cash and balances`` and
      ``2000 Customer deposits`` — codes last seen on a 2026-04-30 upload —
      were still contributing 24m and 180m to a 2026-06-30 book.

    The rule, stated rather than emergent:

    1. Only a **full** extraction can retire a code: an ``incremental`` batch
       carries the accounts that moved, so a code's absence from it says
       nothing. ``rows`` therefore arrives paired with its batch's declared
       ``extraction_mode``.
    2. The **chart of accounts in force** is the set of codes in the most
       recent GL generation, at or before the as-of, that a full extraction
       wrote — plus any code reported AFTER that date (an incremental top-up
       is an addition to the chart, never a redefinition of it). Balance-less
       rows count: they still assert the code is on the chart, which matters
       because a core can push a chart refresh with no balances at all — the
       primary's 2026-06-30 generation is exactly that.
    3. A code absent from that chart has been retired. It is dropped; an older
       balance is never resurrected under it. With no full extraction anywhere
       in the history there is no authority to retire anything, so every code
       stays.
    4. A code on the chart takes its most recent NON-NULL balance at or before
       the as-of — sparse reporting carried forward, inside the chart only.
    5. A charted code that has never carried a balance contributes nothing. No
       value is invented for it, and it is not read as a zero.

    Returns the resolved accounts (ordered by code), the chart's own as-of date
    so the balance-sheet block can report which balances are carried forward
    rather than current, and the retired codes with the balance they last
    carried so the drop is reported rather than silent.
    """
    if not rows:
        return [], None, ()
    full_dates = [account.as_of_date for account, mode in rows if mode == "full"]
    chart_as_of = max(full_dates) if full_dates else None
    if chart_as_of is None:
        chart = {account.account_code for account, _ in rows}
    else:
        chart = {
            account.account_code for account, _ in rows if account.as_of_date >= chart_as_of
        }
    # Deterministic scan: (organization, bank, code, as_of) is unique among
    # current rows, so the max by as_of_date is unambiguous; sorting keeps the
    # walk reproducible regardless of the database's row order.
    resolved: dict[str, CanonicalGlAccount] = {}
    retired: dict[str, CanonicalGlAccount] = {}
    for account, _ in sorted(rows, key=lambda row: (row[0].account_code, row[0].as_of_date)):
        if account.balance is None:
            continue
        target = resolved if account.account_code in chart else retired
        current = target.get(account.account_code)
        if current is None or account.as_of_date > current.as_of_date:
            target[account.account_code] = account
    return (
        sorted(resolved.values(), key=lambda account: account.account_code),
        chart_as_of,
        tuple(
            (account.account_code, account.name, _dec(account.balance))
            for account in sorted(retired.values(), key=lambda row: row.account_code)
        ),
    )


def _sovereign_issuer_names(db: Session, bank: Bank) -> tuple[str, ...]:
    """Lower-cased issuer names that identify this jurisdiction's sovereign.

    Registry-driven (``jurisdictions``), never a country literal — CLAUDE.md's
    jurisdiction rule. Used only as the last-resort signal in the SECURITY_HOLDING
    issuer test, behind the typed ``counterparty_type`` and the documented
    ``attributes.instrument`` / ``attributes.issuer_class`` conventions.
    """
    row = jurisdictions.get_jurisdiction(db, bank)
    if row is None:
        return ()
    names = (row.country_name, row.central_bank_name, row.sovereign_rating_issuer)
    return tuple(sorted({name.strip().lower() for name in names if name and name.strip()}))


def _load_market_data(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[CurveView | None, dict[str, Decimal], dict[str, list[tuple[date, Decimal]]]]:
    """Canonical market data by business scope (vendor-blind, §15 arbitration).

    The GHS curve feeds FTP; per-currency GHS spots overlay the legacy
    ``fx_rates_current`` dataset; spot histories deep enough for a VaR return
    series replace ``fx_rates_historical`` per currency. Everything absent
    falls back to the legacy reference rows.

    Projection-curve preference (curve platform spec §13 Stage 2): the desk's
    published sovereign zero (``AEQ.{CCY}.SOV.ZERO``) wins over currency-level
    vendor arbitration whenever it exists — it is the secondary-market,
    arbitrage-consistent upgrade of the same curve. Absent a desk publish the
    selection is exactly the historical arbitration, so books without desk
    curves derive byte-identically.
    """
    base_ccy = jurisdictions.base_currency(bank)
    # Route through the per-bank source preference (market_data_sources.md §3):
    # the selected plane + overlay flow live into FTP. The default preference
    # (aequor + overlay on) reproduces the historical projection-curve selection
    # exactly, so books without a preference derive byte-identically.
    market_curve = market_data_sources.preferred_projection_curve(
        db, ctx.organization_id, bank.id, base_ccy, as_of
    )
    market_spots: dict[str, Decimal] = {}
    market_fx_history: dict[str, list[tuple[date, Decimal]]] = {}
    for currency in list_fx_base_currencies(db, ctx.organization_id, bank.id, base_ccy, as_of):
        spot = market_data_sources.preferred_fx_spot(
            db, ctx.organization_id, bank.id, currency, base_ccy, as_of
        )
        if spot is not None:
            market_spots[currency] = spot.rate
        history = market_data_sources.preferred_fx_history(
            db, ctx.organization_id, bank.id, currency, base_ccy, as_of
        )
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
    if balance_ghs is None and position.currency == base_currency:
        # A book already denominated in the reporting currency needs no
        # conversion: its own balance IS the reporting-currency amount. This is
        # the ONLY substitution left here. A foreign-currency position with no
        # ingested conversion keeps ``None`` — see ``_PositionRow.balance_ghs``
        # for why it must not become zero.
        balance_ghs = balance
    return _PositionRow(
        source_reference=snapshot.source_reference,
        source_system=snapshot.source_system,
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


def _current_fact(
    bank: Bank, as_of_date: date, generation: int, spec: _FactSpec
) -> CurrentFinancialFact:
    attributes = dict(spec.attributes)
    attributes["source"] = spec.source_tag
    if spec.source_tag != SOURCE_TAG:
        attributes["derived_by"] = SOURCE_TAG
    attributes["derived_from"] = spec.derived_from
    return CurrentFinancialFact(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        source_as_of_date=as_of_date,
        source_generation=generation,
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
    #: The governed risk-weight code for this exposure, or ``None`` when the
    #: loan's regulatory category maps to no Basel/CRD exposure class this
    #: platform recognises. ``None`` is carried onto the ``loan_exposure`` fact
    #: and ``capital.engine.resolve_risk_weight`` refuses on it — see
    #: :func:`_classify_loans`.
    risk_weight_code: str | None


#: The category an exposure lands in when its regulatory classification is not
#: one this platform recognises. It is deliberately not a Basel exposure class:
#: it carries no risk weight, and the capital engine refuses the moment it reads
#: one of these facts.
_UNCLASSIFIED_PREFIX = "unclassified_"
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _loan_family(category: str) -> str:
    """The IRR/FTP family label for an exposure category."""
    return _LOAN_FAMILY.get(category, _UNCLASSIFIED_FAMILY)


def _unclassified_category(regulatory_category: str | None) -> str:
    """The exposure category for a loan whose regulatory class is unrecognised.

    Named after the bank's OWN category token so the refusal downstream points at
    the product taxonomy that has to be fixed, rather than at a generic bucket.
    """
    token = _NON_SLUG_RE.sub("_", (regulatory_category or "unmapped").strip().lower()).strip("_")
    return f"{_UNCLASSIFIED_PREFIX}{token or 'unmapped'}"


def _classify_loans(canonical: _Canonical, warnings: list[str]) -> list[_LoanRow]:
    """Every LOAN position bound to its exposure category and risk-weight code.

    A regulatory category the map does not carry gets NO risk weight (forensic
    audit 2026-08-22 D-22). The prior form substituted ``CORPORATE_UNRATED``
    (RW100) and logged a warning, which is the same defect this programme has
    been removing everywhere else: a risk weight is a regulatory determination
    about an exposure, and 100% is a determination as much as 0% is. On the
    measured book the substitution weighted GHS 387,209,829.04 across 363
    positions at 100% on the strength of product LABELs the platform could not
    interpret (``LOAN_MORTGAGE``, ``LOAN_RETAIL``, ``LOAN_SME``, at 2026-06-30
    and in every period of that institution's ten-year history) — conservative
    in direction, but wrong, and filed.

    Nothing here guesses the right weight, because the directive does not
    license one from a label. The Capital Requirements Directive (BoG, 2018)
    weights a residential mortgage at 35% ONLY where loan-to-value is at or
    below 80% and six further conditions hold (¶131), and at 100% otherwise
    (¶132); a retail exposure at 75% only where it QUALIFIES (¶128). Whether an
    exposure qualifies is a fact about the loan, not about the string its source
    system files it under. So the exposure keeps its full balance, carries no
    code, and ``capital.engine.resolve_risk_weight`` refuses the capital run
    naming the category — MISSING_REQUIRED_INPUT, the same refusal it already
    raises for an exposure that arrives with no code at all.

    The remedy is data, in either of two places: map the product to a recognised
    ``regulatory_category`` at ingestion, or extend the map here once the
    directive genuinely settles the class.
    """
    loans: list[_LoanRow] = []
    unknown: dict[str, set[str]] = {}
    for row in canonical.by_type("LOAN"):
        code: str | None
        if row.ifrs9_stage == 3:
            category, code = _PAST_DUE_CATEGORY
        else:
            mapped = _LOAN_CATEGORY_MAP.get((row.regulatory_category or "").upper())
            if mapped is None:
                declared = row.regulatory_category or "<none>"
                unknown.setdefault(declared, set()).add(row.product_code or "<no-product>")
                category, code = _unclassified_category(row.regulatory_category), None
            else:
                category, code = mapped
        loans.append(_LoanRow(row=row, category=category, risk_weight_code=code))
    for declared in sorted(unknown):
        warnings.append(
            f"Loan products declare regulatory category {declared!r}, which is not a "
            "Basel/CRD exposure class this platform recognises, so NO risk weight is "
            f"established for them and they are exposed as "
            f"'{_unclassified_category(None if declared == '<none>' else declared)}'. The "
            "capital run refuses rather than weighting them at 100%: a risk weight is a "
            "regulatory determination about the exposure, and the directive does not "
            "license one from a product label (a residential mortgage is 35% only where "
            "loan-to-value and six further conditions hold, 100% otherwise). Map these "
            f"products to a recognised regulatory category at ingestion: "
            f"{_shown(sorted(unknown[declared]))}."
        )
    return loans


#: Matches the four-digit chart code inside an account code, so the documented
#: numeric chart convention (1x = assets, 12xx = securities, 13xx = the loan
#: book, 21xx = interbank borrowings, …) reads the same whether the source
#: system emits ``1201`` or ``GL-1200``. Charts that do not use the convention
#: at all are classified by account NAME, which is the only tenant-neutral
#: signal left. Neither branch hardcodes a tenant's chart.
_CHART_CODE_RE = re.compile(r"\d{4}")


def _chart_code(code: str) -> int | None:
    """The four-digit chart code inside ``code``, or ``None``."""
    match = _CHART_CODE_RE.search(code)
    return int(match.group(0)) if match else None


def _in_block(code: str, low: int, high: int) -> bool:
    chart = _chart_code(code)
    return chart is not None and low <= chart <= high


@dataclass(frozen=True)
class _GlCoverage:
    """Which GL blocks the sub-ledger actually stands in for.

    A GL account is replaced by a position aggregate ONLY when the sub-ledger
    genuinely carries positions of the covering type. Without the gate, a bank
    that keeps a block in the GL but has not ingested the matching sub-ledger
    silently loses the balance — which is how the SDI's ``GL-2400 Borrowings``
    would vanish, and the mirror image of how its ``GL-1200`` was counted twice.
    """

    securities: bool
    loans: bool
    interbank_placements: bool
    deposits: bool
    interbank_borrowings: bool


def _gl_coverage(canonical: _Canonical) -> _GlCoverage:
    return _GlCoverage(
        securities=bool(canonical.by_type("SECURITY_HOLDING")),
        loans=bool(canonical.by_type("LOAN")),
        interbank_placements=bool(canonical.by_type("INTERBANK_PLACEMENT")),
        deposits=bool(canonical.by_type("DEPOSIT")),
        interbank_borrowings=bool(canonical.by_type("INTERBANK_BORROWING")),
    )


def _classify_gl_assets(
    canonical: _Canonical, coverage: _GlCoverage, warnings: list[str]
) -> tuple[dict[str, Decimal], Decimal]:
    """Classify leaf ASSET GLs → (cash rows by category, other-assets residual).

    Every ASSET account lands in exactly one place: a cash line, a sub-ledger
    line that stands in for it, or the ``other_assets`` residual. Nothing is
    dropped on the floor — a block is skipped only when ``coverage`` says a
    position aggregate is genuinely carrying it.

    Central-bank balances are identified by the numeric chart convention first
    (``1003``) and, for charts that do not use it, by the bank's OWN central-bank
    name forms from the jurisdiction registry (``canonical.central_bank_names``).
    The pre-fix test was the literal token ``"bog"``, so an institution whose
    chart spells the central bank out — the SDI's ``GL-1020 "Balances with Bank
    of Ghana"`` — fell through to ``other_assets``, understating high-quality
    liquid assets by the whole central-bank balance (NEW-40). ``"bill"`` still
    excludes central-bank BILLS: those are securities, not a settlement balance.

    The ``bog_*`` keys are historical fact-category identifiers kept for wire and
    database stability (the same reason the ``refinitiv`` vendor id survives its
    rebrand). They mean "central-bank required/excess reserves" for every
    jurisdiction and carry no country claim.
    """
    cash = {"cash_vault": _ZERO, "bog_required_reserves": _ZERO, "bog_excess_reserves": _ZERO}
    other = _ZERO
    have_reserve_split = False
    for account in canonical.gl_accounts:
        if account.account_class != "ASSET" or account.balance is None:
            continue
        balance = _dec(account.balance)
        name = account.name.lower()
        code = account.account_code.strip()
        if _chart_code(code) == 1001 or ("cash" in name and "flow" not in name):
            cash["cash_vault"] += balance
        elif _chart_code(code) == 1002 or "statutory" in name or "required" in name:
            cash["bog_required_reserves"] += balance
            have_reserve_split = True
        elif _chart_code(code) == 1003 or (
            canonical.central_bank_names.matches(name) and "bill" not in name
        ):
            cash["bog_excess_reserves"] += balance
            have_reserve_split = True
        elif _is_loan_loss_allowance_gl(code, name):
            # A credit-balance contra inside the asset side. No position line
            # carries it (the loan sub-ledger is gross), so it stays here: total
            # assets are stated net of impairment, as the ledger states them.
            other += balance
        elif coverage.securities and _is_securities_gl(code, name):
            continue  # covered by SECURITY_HOLDING positions
        elif coverage.loans and _is_loan_gl(code, name):
            continue  # covered by LOAN positions
        elif coverage.interbank_placements and _is_interbank_placement_gl(code, name):
            continue  # covered by INTERBANK_PLACEMENT positions
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


def _classify_gl_funding(
    canonical: _Canonical, coverage: _GlCoverage, warnings: list[str]
) -> tuple[Decimal, Decimal, list[tuple[str, str, Decimal]]]:
    """Classify LIABILITY + EQUITY GLs the same way the asset side is classified.

    The pre-fix derivation never read a GL liability or equity account at all:
    the asset side was GL-plus-positions while the funding side was
    positions-plus-the-regulatory-capital-register, so the identity compared two
    different books. This is the mirror of :func:`_classify_gl_assets`.

    Returns ``(uncovered borrowings, GL equity, unreconciled liabilities)``.
    """
    uncovered_borrowings = _ZERO
    gl_equity = _ZERO
    unreconciled: list[tuple[str, str, Decimal]] = []
    for account in canonical.gl_accounts:
        if account.balance is None:
            continue
        balance = _dec(account.balance)
        name = account.name.lower()
        code = account.account_code.strip()
        if account.account_class == "EQUITY":
            gl_equity += balance
            continue
        if account.account_class != "LIABILITY":
            continue  # INCOME / EXPENSE are not balance-sheet lines
        if _is_deposit_gl(code, name):
            if coverage.deposits:
                continue  # covered by DEPOSIT positions
            # A deposit block with no sub-ledger cannot be split stable /
            # less-stable / operational, and every deposit fact category
            # demands that split. Naming it beats inventing one.
            if balance != _ZERO:
                unreconciled.append((account.account_code, account.name, balance))
            continue
        if coverage.interbank_borrowings and _is_interbank_borrowing_gl(code, name):
            continue  # covered by INTERBANK_BORROWING positions
        if _is_borrowing_gl(code, name):
            uncovered_borrowings += balance
            continue
        if balance != _ZERO:
            unreconciled.append((account.account_code, account.name, balance))
    if unreconciled:
        # NOT bucketed anywhere. Payables, accruals and sundry provisions have
        # no honest home in the balance-sheet fact taxonomy, and guessing one
        # would put a number on a regulatory return that nobody chose. They are
        # named here and left to show up in the identity gap, which is exactly
        # what the fail-closed control exists to surface.
        listed = ", ".join(
            f"{code} {name} ({money(amount)} {canonical.base_currency})"
            for code, name, amount in unreconciled
        )
        warnings.append(
            f"{len(unreconciled)} GL liability account(s) are carried by no derived "
            f"balance-sheet line and are therefore absent from funding: {listed}. They widen "
            "the balance-sheet identity gap until the matching sub-ledger is ingested or the "
            "accounts are mapped."
        )
    return uncovered_borrowings, gl_equity, unreconciled


def _warn_carried_forward_gl(canonical: _Canonical, warnings: list[str]) -> None:
    """Say which GL balances are older than the chart they are reported under.

    ``_resolve_gl_chart`` carries a charted account's last reported balance
    forward, which is right — sparse reporting is legitimate. It becomes a
    silent substitution the moment nobody is told. On the primary's Sample Bank
    the whole 2026-06-30 chart refresh landed with NULL balances, so every
    single 2026-06-30 GL figure is in fact the 2026-05-31 balance; that has to
    be visible on the derivation, not inferred from a database query.
    """
    if canonical.gl_retired:
        listed = ", ".join(
            f"{code} {name} ({money(balance)} {canonical.base_currency})"
            for code, name, balance in canonical.gl_retired
        )
        warnings.append(
            f"{len(canonical.gl_retired)} GL account code(s) are absent from the chart of "
            "accounts in force and were NOT carried forward into this book: "
            f"{listed}. Re-report them if they are still live."
        )
    chart_as_of = canonical.gl_chart_as_of
    if chart_as_of is None:
        return
    stale = [account for account in canonical.gl_accounts if account.as_of_date < chart_as_of]
    if not stale:
        return
    oldest = min(account.as_of_date for account in stale)
    codes = ", ".join(sorted(account.account_code for account in stale)[:12])
    more = "" if len(stale) <= 12 else f", +{len(stale) - 12} more"  # noqa: PLR2004
    warnings.append(
        f"{len(stale)} of {len(canonical.gl_accounts)} GL account balances are carried forward "
        f"from an earlier reporting date (oldest {oldest.isoformat()}) into the "
        f"{chart_as_of.isoformat()} chart of accounts, because the newer generation reported no "
        f"balance for them: {codes}{more}."
    )


#: Name tokens that identify an impairment / loan-loss contra account. Checked
#: BEFORE the loan-block test, because such an account sits inside the loan code
#: block on most charts yet is not carried by the (gross) loan sub-ledger.
_ALLOWANCE_NAME_TOKENS = ("provision", "impairment", "allowance", "contra", "write-off")


def _is_loan_loss_allowance_gl(code: str, name: str) -> bool:
    del code  # named by convention, never by code block
    return any(token in name for token in _ALLOWANCE_NAME_TOKENS)


def _is_securities_gl(code: str, name: str) -> bool:
    if _in_block(code, 1200, 1299):
        return True
    return any(
        token in name for token in ("t-bill", "tbill", "treasury bill", "bond", "securit", "gilt")
    )


def _is_loan_gl(code: str, name: str) -> bool:
    if _is_loan_loss_allowance_gl(code, name):
        return False
    if _in_block(code, 1300, 1399):
        return True
    return any(token in name for token in ("loan", "mortgage", "advance"))


def _is_interbank_placement_gl(code: str, name: str) -> bool:
    if _in_block(code, 1100, 1199):
        return True
    return any(token in name for token in ("interbank", "placement", "due from bank"))


def _is_deposit_gl(code: str, name: str) -> bool:
    if _in_block(code, 2000, 2099):
        return True
    return "deposit" in name


def _is_interbank_borrowing_gl(code: str, name: str) -> bool:
    """The interbank block — the only borrowings INTERBANK_BORROWING covers."""
    if _in_block(code, 2100, 2199):
        return True
    return any(token in name for token in ("interbank", "due to bank", "repo", "money market"))


def _is_borrowing_gl(code: str, name: str) -> bool:
    """Borrowed funding of any kind, interbank or not.

    Subordinated debt and term loans from development banks are borrowed money
    on the balance sheet but are NOT interbank placements, so they are never
    covered by the interbank sub-ledger. Treating them as covered dropped real
    funding off the sheet — on the primary that was 33.9m of Tier 2 debt.
    """
    if _is_interbank_borrowing_gl(code, name):
        return True
    return any(token in name for token in ("borrow", "debt", "note issued", "bond issued"))


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
    deposits = canonical.by_type("DEPOSIT")
    for row, balance in _stated(deposits):
        product_code = row.product_code or "<no-product>"
        share = stability.get(product_code)
        if _is_retail_deposit_product(row):
            if share is None:
                missing_retail.add(product_code)
                share = _ZERO  # conservative: all less-stable
            stable = balance * share
            split.retail_stable += stable
            split.retail_less_stable += balance - stable
        else:
            is_term = row.contractual_maturity is not None
            if is_term:
                split.wholesale_non_op_corporate += balance
            else:
                operational_share = share if share is not None else _ZERO
                operational = balance * operational_share
                split.wholesale_operational += operational
                split.wholesale_non_op_sme += balance - operational
    unconverted = _Unconverted.over(deposits)
    if unconverted:
        warnings.append(unconverted.note("DEPOSIT positions", canonical.base_currency))
    if missing_retail:
        warnings.append(
            "Retail deposit products without a DEPOSIT_STABILITY assumption were "
            f"treated as fully less-stable: {', '.join(sorted(missing_retail))}."
        )
    return split


def _derive_balance_sheet_block(  # noqa: PLR0912, PLR0915 - one linear balance-sheet assembly
    canonical: _Canonical, groups: list[GroupResult], *, live: bool = False
) -> tuple[
    list[_FactSpec],
    list[_LoanRow],
    dict[str, Decimal],
    _SecuritiesSplit,
    reconciliation.BalanceIdentityOutcome,
]:
    warnings: list[str] = []
    coverage = _gl_coverage(canonical)
    loan_rows = _classify_loans(canonical, warnings)
    cash, gl_other_assets = _classify_gl_assets(canonical, coverage, warnings)
    uncovered_borrowings, gl_equity, _unreconciled = _classify_gl_funding(
        canonical, coverage, warnings
    )
    _warn_carried_forward_gl(canonical, warnings)
    deposit_split = _split_deposits(canonical, warnings)

    securities, non_sovereign_securities = _split_securities(canonical, warnings)
    bills, bonds = securities.bills, securities.bonds
    loan_positions = [loan.row for loan in loan_rows]
    loans_gross = sum((amount for _, amount in _stated(loan_positions)), _ZERO)
    # Interbank: ONE source per leg, and the same source for both. The borrowing
    # leg has always come from the sub-ledger; the placement leg was counted in
    # neither the GL (the GL rows were the residual) nor the positions, so the
    # liability leg stood alone. Both legs are now the sub-ledger where it
    # exists, and ``_classify_gl_assets`` skips the GL placement block under the
    # same coverage gate so nothing is counted twice.
    placements = canonical.by_type("INTERBANK_PLACEMENT")
    interbank_placements = sum((amount for _, amount in _stated(placements)), _ZERO)

    secured_funding = _ZERO
    term_borrowings = _ZERO
    one_year_out = canonical.as_of + timedelta(days=365)
    borrowings = canonical.by_type("INTERBANK_BORROWING")
    for row, amount in _stated(borrowings):
        if row.contractual_maturity is not None and row.contractual_maturity > one_year_out:
            term_borrowings += amount
        else:
            secured_funding += amount
    # Every balance-sheet asset and liability line above is a total over STATED
    # amounts only. Whatever it left out is named here rather than silently
    # absorbed, because the identity control downstream measures assets against
    # liabilities and an unexplained gap is indistinguishable from a bad book.
    for subject, rows in (
        ("LOAN positions", loan_positions),
        ("INTERBANK_PLACEMENT positions", placements),
        ("INTERBANK_BORROWING positions", borrowings),
    ):
        unconverted = _Unconverted.over(rows)
        if unconverted:
            warnings.append(unconverted.note(subject, canonical.base_currency))
    secured_funding_note = "INTERBANK_BORROWING ≤ 1y"
    if uncovered_borrowings != _ZERO:
        # A GL borrowing block with no sub-ledger behind it. The AMOUNT is the
        # ledger's, not invented; only the tenor bucket is assigned, and it is
        # assigned by the rule this very function already applies to a borrowing
        # position with no contractual maturity — the ≤ 1y bucket, which is the
        # conservative side for NSFR available stable funding.
        secured_funding += uncovered_borrowings
        secured_funding_note += "; GL borrowings with no sub-ledger, bucketed ≤ 1y (no tenor)"
        warnings.append(
            f"{money(uncovered_borrowings)} {canonical.base_currency} of GL borrowings carry no "
            "matching sub-ledger positions and no tenor; they are carried in secured_funding_l1 "
            "(the ≤ 1y bucket applied to any borrowing without a contractual maturity). Ingest "
            "the borrowing sub-ledger to bucket them by their real maturity."
        )

    capital_rows = list(canonical.refs.get("capital_structure", ()))
    capital_amounts = [_dec_or_none(payload.get("amount_ghs")) for payload in capital_rows]
    capital_register_total = sum(
        (amount for amount in capital_amounts if amount is not None), _ZERO
    )
    missing_capital_amounts = sum(1 for amount in capital_amounts if amount is None)
    if missing_capital_amounts:
        # A capital_structure row with no amount contributes nothing here and is
        # already skipped by _derive_capital_components. Say so: it is a real
        # shortfall in equity, and the balance-sheet identity control below is
        # what stops it from being plugged away in silence.
        warnings.append(
            f"{missing_capital_amounts} capital_structure row(s) carry no amount_ghs and "
            "contribute nothing to the capital components. Regulatory capital is understated "
            "by whatever they hold."
        )
    # ``capital_total`` is the balance sheet's EQUITY line, so it is the ledger's
    # equity. ``capital_structure`` is the REGULATORY CAPITAL register — it
    # carries Tier 2 subordinated debt (a liability) and regulatory deductions
    # (goodwill, DTA), which is why it was the wrong source: the identity was
    # measuring accounting assets against regulatory capital. The register keeps
    # its real job, the ``capital_component`` facts, untouched.
    has_gl_equity = any(
        account.account_class == "EQUITY" and account.balance is not None
        for account in canonical.gl_accounts
    )
    if has_gl_equity:
        capital_total = gl_equity
        capital_note = "GL equity accounts"
    else:
        capital_total = capital_register_total
        capital_note = "capital_structure Σ signed amounts (no GL equity accounts ingested)"
        if capital_rows:
            warnings.append(
                "No GL equity accounts were ingested, so the balance sheet's equity line falls "
                "back to the regulatory capital register, which includes Tier 2 instruments and "
                "regulatory deductions. Ingest the GL equity block for an accounting equity."
            )
    if capital_total == _ZERO:
        warnings.append(
            "Neither GL equity accounts nor capital_structure reference rows were found; "
            "capital_total is zero and capital-dependent modules will fail until capital data "
            "is ingested."
        )

    other_assets = gl_other_assets + non_sovereign_securities + interbank_placements
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
    # The governed balance-sheet identity control (audit P0-10). The gap is no
    # longer plugged unconditionally: it is plugged only within the governed
    # tolerance or under an approved, effective-dated exception, and the plug is
    # always recorded in provenance. Anything else refuses to derive.
    identity, plug, plug_target = canonical.reconciliation.evaluate_balance_identity(
        assets_total, funding_total, plug_when_blocked=live
    )
    gap = identity.gap
    plug_note: str | None = None
    if plug != _ZERO and plug_target == "other_assets":
        other_assets += plug
        plug_note = f"balance plug +{money(plug)} {canonical.base_currency} added to other_assets"
    elif plug != _ZERO and plug_target == "term_borrowings_gt_1y":
        term_borrowings += plug
        plug_note = (
            f"balance plug +{money(plug)} {canonical.base_currency} added to term_borrowings_gt_1y"
        )
    if plug_note is not None:
        # Every plug is reported, at ANY size. The pre-audit code warned only
        # above 0.5% of assets, so a sub-threshold plug was entirely invisible.
        gap_pct = (identity.gap_fraction * _HUNDRED).quantize(Decimal("0.0001"))
        head = (
            f"Balance-sheet identity gap of {money(abs(gap))} {canonical.base_currency} "
            f"({gap_pct}% of assets) was plugged ({plug_note})"
        )
        if identity.within_tolerance:
            warnings.append(
                f"{head}; within the governed tolerance of "
                f"{identity.tolerance.percent}% ({identity.tolerance.source})."
            )
        elif identity.exception_applied and identity.exception is not None:
            warnings.append(
                f"{head}. It EXCEEDS the governed tolerance of "
                f"{identity.tolerance.percent}% ({identity.tolerance.source}) and is "
                "permitted only by approved reconciliation exception "
                f"{identity.exception.exception_id}. The general ledger and sub-ledgers "
                "do not reconcile."
            )
        else:
            # Live plane only: the official plane never reaches here — it raises.
            warnings.append(
                f"{head} FOR THE LIVE VIEW ONLY. The gap exceeds the governed tolerance "
                f"of {identity.tolerance.percent}% ({identity.tolerance.source}) with no "
                "approved exception, so nothing derived from this book may be filed."
            )

    def bs(category: str, amount: Decimal, side: str, derived_from: str) -> _FactSpec:
        attributes: dict[str, Any] = {"side": side}
        if plug_target == category and plug != _ZERO:
            # The plug is never invisible: the receiving line carries the full
            # control record (gap, tolerance, tolerance source, exception).
            attributes["reconciliation"] = identity.provenance()
        return _FactSpec(
            fact_group="balance_sheet",
            category=category,
            amount=amount,
            derived_from=derived_from,
            attributes=attributes,
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
            "GL asset residual (fixed and sundry assets, impairment contra) + "
            "INTERBANK_PLACEMENT positions" + (f"; {plug_note}" if plug_note and gap > 0 else ""),
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
        bs("secured_funding_l1", secured_funding, "liability", secured_funding_note),
        bs(
            "term_borrowings_gt_1y",
            term_borrowings,
            "liability",
            "INTERBANK_BORROWING > 1y" + (f"; {plug_note}" if plug_note and gap < 0 else ""),
        ),
        bs("capital_total", capital_total, "equity", capital_note),
    ]
    if identity.blocks_filing:
        warnings.append(identity.message(canonical.base_currency))
    # The diagnosis follows the verdict: the identity control can only say the
    # book is out by x%, and the commonest reason it is out by a LOT is that two
    # source systems each pushed a complete book. Reported at any identity
    # verdict — a duplicated book that happens to balance is still double
    # counted — and it never changes a figure or a gate.
    if canonical.source_overlap is not None:
        overlap_message = canonical.source_overlap.message(canonical.base_currency)
        if overlap_message is not None:
            warnings.append(overlap_message)
    groups.append(
        GroupResult(group="balance_sheet", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs, loan_rows, cash, securities, identity


#: Counterparty types whose paper is sovereign / central-bank issuance, i.e. the
#: only ``SECURITY_HOLDING`` rows the derivation may emit as Level-1 HQLA at a
#: 0% risk weight. (Audit §3: every security was emitted L1/RW0 with no issuer
#: or rating test, so a corporate bond financed the LCR and carried no RWA.)
_SOVEREIGN_COUNTERPARTY_TYPES = frozenset(
    {"SOVEREIGN", "CENTRAL_BANK", "GOVERNMENT_ENTITY", "MULTILATERAL_DEV_BANK"}
)
#: Documented ``attributes.instrument`` values (docs/API_INTEGRATION.md §3.4)
#: that name sovereign or central-bank paper.
_SOVEREIGN_INSTRUMENTS = frozenset(
    {
        "tbill",
        "tbill_other",
        "gog_bond",
        "gog_bond_other",
        "gog_stock",
        "ggilb",
        "bog_bill",
        "bog_bond",
        "bog_bond_other",
        "bog_other",
        "tor_bond",
        "finsap_bond",
        "cocoa_bill",
        "grains_bill",
        "cotton_bill",
    }
)
#: Product-code tokens that name sovereign / central-bank paper directly. The
#: pre-audit ``_is_bill`` split already keyed on this vocabulary to decide
#: ``securities_bog_bills`` vs ``securities_gog_bonds``.
_SOVEREIGN_PRODUCT_TOKENS = (
    "TBILL",
    "T-BILL",
    "GOG",
    "GOVT",
    "GOVERNMENT",
    "TREASURY",
    "SOVEREIGN",
)


def _is_sovereign_security(row: _PositionRow, sovereign_names: tuple[str, ...]) -> bool:
    """Positive evidence that this holding is sovereign / central-bank paper.

    Fail-closed by construction: absence of every signal below means the paper
    is NOT recognised as Level-1 HQLA and NOT risk-weighted at 0%. Each signal
    is ingested data, never an inference from a missing field:

    * the typed ``counterparty_type`` (SOVEREIGN / CENTRAL_BANK / …);
    * ``attributes.instrument`` from the documented BoG instrument vocabulary;
    * ``attributes.issuer_class``, documented as GOVERNMENT_ENTITY-only;
    * a product code naming sovereign paper (TBILL / GOG / TREASURY / …);
    * an ``attributes.issuer`` naming the jurisdiction's sovereign or central
      bank, resolved from the jurisdictions registry — never a literal country.
    """
    if (row.counterparty_type or "").upper() in _SOVEREIGN_COUNTERPARTY_TYPES:
        return True
    attributes = row.attributes or {}
    if str(attributes.get("instrument") or "").strip().lower() in _SOVEREIGN_INSTRUMENTS:
        return True
    if attributes.get("issuer_class"):
        return True
    code = f"{row.product_code or ''} {row.regulatory_category or ''}".upper()
    if any(token in code for token in _SOVEREIGN_PRODUCT_TOKENS):
        return True
    issuer = str(attributes.get("issuer") or "").strip().lower()
    if not issuer:
        return False
    return any(name in issuer for name in sovereign_names)


#: The Basel HQLA levels this derivation may emit. Mirrors
#: ``app.domain.liquidity.engine.HQLA_LEVELS``; a value outside it raises
#: ``UnclassifiedHqlaError`` there rather than being counted at face value.
_HQLA_LEVELS = ("L1", "L2A", "L2B")

#: Issuers whose paper is public-sector or multilateral rather than the
#: sovereign/central bank itself. BCBS 238 makes their tier turn ENTIRELY on the
#: Basel II standardised risk weight of the claim — 0% is Level 1 (¶50(c)), 20%
#: is Level 2A (¶52(a)) — and the canonical book carries no per-issuer risk
#: weight for a security. Neither tier can therefore be established from the
#: data, so these are excluded from HQLA unless the bank ingests its own
#: determination in ``attributes.hqla_level``.
_PUBLIC_SECTOR_COUNTERPARTY_TYPES = frozenset({"GOVERNMENT_ENTITY", "MULTILATERAL_DEV_BANK"})


@dataclass(frozen=True)
class _SecuritiesSplit:
    """The SECURITY_HOLDING book split for the balance sheet AND for HQLA.

    ``bills``/``bonds`` are the balance-sheet lines and keep their historical
    meaning (every sovereign holding, whatever its liquidity tier). The
    remaining fields partition that same total by established Basel HQLA level,
    so ``l1_bills + l1_bonds + level2a + level2b + unclassified == bills + bonds``
    by construction — the securities fact group still ties to the balance-sheet
    securities lines.
    """

    bills: Decimal
    bonds: Decimal
    l1_bills: Decimal
    l1_bonds: Decimal
    level2a: Decimal
    level2b: Decimal
    unclassified: Decimal
    #: (reason, amount, example source references) for every excluded bucket.
    exclusions: tuple[tuple[str, Decimal, tuple[str, ...]], ...] = ()


def _classify_security_hqla(row: _PositionRow, canonical: _Canonical) -> tuple[str | None, str]:
    """The Basel HQLA level of one sovereign-bucket holding, or why there is none.

    Fail-closed by construction (forensic re-audit 2026-08-22 D-6). Until then
    every one of the four emission sites in ``_derive_securities`` stamped a
    literal ``"L1"``, so no Level-2 fact could exist: the haircut schedule, the
    40% Level-2 cap and the 15% Level-2B sub-cap built for enterprise audit P0-8
    were unreachable code, and their governed parameters never entered
    ``input_hash``. L1 is also the most favourable tier there is (0% haircut, no
    cap), so the literal was a silent grant, not a neutral default.

    Evidence, in order. Every branch is READ, never inferred:

    1. ``attributes.hqla_level`` — the bank's OWN Basel determination, the same
       thing it maintains for its LCR return. Documented in
       docs/API_INTEGRATION.md §3.4. Validated against the Basel taxonomy: a
       value outside it excludes the holding rather than defaulting to L1.
    2. Public-sector / multilateral issuance → excluded. See
       :data:`_PUBLIC_SECTOR_COUNTERPARTY_TYPES` for why neither tier is
       establishable.
    3. Sovereign or central-bank paper in a currency OTHER than the bank's own →
       excluded. BCBS 238 ¶50(c) admits it at Level 1 only on a 0% risk weight,
       and ¶50(e) only up to the bank's stressed net outflows in that same
       currency. The book carries neither the risk weight nor a per-currency
       outflow attribution, so the tier cannot be established.
    4. Sovereign or central-bank paper in the bank's own reporting currency →
       Level 1 (BCBS 238 ¶50(d)-(e)). This is the domestic-sovereign case, and
       the only one the data settles on its own.

    What this deliberately does NOT do is infer Level 2A/2B from an issuer
    rating. BCBS 238 ¶52(b)/¶54(a) require, beyond the rating, that the paper is
    not issued by a financial institution AND has a proven record as a reliable
    source of liquidity in deep, active markets. The canonical book establishes
    the first and not the second, so a rating-driven tier would be a modelling
    claim wearing a citation. Non-sovereign paper therefore keeps its existing
    treatment (``other_assets``, no HQLA credit) until the bank classifies it.
    """
    attributes = row.attributes or {}
    declared = str(attributes.get("hqla_level") or "").strip().upper()
    if declared:
        if declared in _HQLA_LEVELS:
            return declared, "ingested attributes.hqla_level"
        return None, (
            f"attributes.hqla_level {declared!r} is not one of {_HQLA_LEVELS}; an "
            "unrecognised level is not Level 1"
        )
    if (row.counterparty_type or "").upper() in _PUBLIC_SECTOR_COUNTERPARTY_TYPES or attributes.get(
        "issuer_class"
    ):
        return None, (
            "public-sector or multilateral issuance whose Basel risk weight the book "
            "does not carry, so BCBS 238 ¶50(c) Level 1 and ¶52(a) Level 2A cannot be "
            "told apart (send attributes.hqla_level to classify it)"
        )
    if (row.currency or "").upper() != (canonical.base_currency or "").upper():
        return None, (
            f"sovereign or central-bank paper denominated in {row.currency}, not the "
            f"bank's own {canonical.base_currency}; BCBS 238 ¶50(c) needs the issuer's "
            "0% risk weight and ¶50(e) needs the stressed net outflow in that same "
            "currency, neither of which the book carries (send attributes.hqla_level "
            "to classify it)"
        )
    return "L1", "domestic sovereign / central-bank paper in the reporting currency"


def _split_securities(
    canonical: _Canonical, warnings: list[str]
) -> tuple[_SecuritiesSplit, Decimal]:
    """The SECURITY_HOLDING book split for the balance sheet and for HQLA.

    Two independent tests, in this order:

    1. **Is it sovereign paper?** ``_is_sovereign_security``. Non-sovereign
       holdings leave the ``securities_bog_bills`` / ``securities_gog_bonds``
       lines — which the capital engine zero-weights — and land in
       ``other_assets`` (RW100, no HQLA credit) with a warning naming them. The
       balance-sheet total is unchanged; only the claim about what the paper IS
       changes.
    2. **What HQLA level is it?** ``_classify_security_hqla``, applied only
       WITHIN the sovereign bucket, so this test can demote a holding out of
       Level 1 but can never promote non-sovereign paper into HQLA. The
       balance sheet, the risk weights and therefore capital are untouched by
       it: it decides only which securities fact carries which
       ``hqla_level``.
    """
    bills = _ZERO
    bonds = _ZERO
    non_sovereign = _ZERO
    unsourced: list[str] = []
    l1_bills = _ZERO
    l1_bonds = _ZERO
    by_level: dict[str, Decimal] = {"L2A": _ZERO, "L2B": _ZERO}
    excluded: dict[str, tuple[Decimal, list[str]]] = {}
    holdings = canonical.by_type("SECURITY_HOLDING")
    for row, balance in _stated(holdings):
        if not _is_sovereign_security(row, canonical.sovereign_issuer_names):
            non_sovereign += balance
            unsourced.append(row.source_reference)
            continue
        is_bill = _is_bill(row, canonical.as_of)
        if is_bill:
            bills += balance
        else:
            bonds += balance
        level, basis = _classify_security_hqla(row, canonical)
        if level == "L1":
            if is_bill:
                l1_bills += balance
            else:
                l1_bonds += balance
        elif level is not None:
            by_level[level] += balance
        else:
            amount, refs = excluded.get(basis, (_ZERO, []))
            excluded[basis] = (amount + balance, [*refs, row.source_reference])
    securities_unconverted = _Unconverted.over(holdings)
    if securities_unconverted:
        warnings.append(
            securities_unconverted.note("SECURITY_HOLDING positions", canonical.base_currency)
        )
    if unsourced:
        warnings.append(
            f"{money(non_sovereign)} {canonical.base_currency} of SECURITY_HOLDING positions "
            "carry no evidence of sovereign or central-bank issuance (counterparty_type, "
            "attributes.instrument, attributes.issuer_class, a sovereign product code or a "
            "named sovereign issuer). They are NOT counted as Level-1 HQLA and NOT "
            f"zero-risk-weighted; they are carried in other_assets: {_shown(unsourced)}."
        )
    split = _SecuritiesSplit(
        bills=bills,
        bonds=bonds,
        l1_bills=l1_bills,
        l1_bonds=l1_bonds,
        level2a=by_level["L2A"],
        level2b=by_level["L2B"],
        unclassified=sum((amount for amount, _ in excluded.values()), _ZERO),
        exclusions=tuple(
            (reason, amount, tuple(sorted(refs)))
            for reason, (amount, refs) in sorted(excluded.items())
        ),
    )
    return split, non_sovereign


def _shown(references: Sequence[str], limit: int = 10) -> str:
    """The first ``limit`` source references, with a count of the rest."""
    listed = ", ".join(sorted(references)[:limit])
    more = "" if len(references) <= limit else f" (+{len(references) - limit} more)"
    return f"{listed}{more}"


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
    totals: dict[str, tuple[Decimal, str | None]] = {}
    for loan in loan_rows:
        balance = loan.row.balance_ghs
        if balance is None:
            continue  # no reporting-currency amount to expose; counted upstream
        amount, code = totals.get(loan.category, (_ZERO, loan.risk_weight_code))
        totals[loan.category] = (amount + balance, code)
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


def _derive_ecl_exposure(loan_rows: list[_LoanRow], groups: list[GroupResult]) -> list[_FactSpec]:
    """Staged EAD buckets for the IFRS 9 ECL engine (Phase 2 item 8).

    Emits ``"<family>:stage<n>"`` rows only for loans carrying an ingested
    IFRS 9 stage — an unstaged book derives nothing, and the capital engine
    then falls back to ingested provisions rather than modeling on air.
    """
    totals: dict[str, Decimal] = {}
    for loan in loan_rows:
        stage = loan.row.ifrs9_stage
        balance = loan.row.balance_ghs
        if stage is None or balance is None:
            continue
        key = f"{loan.category}:stage{stage}"
        totals[key] = totals.get(key, _ZERO) + balance
    specs = [
        _FactSpec(
            fact_group="ecl_exposure",
            category=category,
            amount=amount,
            derived_from="LOAN positions by family and ingested IFRS 9 stage",
        )
        for category, amount in sorted(totals.items())
    ]
    if specs:
        groups.append(GroupResult(group="ecl_exposure", status="derived", rows=len(specs)))
    else:
        # Audit §3 / P0-10 companion: the empty case used to append NO group at
        # all, so a capital run with no IFRS 9 ECL looked complete. It is now an
        # explicit NOT_COMPUTABLE state naming what is missing and what the
        # capital engine falls back to.
        groups.append(
            GroupResult(
                group="ecl_exposure",
                status="skipped",
                note="Not computable: no LOAN position carries an ingested IFRS 9 stage, so "
                "no staged EAD buckets exist. The capital run uses INGESTED provisions "
                "instead of a modelled ECL — the impairment figure is the bank's own, not "
                "this platform's.",
            )
        )
    return specs


def _derive_crm_collateral(
    loan_rows: list[_LoanRow], groups: list[GroupResult]
) -> list[_FactSpec]:
    """CRM collateral/guarantee values per loan family + class (item 9).

    Reads the documented ``crm_collateral_ghs``/``crm_collateral_class`` and
    ``crm_guarantee_ghs``/``crm_guarantor_class`` attribute conventions
    (docs/API_INTEGRATION.md §3.4); the supervisory haircut is applied by the
    capital engine from the effective-dated ``param_crm_haircut`` schedule,
    never here.
    """
    totals: dict[str, Decimal] = {}
    for loan in loan_rows:
        attributes = loan.row.attributes
        for value_key, class_key in (
            ("crm_collateral_ghs", "crm_collateral_class"),
            ("crm_guarantee_ghs", "crm_guarantor_class"),
        ):
            value = _dec_or_none(attributes.get(value_key))
            collateral_class = attributes.get(class_key)
            if value is None or value <= _ZERO or not collateral_class:
                continue
            key = f"{loan.category}:{str(collateral_class).upper()}"
            totals[key] = totals.get(key, _ZERO) + value
    specs = [
        _FactSpec(
            fact_group="crm_collateral",
            category=category,
            amount=amount,
            derived_from="LOAN crm_collateral_*/crm_guarantee_* attribute conventions",
        )
        for category, amount in sorted(totals.items())
    ]
    if specs:
        groups.append(GroupResult(group="crm_collateral", status="derived", rows=len(specs)))
    else:
        groups.append(
            GroupResult(
                group="crm_collateral",
                status="skipped",
                note="Not computable: no LOAN position carries the documented "
                "crm_collateral_*/crm_guarantee_* attributes, so no credit-risk mitigation "
                "is recognised. Credit exposures are risk-weighted GROSS of collateral — "
                "conservative, and never a modelled reduction.",
            )
        )
    return specs


def _derive_securities(
    securities: _SecuritiesSplit,
    cash_amounts: dict[str, Decimal],
    groups: list[GroupResult],
) -> list[_FactSpec]:
    """The HQLA stock, one fact per established Basel level.

    Every fact in this group carries the level ``_classify_security_hqla``
    established from the canonical evidence, and a holding whose level could
    NOT be established carries ``hqla_level=None`` — which
    ``liquidity.engine.compute_lcr`` filters out of the HQLA stock entirely
    (``fact.hqla_level is not None``). It is still emitted, at the same amount
    and the same risk-weight code, so the group continues to tie to the
    balance-sheet securities lines and no capital, NSFR or stress figure moves;
    only the LCR numerator does, and only downwards.
    """
    specs = [
        _FactSpec(
            fact_group="securities",
            category="bog_bills",
            amount=securities.l1_bills,
            hqla_level="L1",
            risk_weight_code="RW0",
            derived_from="SECURITY_HOLDING positions (bills), Basel HQLA Level 1",
        ),
        _FactSpec(
            fact_group="securities",
            category="gog_bonds",
            amount=securities.l1_bonds,
            hqla_level="L1",
            risk_weight_code="RW0",
            derived_from="SECURITY_HOLDING positions (bonds), Basel HQLA Level 1",
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
    for level, amount in (("L2A", securities.level2a), ("L2B", securities.level2b)):
        if amount > _ZERO:
            specs.append(
                _FactSpec(
                    fact_group="securities",
                    category=f"hqla_level{level[1:].lower()}",
                    amount=amount,
                    hqla_level=level,
                    risk_weight_code="RW0",
                    derived_from=(
                        f"SECURITY_HOLDING positions classified Basel HQLA {level} "
                        "(attributes.hqla_level)"
                    ),
                )
            )
    warnings: list[str] = []
    if securities.unclassified > _ZERO:
        specs.append(
            _FactSpec(
                fact_group="securities",
                category="hqla_unclassified",
                amount=securities.unclassified,
                hqla_level=None,
                risk_weight_code="RW0",
                derived_from="SECURITY_HOLDING positions with no establishable Basel HQLA level",
            )
        )
    for reason, amount, references in securities.exclusions:
        warnings.append(
            f"{money(amount)} of SECURITY_HOLDING positions are EXCLUDED from HQLA: "
            f"{reason}. They are carried at full value on the balance sheet and are "
            f"unchanged for capital; they earn no LCR credit: {_shown(references)}."
        )
    groups.append(
        GroupResult(group="securities", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


def _derive_off_balance(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    warnings: list[str] = []
    totals: dict[str, tuple[Decimal, Decimal]] = {}  # category -> (Σ notional, Σ notional×ccf)
    missing_ccf = 0
    guarantees = canonical.by_type("LC_GUARANTEE")
    unstated_notional: list[str] = []
    for row in guarantees:
        notional = row.notional_ghs if row.notional_ghs > _ZERO else row.balance_ghs
        if notional is None:
            # A foreign-currency commitment with neither a reporting-currency
            # notional nor a reporting-currency balance. Its exposure at default
            # is unknown; a zero EAD would UNDERSTATE risk-weighted assets, which
            # is the unsafe direction, so it is excluded and named.
            unstated_notional.append(row.source_reference)
            continue
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
    if unstated_notional:
        warnings.append(
            f"{len(unstated_notional)} off-balance positions carry neither a "
            f"{canonical.base_currency} notional nor a {canonical.base_currency} balance, so "
            "their exposure at default cannot be established; they are EXCLUDED from the "
            "off-balance book rather than counted at zero, which would understate "
            f"risk-weighted assets: {_shown(unstated_notional)}."
        )
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
            balance = loan.row.balance_ghs
            if maturity is None or maturity > window_end or balance is None:
                continue
            if loan.category in _RETAIL_LOAN_CATEGORIES:
                retail += balance
            else:
                corporate += balance
        derived_from = "LOAN positions maturing within 30 days"
    else:
        # Audit §3: this branch booked 2% of GROSS LOANS as a 30-day LCR inflow
        # on a book with no maturities at all. Inflows reduce net cash outflows,
        # so an invented inflow RAISES the LCR — the least safe direction for a
        # substitution. Absence of evidence of a repayment is not a repayment:
        # the segments book zero and the shortfall is named.
        derived_from = (
            "not computable: no LOAN position carries a contractual maturity, so no "
            "30-day repayment inflow is evidenced (missing_required_input; booked at "
            "zero rather than assumed)"
        )
        warnings.append(
            "No loan positions carry contractual maturities, so 30-day loan repayment "
            "inflows are NOT COMPUTABLE and are booked at zero. The LCR is understated "
            "until contractual maturities are ingested — it is never overstated by an "
            "assumed inflow."
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
            balance = loan.row.balance_ghs
            if cpr is None or balance is None:
                continue
            expected = _expected_prepaid_30d(balance, cpr)
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
            amount
            for row, amount in _stated(canonical.by_type("INTERBANK_PLACEMENT"))
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


@dataclass
class _FxLeg:
    """One currency's on-balance FX book, measured on two different legs.

    The legs are built from DIFFERENT evidence and must not be conflated. The
    currency leg is each position's own ingested balance in its own currency —
    always present, never converted, so every position counts. The
    reporting-currency leg exists only for positions carrying an ingested
    conversion; the rest are EXCLUDED and COUNTED in ``unconverted``, exactly as
    ``sdi_capital._exposure_by_bucket`` excludes and counts them.

    The asymmetry is deliberate and is the whole point of D-21. Before this,
    ``_position_row`` handed the reporting-currency leg a fabricated ZERO for an
    unconverted position while the currency leg took it in full, so a book short
    USD 144.7m was filed LONG 21.0m cedis — one position, measured twice,
    disagreeing about its own direction. Nothing here decides what to do about
    that: ``regulatory_fx._unstatable_position`` refuses on the contradiction,
    and this module's job is only to stop manufacturing it.
    """

    assets_ccy: Decimal = _ZERO
    liabilities_ccy: Decimal = _ZERO
    assets_reporting: Decimal = _ZERO
    liabilities_reporting: Decimal = _ZERO
    unconverted: int = 0

    @property
    def net_ccy(self) -> Decimal:
        return self.assets_ccy - self.liabilities_ccy

    @property
    def net_reporting(self) -> Decimal:
        """The net over the positions that carry a conversion — a PARTIAL net
        whenever ``unconverted`` is non-zero, never a complete one."""
        return self.assets_reporting - self.liabilities_reporting

    @property
    def leg_complete(self) -> bool:
        return self.unconverted == 0


def _fx_legs(canonical: _Canonical) -> dict[str, _FxLeg]:
    """The on-balance FX book per foreign currency. See :class:`_FxLeg`."""
    legs: dict[str, _FxLeg] = {}
    for row in canonical.positions:
        if row.currency == canonical.base_currency or row.position_type == "LC_GUARANTEE":
            continue
        is_asset = row.position_type in _FX_ASSET_TYPES
        if not is_asset and row.position_type not in _FX_LIABILITY_TYPES:
            continue
        leg = legs.setdefault(row.currency, _FxLeg())
        if is_asset:
            leg.assets_ccy += row.balance
        else:
            leg.liabilities_ccy += row.balance
        if row.balance_ghs is None:
            leg.unconverted += 1
        elif is_asset:
            leg.assets_reporting += row.balance_ghs
        else:
            leg.liabilities_reporting += row.balance_ghs
    return legs


def _derive_fx_positions(
    canonical: _Canonical, groups: list[GroupResult]
) -> tuple[list[_FactSpec], set[str]]:
    warnings: list[str] = []
    spots = _spot_rates(canonical)
    with_history = _historical_currencies(canonical)

    legs = _fx_legs(canonical)
    hedge_deltas = _fx_hedge_deltas(canonical, warnings)

    currencies = sorted(set(legs) | set(hedge_deltas))
    specs: list[_FactSpec] = []
    included: set[str] = set()
    net_long = _ZERO
    net_short = _ZERO
    for currency in currencies:
        leg = legs.get(currency, _FxLeg())
        base_ccy = leg.net_ccy
        base_ghs = leg.net_reporting
        delta = hedge_deltas.get(currency, _ZERO)
        unconverted = leg.unconverted
        if unconverted:
            warnings.append(
                f"{unconverted} {currency} positions carry no ingested "
                f"{canonical.base_currency} balance, so the {currency} net open position is "
                f"stated in {currency} over the whole book but in {canonical.base_currency} "
                "over only part of it. The two legs therefore describe different books and "
                "the position cannot be filed as it stands. Ingest attributes.balance_ghs on "
                f"those positions, or the {currency} rate behind it."
            )
        # The spot resolves from the on-balance book before hedge deltas apply,
        # so an implied fallback rate stays consistent with the position data.
        # It is REQUIRED only to convert a hedge delta; without a delta the net
        # is already in base currency and needs no rate.
        resolved = _resolve_spot(currency, spots.get(currency), leg, warnings)
        if resolved is None and delta != _ZERO:
            # Audit §3: this branch used to substitute a 1.0 spot, valuing the
            # hedge leg at par with the base currency. Nothing is invented now.
            warnings.append(
                f"{currency} was excluded from the FX book: its FX_HEDGE legs cannot be "
                "converted because no spot rate was ingested and none is implied by the "
                "position book (missing_required_input). Ingest an fx_rates_current row "
                f"for {currency}."
            )
            continue
        spot = resolved[0] if resolved is not None else None
        spot_source = resolved[1] if resolved is not None else "not_required"
        net_ccy = base_ccy + delta
        net_ghs = base_ghs + (delta * spot if (delta != _ZERO and spot is not None) else _ZERO)
        # The open position drives the FX capital charge and the NOP limits, so
        # it covers EVERY currency the bank holds. Only the per-currency VaR row
        # needs a return history (audit §3: a currency with no history used to
        # vanish from the book entirely, understating the capital charge).
        if net_ghs >= _ZERO:
            net_long += net_ghs
        else:
            net_short += -net_ghs
        if currency not in with_history:
            warnings.append(
                f"{currency} carries no ingested daily return history, so it has no VaR "
                f"row; its net of {money(net_ghs)} {canonical.base_currency} IS included "
                "in the net open position and therefore in the FX capital charge."
            )
            continue
        included.add(currency)
        attributes = {
            "currency": currency,
            "side": "long" if net_ghs >= _ZERO else "short",
            "spot_ghs": str(spot) if spot is not None else "",
            "net_ccy": str(money(net_ccy)),
            "assets_ccy": str(money(leg.assets_ccy)),
            "liabilities_ccy": str(money(leg.liabilities_ccy)),
            "net_derivatives_ccy": str(money(delta)),
            "net_ghs": str(money(net_ghs)),
        }
        if spot_source != "ingested":
            # Only stamped when the rate did NOT come straight from ingested
            # data, so a book with proper spots hashes byte-identically.
            attributes["spot_source"] = spot_source
        if unconverted:
            # Stamped ONLY on an incomplete book, for the same reason
            # ``spot_source`` is: a book whose every position carries a
            # conversion hashes byte-identically to before, so ``input_hash``
            # moves for exactly the books whose reporting-currency leg is
            # partial — which is the fact worth recording in a sealed run.
            attributes["unconverted_position_count"] = str(unconverted)
        specs.append(
            _FactSpec(
                fact_group="fx_position",
                category=currency,
                amount=net_ghs,
                derived_from="per-currency net of position balance_ghs "
                "(assets − liabilities + signed FX_HEDGE notional deltas; "
                "LC/guarantees excluded as off-balance)",
                attributes=attributes,
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
    leg: _FxLeg,
    warnings: list[str],
) -> tuple[Decimal, str] | None:
    """(rate, source) for one currency, or ``None`` when no rate is knowable.

    Audit §3: the pre-audit form returned ``1.0`` when neither an ingested spot
    nor an implied rate existed, and that 1.0 then converted hedge deltas — a
    foreign-currency exposure silently valued at par with the base currency.
    A rate is never invented now: absence returns ``None`` and the caller
    excludes the currency from the FX book with a MISSING_REQUIRED_INPUT
    warning.

    ``leg.leg_complete`` says whether EVERY position in this currency carried an
    ingested reporting-currency balance. When it did not, the implied fallback is
    withdrawn (audit D-21): ``net_reporting / net_ccy`` divides a PARTIAL
    reporting-currency numerator by a complete currency denominator, so the
    "rate" it produces is an artefact of how much of the book was converted — on
    the measured book it came out NEGATIVE. A number that is not an exchange rate
    must not be published as one. An ingested rate is unaffected.
    """
    if spot is not None:
        return spot, "ingested"
    if not leg.leg_complete:
        warnings.append(
            f"No spot rate was ingested for {currency} and none was implied from its "
            f"position book, because part of that book carries no reporting-currency "
            "balance: an implied rate over an incomplete leg measures the conversion "
            "gap, not the exchange rate. Ingest an fx_rates_current row for "
            f"{currency}."
        )
        return None
    if leg.net_ccy != _ZERO:
        implied = (leg.net_reporting / leg.net_ccy).quantize(Decimal("0.000001"))
        warnings.append(
            f"No current spot rate was ingested for {currency}; the implied rate "
            f"{implied} from the position book was used."
        )
        return implied, "implied_from_position_book"
    return None


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
        pair = (
            str(attributes.get("currency_pair") or f"{row.currency}/{canonical.base_currency}")
            .strip()
            .upper()
        )
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
                "effectiveness measures; amount is the hedge MtM in "
                f"{canonical.base_currency}",
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
    months: list[tuple[date, dict[str, Decimal | None]]] = []
    for payload in canonical.refs.get("historical_financials", ()):
        period_end = str(payload.get("period_end", ""))
        nii = _dec_or_none(payload.get("net_interest_income_ghs"))
        if not period_end or nii is None:
            continue
        fees = _dec_or_none(payload.get("non_interest_income_ghs"))
        months.append(
            (
                date.fromisoformat(period_end),
                {
                    "gross_income": nii + (fees or _ZERO),
                    "net_interest_income": nii,
                    "net_income": _dec_or_none(payload.get("net_income_ghs")),
                    "operating_expenses": _dec_or_none(payload.get("operating_expenses_ghs")),
                    "provisions": _dec_or_none(payload.get("provisions_ghs")),
                },
            )
        )
    months.sort()

    specs: list[_FactSpec] = []
    remaining = months
    for _ in range(3):
        if len(remaining) < 12:
            break
        window = remaining[-12:]
        remaining = remaining[:-12]
        year = window[-1][0].year
        metrics = {
            "gross_income": "trailing 12-month gross income (net interest + non-interest)",
            "net_interest_income": "trailing 12-month net interest income",
            "net_income": "trailing 12-month net income",
            "operating_expenses": "trailing 12-month operating expenses",
            "provisions": "trailing 12-month credit-loss provisions",
        }
        for metric, description in metrics.items():
            amounts = [values[metric] for _, values in window]
            if any(amount is None for amount in amounts):
                continue
            specs.append(
                _FactSpec(
                    fact_group="operational_income",
                    category=f"{metric}_{year}",
                    amount=sum((amount for amount in amounts if amount is not None), _ZERO),
                    income_year=year,
                    derived_from=f"{description} from historical_financials",
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


def _derive_cashflow_summary(canonical: _Canonical, groups: list[GroupResult]) -> list[_FactSpec]:
    """Trailing 90-day actual cash-flow summary from canonical ETL rows."""
    observations: list[tuple[date, Decimal, Decimal, Decimal]] = []
    for payload in canonical.refs.get("historical_cashflows", ()):
        raw_date = str(payload.get("date", ""))
        inflows = _dec_or_none(payload.get("deposit_inflow_ghs"))
        outflows = _dec_or_none(payload.get("deposit_outflow_ghs"))
        net = _dec_or_none(payload.get("net_cashflow_ghs"))
        if not raw_date or inflows is None or outflows is None:
            continue
        observations.append(
            (date.fromisoformat(raw_date), inflows, outflows, net or inflows - outflows)
        )
    observations.sort(key=lambda item: item[0])
    if not observations:
        groups.append(
            GroupResult(
                group="cashflow",
                status="skipped",
                note="No canonical historical cash flows were ingested.",
            )
        )
        return []
    window = observations[-90:]
    specs = [
        _FactSpec(
            fact_group="cashflow",
            category="inflows_90d",
            amount=sum((inflow for _, inflow, _, _ in window), _ZERO),
            derived_from="latest 90 canonical historical_cashflows inflows",
        ),
        _FactSpec(
            fact_group="cashflow",
            category="outflows_90d",
            amount=sum((outflow for _, _, outflow, _ in window), _ZERO),
            derived_from="latest 90 canonical historical_cashflows outflows",
        ),
        _FactSpec(
            fact_group="cashflow",
            category="net_cashflow_90d",
            amount=sum((net for _, _, _, net in window), _ZERO),
            derived_from="latest 90 canonical historical_cashflows net cash flow",
        ),
    ]
    groups.append(GroupResult(group="cashflow", status="derived", rows=len(specs)))
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
    #: Balance placed in this bucket by a DEFAULT rather than by an ingested
    #: repricing horizon (audit §3: the defaults were asymmetric — a horizonless
    #: asset went to "5y+", a horizonless interbank line to "overnight" — and
    #: nothing said so. The amount is now counted, warned and stamped.)
    defaulted_balance: Decimal = _ZERO

    def add(
        self, balance: Decimal, rate: Decimal, is_fixed: bool, *, defaulted: bool = False
    ) -> None:
        self.balance += balance
        self.weighted_rate += balance * rate
        if is_fixed:
            self.fixed_balance += balance
        if defaulted:
            self.defaulted_balance += balance


def _derive_irr_positions(
    canonical: _Canonical,
    loan_rows: list[_LoanRow],
    groups: list[GroupResult],
) -> list[_FactSpec]:
    warnings: list[str] = []
    durations = _nmd_duration_months(canonical)
    cells: dict[tuple[str, str, str], _IrrCell] = {}
    excluded_core = _ZERO
    irr_unconverted: list[_PositionRow] = []

    defaulted_by_reason: dict[str, Decimal] = {}

    def add(
        side: str, family: str, bucket: str, row: _PositionRow, *, defaulted_reason: str | None
    ) -> None:
        balance = row.balance_ghs
        if balance is None:
            # The repricing gap is measured in the reporting currency. A position
            # with no reporting-currency amount belongs to no cell; it is counted
            # in ``irr_unconverted`` and named, never bucketed at zero.
            irr_unconverted.append(row)
            return
        rate = (row.interest_rate or _ZERO) * _HUNDRED
        is_fixed = row.rate_type != "FLOATING"
        cells.setdefault((side, family, bucket), _IrrCell()).add(
            balance, rate, is_fixed, defaulted=defaulted_reason is not None
        )
        if defaulted_reason is not None:
            defaulted_by_reason[defaulted_reason] = (
                defaulted_by_reason.get(defaulted_reason, _ZERO) + balance
            )

    def bucket_or_default(row: _PositionRow, fallback: str, label: str) -> tuple[str, str | None]:
        bucket = _repricing_bucket(row, canonical.as_of)
        if bucket is not None:
            return bucket, None
        return fallback, f"{label} with no repricing horizon or maturity → '{fallback}'"

    for loan in loan_rows:
        bucket, reason = bucket_or_default(loan.row, _IRR_BUCKETS[-1][0], "loans")
        add("asset", _loan_family(loan.category), bucket, loan.row, defaulted_reason=reason)
    for row in canonical.by_type("SECURITY_HOLDING"):
        bucket, reason = bucket_or_default(row, _IRR_BUCKETS[-1][0], "securities")
        add("asset", "securities", bucket, row, defaulted_reason=reason)
    for row in canonical.by_type("INTERBANK_PLACEMENT"):
        bucket, reason = bucket_or_default(row, "overnight", "interbank placements")
        add("asset", "interbank_placements", bucket, row, defaulted_reason=reason)

    for row in canonical.by_type("DEPOSIT"):
        placement = _deposit_irr_placement(row, canonical.as_of, durations)
        if placement is None:
            if row.balance_ghs is None:
                irr_unconverted.append(row)
                continue
            excluded_core += row.balance_ghs  # zero-rate NMD core: non-rate-sensitive
            continue
        family, bucket, reason = placement
        add("liability", family, bucket, row, defaulted_reason=reason)
    for row in canonical.by_type("INTERBANK_BORROWING"):
        bucket, reason = bucket_or_default(row, "overnight", "interbank borrowings")
        add("liability", "interbank_borrowings", bucket, row, defaulted_reason=reason)

    sub_debt = _subordinated_debt(canonical)
    if sub_debt > _ZERO:
        long_rate = _long_curve_rate(canonical)
        cells.setdefault(("liability", "subordinated_debt", "5y+"), _IrrCell()).add(
            sub_debt, long_rate, True
        )
    warnings.extend(_irr_warnings(canonical, excluded_core, irr_unconverted, defaulted_by_reason))

    specs = _irr_specs(cells)
    groups.append(
        GroupResult(group="irr_position", status="derived", rows=len(specs), warnings=warnings)
    )
    return specs


def _irr_specs(cells: Mapping[tuple[str, str, str], _IrrCell]) -> list[_FactSpec]:
    """One ``irr_position`` fact per non-empty (side, family, repricing bucket)."""
    specs: list[_FactSpec] = []
    for (side, family, bucket), cell in sorted(cells.items()):
        if cell.balance <= _ZERO:
            continue
        rate_pct = (cell.weighted_rate / cell.balance).quantize(RATE)
        fixed_or_float = "fixed" if cell.fixed_balance * 2 >= cell.balance else "float"
        slug = bucket.replace("-", "_").replace("+", "plus")
        attributes = {
            "side": side,
            "bucket": bucket,
            "fixed_or_float": fixed_or_float,
            "rate_pct": str(rate_pct),
            "midpoint_years": _BUCKET_MIDPOINT[bucket],
        }
        if cell.defaulted_balance > _ZERO:
            # Only stamped where a default actually placed balance, so a fully
            # dated book derives (and hashes) exactly as before.
            attributes["defaulted_balance"] = str(money(cell.defaulted_balance))
        specs.append(
            _FactSpec(
                fact_group="irr_position",
                category=f"{family}_{slug}",
                amount=cell.balance,
                derived_from="positions aggregated by repricing bucket "
                "(float → next repricing, fixed → maturity, NMD → behavioral)",
                attributes=attributes,
            )
        )
    return specs


def _subordinated_debt(canonical: _Canonical) -> Decimal:
    """Subordinated debt from the ingested capital structure.

    It prices as a long fixed liability at the ingested curve's long end: the
    canonical model carries no instrument-level terms for it.
    """
    return sum(
        (
            _dec(payload.get("amount_ghs"), _ZERO)
            for payload in canonical.refs.get("capital_structure", ())
            if "SUBORDINATED" in str(payload.get("capital_component", "")).upper()
        ),
        _ZERO,
    )


def _irr_warnings(
    canonical: _Canonical,
    excluded_core: Decimal,
    unconverted_rows: list[_PositionRow],
    defaulted_by_reason: dict[str, Decimal],
) -> list[str]:
    """Everything the repricing book left out or assumed, stated in one place."""
    warnings: list[str] = []
    if excluded_core > _ZERO:
        warnings.append(
            f"{money(excluded_core)} {canonical.base_currency} of zero-rate non-maturity "
            "deposits were excluded from the rate-sensitive book as the behavioral core."
        )
    unconverted = _Unconverted.over(unconverted_rows)
    if unconverted:
        warnings.append(unconverted.note("rate-sensitive positions", canonical.base_currency))
    for reason, amount in sorted(defaulted_by_reason.items()):
        warnings.append(
            f"{money(amount)} {canonical.base_currency} was bucketed by a DEFAULT, not by "
            f"an ingested repricing horizon: {reason}. IRRBB duration and EVE sensitivity "
            "for that balance are an assumption, not a measurement."
        )
    return warnings


def _deposit_irr_placement(
    row: _PositionRow, as_of: date, durations: dict[str, Decimal]
) -> tuple[str, str, str | None] | None:
    """(family, bucket, defaulted-reason) for one deposit.

    ``None`` marks the non-rate-sensitive behavioural core. The third element is
    ``None`` when the bucket came from ingested data and a short reason when a
    default placed it.
    """
    bucket = _repricing_bucket(row, as_of)
    if bucket is not None:
        family = (
            "term_deposits_retail" if _is_retail_deposit_product(row) else "term_deposits_wholesale"
        )
        return family, bucket, None
    if (row.interest_rate or _ZERO) <= _ZERO:
        return None
    if _is_retail_deposit_product(row):
        return (
            "savings_repricing",
            _SAVINGS_REPRICING_BUCKET,
            "retail non-maturity deposits with no repricing horizon → "
            f"'{_SAVINGS_REPRICING_BUCKET}' (documented savings-repricing assumption)",
        )
    months = durations.get(row.product_code or "")
    if months is None:
        return (
            "wholesale_call",
            "overnight",
            "wholesale call deposits with no NMD_DURATION assumption → 'overnight'",
        )
    return "wholesale_call", _bucket_for_days(int(months * Decimal("30.44"))), None


_INDEX_RESET = re.compile(r"^(\d+)\s*([dmy])")
_DEFAULT_INDEX_RESET_DAYS = 91  # the 91-day T-Bill, Ghana's standard floating index
_DEFAULT_RECEIVE_INDEX = "91d_tbill"
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


def _derive_irr_swaps(  # noqa: PLR0912, PLR0915 - one linear swap decomposition
    canonical: _Canonical, groups: list[GroupResult]
) -> list[_FactSpec]:
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
        raw_direction = attributes.get("direction")
        if raw_direction is None or not str(raw_direction).strip():
            # Audit §3: a swap with no stated direction used to be assumed
            # pay-fixed, which flips the sign of its entire rate sensitivity.
            warnings.append(
                f"Swap {swap_id}: no direction was ingested (pay_fixed | receive_fixed). "
                "The leg decomposition reverses with the direction, so the swap was "
                "excluded rather than assumed; IRR runs on the unhedged book for it."
            )
            continue
        direction = str(raw_direction).strip().lower()
        if direction not in ("pay_fixed", "receive_fixed"):
            warnings.append(
                f"Swap {swap_id}: direction {direction!r} is not supported (the IRR "
                "engine decomposes pay-fixed and receive-fixed swaps only); the swap "
                "was excluded."
            )
            continue
        notional = row.notional_ghs if row.notional_ghs > _ZERO else row.balance_ghs
        if notional is None or notional <= _ZERO:
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
        raw_index = attributes.get("receive_index")
        receive_index = str(raw_index or _DEFAULT_RECEIVE_INDEX).strip().lower()
        if not raw_index or not str(raw_index).strip():
            warnings.append(
                f"Swap {swap_id}: no receive_index was ingested; the floating leg is "
                f"bucketed on the documented {_DEFAULT_RECEIVE_INDEX} reset "
                f"({_DEFAULT_INDEX_RESET_DAYS} days)."
            )
        elif _INDEX_RESET.match(receive_index) is None:
            warnings.append(
                f"Swap {swap_id}: receive_index {receive_index!r} carries no parseable "
                f"reset tenor; the documented {_DEFAULT_INDEX_RESET_DAYS}-day reset was "
                "used to bucket the floating leg."
            )
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
        if market_curve.curve_name == desk_projection_curve_name(canonical.base_currency):
            # Selection provenance: the desk sovereign zero was preferred over
            # currency-level vendor arbitration (curve platform spec §13
            # Stage 2); the arbitration winner keeps the unmarked string.
            base_source += ", desk-published sovereign zero preferred"
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
                note=f"No {canonical.base_currency} yield curve was ingested; FTP runs "
                "will fail without a transfer curve.",
                warnings=[
                    "ftp_curve_point could not be derived — no "
                    f"{canonical.base_currency} yield curve."
                ],
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


def _ftp_unconverted_warnings(
    books: Mapping[tuple[str, str], _FtpBook], base_currency: str
) -> list[str]:
    """What the priced books left out for want of a reporting-currency amount."""
    counts: dict[str, int] = {}
    for book in books.values():
        for currency in book.unconverted:
            counts[currency] = counts.get(currency, 0) + 1
    if not counts:
        return []
    return [_Unconverted(counts=counts).note("priced positions", base_currency)]


@dataclass
class _FtpBook:
    balance: Decimal = _ZERO
    weighted_rate: Decimal = _ZERO
    weighted_tenor_days: Decimal = _ZERO
    tenor_weight: Decimal = _ZERO
    ecl: Decimal = _ZERO

    #: Positions this book could not take in, because they carry no
    #: reporting-currency amount. Counted, never absorbed at zero — a zero-weight
    #: observation would silently thin the balance-weighted rate and tenor.
    unconverted: list[str] = field(default_factory=list)

    def add(self, row: _PositionRow, as_of: date) -> None:
        balance = row.balance_ghs
        if balance is None:
            self.unconverted.append(row.currency)
            return
        self.balance += balance
        self.weighted_rate += balance * (row.interest_rate or _ZERO) * _HUNDRED
        if row.contractual_maturity is not None:
            days = Decimal(max((row.contractual_maturity - as_of).days, 0))
            self.weighted_tenor_days += balance * days
            self.tenor_weight += balance
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
        books.setdefault(("asset", _loan_family(loan.category)), _FtpBook()).add(
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
    groups.append(
        GroupResult(
            group="ftp_product",
            status="derived",
            rows=len(specs),
            warnings=_ftp_unconverted_warnings(books, canonical.base_currency),
        )
    )
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
    for row, amount in _stated(canonical.positions):
        if row.branch_id is None:
            continue
        branch = names.get(row.branch_id, row.branch_id.lower().replace("-", "_"))
        if row.position_type == "DEPOSIT":
            deposits[branch] = deposits.get(branch, _ZERO) + amount
        elif row.position_type == "LOAN":
            loans[branch] = loans.get(branch, _ZERO) + amount
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
    # Term deposits are not NMDs, so the population is filtered BEFORE the
    # unconverted count is taken — the count must measure what this book tried
    # to price, not the whole deposit ledger.
    nmd_rows = [row for row in canonical.by_type("DEPOSIT") if row.contractual_maturity is None]
    unconverted = _Unconverted.over(nmd_rows)
    if unconverted:
        warnings.append(unconverted.note("non-maturity deposits", canonical.base_currency))
    for row, balance in _stated(nmd_rows):
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
        segment.balance += balance
        segment.weighted_core += balance * share * _HUNDRED
        segment.weighted_duration += balance * months / _TWELVE

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
