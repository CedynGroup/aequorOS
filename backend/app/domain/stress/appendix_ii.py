"""Appendix II Tables 1–6 builders (docs/stress.md §1.8, Phase 2 item 3).

The regulatory deliverable itself: the BoG Stress Testing Guideline Appendix II
prescribes six tables that ARE the annual ICAAP stress submission (¶67, ¶68).
This module turns one :class:`EnterpriseProjection` (base + stress, ≥3 years)
into those exact structures — pure, deterministic, Decimal-only. It reads the
projection's per-year ``RwaResult``/``CapitalRatiosResult`` (the authoritative
capital build the pure engines produced) and never re-derives a ratio.

Amounts are reported in **thousands of the institution's own reporting currency**
(``<currency>'000``) per the directive convention; ratios stay as
percentages. The projection passed here is the required "results WITHOUT
management actions" output (¶67(f)); when the run also models a management-action
plan, its :class:`ManagementActionsResult` is passed as ``management_actions`` and
the Phase 3 overlay fills Table 1's "Management actions", "Post-capitalisation"
and residual-capital blocks (real values). With no plan those blocks stay an
explicit ``None`` — a fabricated zero would misrepresent the pre-action position.

The one hard cross-table invariant the directive names — **stressed Total
Pillar-1 RWA (Table 5) must equal Table 1's stressed RWA** — holds by
construction (both read the same stress projection's ``rwa.total_rwa``) and is
asserted in :func:`build_appendix_ii`.

Where a directive line is not modelled in Phase 2 it is reported as ``None`` with
its slot preserved (Pillar-2 add-ons — the ICAAP assessment; the interest
income/expense split; trading income; D&A) rather than dropped, so the Phase 3/5
consumers see the full contract. The exposure-class loss allocation (Table 1) is
a documented credit-RWA-share allocation of the adverse impairment; the
exposure-level bottom-up decomposition is Phase 4.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.authority.outcomes import (
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    outcome,
)
from app.domain.capital.engine import CapitalLineItem, CapitalRatiosResult, RwaResult
from app.domain.stress.credit_bottom_up import CRD_EXPOSURE_CLASSES
from app.domain.stress.management_actions import ManagementActionsResult, PostActionYear
from app.domain.stress.projection import (
    EnterpriseProjection,
    ProjectedYear,
    component_breakdown,
)
from app.domain.stress.translation import MacroPathPoint

__all__ = [
    "CRD_EXPOSURE_CLASSES",
    "AppendixIITables",
    "Pillar2Requirement",
    "Table1ManagementActions",
    "Table1ManagementActionsRow",
    "Table1Residual",
    "build_appendix_ii",
    "build_table6",
    "thousands",
]

_THOUSAND = Decimal("1000")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
MONEY = Decimal("0.001")  # GHS'000 to three decimals

# There is deliberately NO ``DEFAULT_CAR_TARGET_PCT`` here (audit 2026-08-22
# D-15). This module used to carry ``Decimal("13")``, applied to Table 1's
# "capital required at CAR target", Table 5's Pillar-1 requirement and the
# management-action RWA-relief valuation. Three things were wrong with it:
#
# * **It is a regulatory floor, and floors are governed, effective-dated data.**
#   The minimum CAR a Ghanaian bank must hold is BoG CRD ¶71's 10% plus ¶75's
#   capital conservation buffer, and BoG has moved that buffer more than once —
#   the total minimum has been 13%, 11.5%, 13%, 10% and 13% over the life of the
#   directive. A literal cannot be any of those on the right date; the governed
#   ``car_min`` parameter (``regulatory_parameters``, effective-dated, clamped)
#   can be all of them.
# * **It is jurisdiction-specific inside a jurisdiction-neutral package.** 13 is a
#   Ghanaian figure and ``app/domain`` must stay neutral (CLAUDE.md).
# * **It is not even the right floor for every Ghanaian institution.** An SDI's
#   statutory minimum is Act 930 s.29's 10%, not 13% — so this literal computed a
#   savings-and-loans institution's Appendix II capital requirement against the
#   universal-bank floor.
#
# ``car_target_pct`` is therefore a REQUIRED argument of :func:`build_appendix_ii`
# and the caller resolves it from the control plane. Absence refuses; it is never
# assumed.

# AT1 is eligible up to 1.5% of RWA, Tier 2 up to 2% of RWA (AppII Table 2).
AT1_CAP_PCT_RWA = Decimal("1.5")
TIER2_CAP_PCT_RWA = Decimal("2")


def thousands(value: Decimal) -> Decimal:
    """GHS → GHS'000, three-decimal (the directive's reporting unit)."""
    return (value / _THOUSAND).quantize(MONEY, rounding=ROUND_HALF_UP)


# --- Named CET1 component slots (Table 2) ------------------------------------

_PAID_UP = frozenset(
    {"paid_up_capital", "stated_capital", "ordinary_shares", "common_equity", "share_capital"}
)
_STATUTORY = frozenset({"statutory_reserves", "statutory_reserve"})
_OTHER_RESERVES = frozenset({"other_reserves", "revaluation_reserves", "capital_surplus"})
_MINORITY = frozenset({"minority_interest", "minority_interests"})
_RETAINED = frozenset({"retained_earnings"})
_INTANGIBLES = frozenset({"intangibles", "goodwill", "intangible_assets"})
_DTA = frozenset({"deferred_tax_assets", "dta"})
_FI_INVEST = frozenset({"fi_investments", "financial_institution_investments"})
_OCI = frozenset({"oci", "accumulated_oci"})
_COMMERCIAL = frozenset({"commercial_entity_investment", "commercial_investments"})


# --- CRD exposure classes (Table 1 "Impact of Adverse") ----------------------
# ``CRD_EXPOSURE_CLASSES`` is imported from ``credit_bottom_up`` (its canonical
# home — the bottom-up decomposition and this fallback allocator read one list).


def _crd_class(line: CapitalLineItem) -> str:  # noqa: PLR0911 - a flat classifier
    code = line.line_code.lower()
    desc = line.description.lower()
    haystack = f"{code} {desc}"
    if "gog" in haystack or "government" in haystack:
        return "gog"
    if "bog" in haystack or "central bank" in haystack:
        return "bog"
    if "past_due" in haystack or "past due" in haystack:
        return "past_due"
    if "high_risk" in haystack or "high risk" in haystack:
        return "high_risk"
    if "sme" in haystack or "retail" in haystack or "mortgage" in haystack:
        return "retail_sme"
    if "corporate" in haystack or "commercial" in haystack:
        return "corporates"
    if "bank" in haystack:
        return "banks"
    if "sovereign" in haystack:
        return "other_sovereigns_central_banks"
    return "other"


# --- Table dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class CapitalSnapshot:
    """One period's headline capital build + ratios (GHS'000, percentages)."""

    label: str
    cet1: Decimal
    tier1: Decimal
    tier2: Decimal
    total_regulatory_capital: Decimal
    total_rwa: Decimal
    cet1_ratio_pct: Decimal
    tier1_ratio_pct: Decimal
    car_pct: Decimal
    paid_up: Decimal


@dataclass(frozen=True)
class ExposureClassLoss:
    exposure_class: str
    loss: Decimal


@dataclass(frozen=True)
class Table1ManagementActionsRow:
    """One stress year of Appendix II Table 1's "Management actions" block (GHS'000).

    The capital-raise lines carry the capital INJECTED (split by CRD tier). The
    dividend-revision line carries the CET1 PRESERVED. The RWA-relief lines (change
    in business strategy, sale of assets, risk reduction, other) are expressed as
    the CAPITAL EQUIVALENT of the RWA reduction (relief × the CAR target), so every
    line and the "Total Management Actions" are on one comparable capital basis;
    the raw RWA relief is carried alongside for transparency.
    """

    year: int
    capital_raised_cet1: Decimal
    capital_raised_at1: Decimal
    capital_raised_tier2: Decimal
    capital_raised_total: Decimal
    revision_of_dividend_policy: Decimal
    change_in_business_strategy: Decimal
    sale_of_assets: Decimal
    risk_reduction: Decimal
    other: Decimal
    total_management_actions: Decimal
    rwa_relief_total: Decimal


@dataclass(frozen=True)
class Table1ManagementActions:
    plan_id: str
    stays_above_all_minima: bool
    first_breach_year: int | None
    binding_minima: tuple[str, ...]
    rows: tuple[Table1ManagementActionsRow, ...]


@dataclass(frozen=True)
class Table1ResidualRow:
    year: int
    residual_capital_required: Decimal


@dataclass(frozen=True)
class Table1Residual:
    """Additional (residual) capital required after actions to meet all minima."""

    rows: tuple[Table1ResidualRow, ...]
    worst: Decimal


@dataclass(frozen=True)
class Table1Summary:
    car_target_pct: Decimal
    paid_up_min: Decimal
    current: CapitalSnapshot
    pre_adverse: tuple[CapitalSnapshot, ...]  # base leg, Y1..Y3
    post_adverse: tuple[CapitalSnapshot, ...]  # stress leg, Y1..Y3
    impact_of_adverse: tuple[tuple[int, tuple[ExposureClassLoss, ...]], ...]
    capital_required_car_target: tuple[tuple[int, Decimal], ...]
    capital_required_paid_up: tuple[tuple[int, Decimal], ...]
    capital_gap: Decimal  # worst-year residual, pre-management-action
    # Phase 3 management-actions overlay. None when the run models no plan — the
    # pre-management-action projection (a required directive output) stands alone.
    management_actions: Table1ManagementActions | None = None
    post_capitalisation: tuple[CapitalSnapshot, ...] | None = None
    residual_capital_required_after_actions: Table1Residual | None = None


@dataclass(frozen=True)
class Cet1Build:
    paid_up: Decimal
    retained_earnings: Decimal
    statutory_reserves: Decimal
    other_reserves: Decimal
    minority_interest: Decimal
    other_cet1: Decimal
    gross_cet1: Decimal
    deduction_intangibles: Decimal
    deduction_fi_investments: Decimal
    deduction_oci: Decimal
    deduction_dta: Decimal
    deduction_commercial_entity: Decimal
    deduction_other: Decimal
    total_deductions: Decimal
    cet1_after_deductions: Decimal


@dataclass(frozen=True)
class Table2Row:
    label: str
    total_rwa: Decimal
    cet1: Cet1Build
    at1_nominal: Decimal
    at1_cap: Decimal
    at1_eligible: Decimal
    tier2_nominal: Decimal
    tier2_cap: Decimal
    tier2_eligible: Decimal
    total_regulatory_capital: Decimal
    #: BoG's **Credit Risk Reserve** — the excess of the BoG prudential provision
    #: over the IFRS 9 impairment, transferred out of income surplus, not
    #: distributable, and EXCLUDED from the adjusted capital base for CAR
    #: (Guide for Financial Publication BSD/2017 §2.2.1(ii)-(iv) p.10, §2.5 item
    #: 5 p.44; CRD June 2018 ¶32 omits it from CET1).
    #:
    #: ALWAYS ``None`` today. Until 2026-08-21 this line was fed
    #: ``general_provisions_amount(ratios)`` — the Tier-2 RECOGNISED general
    #: provisions, i.e. the capped allowance ADDED to Tier 2. That is a different
    #: quantity with the opposite capital sign, and it was being reported into a
    #: BoG template line. The correct figure is ``prudential provision - IFRS 9
    #: ECL``, and the enterprise projection carries no loan-classification
    #: dimension, so no prudential provision exists to subtract from. The repo's
    #: rule for a template cell with no honest source is to leave it unpopulated
    #: with a stated reason, never to substitute a plausible number: see
    #: ``AppendixIITables.not_computable``.
    credit_risk_reserve: Decimal | None


#: Why Appendix II's "Credit Risk Reserve" line is not populated.
#:
#: BoG's Credit Risk Reserve is ``BoG prudential provision - IFRS 9 impairment``,
#: appropriated from income surplus, non-distributable, and excluded from the
#: adjusted capital base (Guide for Financial Publication BSD/2017 §2.2.1(ii)-(iv)
#: p.10 and §2.5 item 5 p.44; CRD June 2018 ¶32 does not list it in CET1). The
#: enterprise projection models loans as aggregate categories and carries no loan
#: classification, so it computes no BoG prudential provision — there is nothing
#: to subtract the IFRS 9 impairment from. Reporting the Tier-2 recognised general
#: provisions here instead (the pre-2026-08-21 behaviour) reported a different
#: quantity with the OPPOSITE capital sign into a BoG template line.
CREDIT_RISK_RESERVE_NOT_COMPUTABLE: OutcomeDetail = outcome(
    OutcomeState.MISSING_REQUIRED_INPUT,
    metric_id="appendix_ii.table2.credit_risk_reserve",
    reason=(
        "The Credit Risk Reserve is the excess of the BoG prudential provision over "
        "the IFRS 9 impairment (Guide for Financial Publication BSD/2017 section "
        "2.2.1). The enterprise projection carries no loan classification, so no "
        "prudential provision is computed for any projected year and the reserve "
        "cannot be established. It is reported as not computed rather than "
        "substituted with the Tier 2 general provisions, which are a different "
        "quantity and move the capital base in the opposite direction."
    ),
    items=("fact:loan_classification", "metric:bog_prudential_provision"),
    context={"table": "appendix_ii_table2", "line": "credit_risk_reserve"},
)


@dataclass(frozen=True)
class Table2Capital:
    rows: tuple[Table2Row, ...]


@dataclass(frozen=True)
class Table3Row:
    label: str
    opening_retained_earnings: Decimal | None
    net_interest_income: Decimal
    fees_and_commissions: Decimal
    trading_income: Decimal | None
    other_income: Decimal | None
    operating_expenses: Decimal
    impairment_losses: Decimal
    depreciation_amortisation: Decimal | None
    profit_before_tax: Decimal
    tax: Decimal
    profit_after_tax: Decimal
    distributions: Decimal
    adjusted_retained_earnings_for_car: Decimal


@dataclass(frozen=True)
class Table3ProfitAndLoss:
    rows: tuple[Table3Row, ...]


@dataclass(frozen=True)
class Table4Row:
    label: str
    cash_and_balances: Decimal
    short_term_investments: Decimal
    loans: Decimal
    other_assets: Decimal
    total_assets: Decimal
    demand_deposits: Decimal
    savings_deposits: Decimal
    time_deposits: Decimal
    other_deposits: Decimal
    borrowings: Decimal
    total_liabilities: Decimal
    capital: Decimal


@dataclass(frozen=True)
class Table4FinancialPosition:
    rows: tuple[Table4Row, ...]


@dataclass(frozen=True)
class Pillar2Requirement:
    """Pillar-2 add-ons — the ICAAP assessment. Unmodelled in Phase 2 (None)."""

    credit_concentration: Decimal | None = None
    irrbb: Decimal | None = None
    sovereign: Decimal | None = None
    country_and_fx: Decimal | None = None
    reputational: Decimal | None = None
    other: Decimal | None = None

    @property
    def total(self) -> Decimal:
        return sum(
            (
                value
                for value in (
                    self.credit_concentration,
                    self.irrbb,
                    self.sovereign,
                    self.country_and_fx,
                    self.reputational,
                    self.other,
                )
                if value is not None
            ),
            _ZERO,
        )


@dataclass(frozen=True)
class Table5Row:
    label: str
    credit_rwa: Decimal
    operational_rwa: Decimal
    market_rwa: Decimal
    total_pillar1_rwa: Decimal
    pillar1_requirement: Decimal  # car_target% of total Pillar-1 RWA
    pillar2: Pillar2Requirement
    total_capital_requirement: Decimal


@dataclass(frozen=True)
class Table5Rwa:
    car_target_pct: Decimal
    rows: tuple[Table5Row, ...]


@dataclass(frozen=True)
class Table6DriverRow:
    variable: str
    year_index: int
    base_value: Decimal
    stress_value: Decimal


@dataclass(frozen=True)
class Table6RiskDrivers:
    rows: tuple[Table6DriverRow, ...]
    source: str | None = None


@dataclass(frozen=True)
class AppendixIITables:
    #: Appendix II lines this build could NOT establish from the projection, each
    #: carrying WS-A's fail-closed state, the metric, the reason and the specific
    #: missing inputs. A line listed here is serialized as ``null``, never as a
    #: plausible substitute (CLAUDE.md: a template cell with no honest source is
    #: ``input_required``, never dropped and never guessed).
    not_computable: tuple[OutcomeDetail, ...]
    #: The institution's OWN reporting currency (ISO code), resolved by the
    #: service from ``jurisdictions.base_currency(bank)``. The serialized ``unit``
    #: is ``"<currency>'000"``. Before 2026-08-21 the unit was the literal
    #: ``"GHS'000"``, so a non-Ghana tenant's ICAAP Appendix II was labelled in
    #: cedis whatever its reporting currency (CLAUDE.md: jurisdiction is data).
    #: REQUIRED and carrying no default — a currency that cannot be established
    #: is a skipped decision at the bank-creation site, not a Ghanaian bank.
    currency: str
    scenario_code: str
    horizon_years: int
    table1_summary: Table1Summary
    table2_capital: Table2Capital
    table3_profit_and_loss: Table3ProfitAndLoss
    table4_financial_position: Table4FinancialPosition
    table5_rwa: Table5Rwa
    table6_risk_drivers: Table6RiskDrivers

    def serialize(self) -> dict[str, object]:
        return _serialize_tables(self)


# --- Builders ----------------------------------------------------------------


def _snapshot(year: ProjectedYear, label: str) -> CapitalSnapshot:
    ratios = year.ratios
    return CapitalSnapshot(
        label=label,
        cet1=thousands(ratios.cet1_capital),
        tier1=thousands(ratios.tier1_capital),
        tier2=thousands(ratios.tier2_capital),
        total_regulatory_capital=thousands(ratios.total_capital),
        total_rwa=thousands(year.rwa.total_rwa),
        cet1_ratio_pct=ratios.cet1_ratio_pct,
        tier1_ratio_pct=ratios.tier1_ratio_pct,
        car_pct=ratios.car_pct,
        paid_up=thousands(year.paid_up),
    )


def _cet1_build(ratios: CapitalRatiosResult) -> Cet1Build:  # noqa: PLR0912 - a flat slot map
    tiers = component_breakdown(ratios)
    cet1 = tiers["CET1"]
    paid_up = retained = statutory = other_res = minority = other_cet1 = _ZERO
    intangibles = fi_invest = oci = dta = commercial = other_ded = _ZERO
    for category, amount in cet1.items():
        if amount >= _ZERO:
            if category in _PAID_UP:
                paid_up += amount
            elif category in _RETAINED:
                retained += amount
            elif category in _STATUTORY:
                statutory += amount
            elif category in _OTHER_RESERVES:
                other_res += amount
            elif category in _MINORITY:
                minority += amount
            else:
                other_cet1 += amount
        else:
            magnitude = -amount
            if category in _INTANGIBLES:
                intangibles += magnitude
            elif category in _FI_INVEST:
                fi_invest += magnitude
            elif category in _OCI:
                oci += magnitude
            elif category in _DTA:
                dta += magnitude
            elif category in _COMMERCIAL:
                commercial += magnitude
            else:
                other_ded += magnitude
    gross = paid_up + retained + statutory + other_res + minority + other_cet1
    total_ded = intangibles + fi_invest + oci + dta + commercial + other_ded
    return Cet1Build(
        paid_up=thousands(paid_up),
        retained_earnings=thousands(retained),
        statutory_reserves=thousands(statutory),
        other_reserves=thousands(other_res),
        minority_interest=thousands(minority),
        other_cet1=thousands(other_cet1),
        gross_cet1=thousands(gross),
        deduction_intangibles=thousands(intangibles),
        deduction_fi_investments=thousands(fi_invest),
        deduction_oci=thousands(oci),
        deduction_dta=thousands(dta),
        deduction_commercial_entity=thousands(commercial),
        deduction_other=thousands(other_ded),
        total_deductions=thousands(total_ded),
        cet1_after_deductions=thousands(gross - total_ded),
    )


def _table2_row(year: ProjectedYear, label: str) -> Table2Row:
    ratios = year.ratios
    total_rwa = year.rwa.total_rwa
    at1_cap = total_rwa * AT1_CAP_PCT_RWA / _HUNDRED
    tier2_cap = total_rwa * TIER2_CAP_PCT_RWA / _HUNDRED
    return Table2Row(
        label=label,
        total_rwa=thousands(total_rwa),
        cet1=_cet1_build(ratios),
        at1_nominal=thousands(ratios.at1_capital),
        at1_cap=thousands(at1_cap),
        at1_eligible=thousands(min(ratios.at1_capital, at1_cap)),
        tier2_nominal=thousands(ratios.tier2_capital),
        tier2_cap=thousands(tier2_cap),
        tier2_eligible=thousands(min(ratios.tier2_capital, tier2_cap)),
        total_regulatory_capital=thousands(ratios.total_capital),
        # Deliberately unpopulated — see Table2Row.credit_risk_reserve. Note that
        # the Tier-2 recognised general provisions are UNCHANGED and still inside
        # ``tier2_nominal`` / ``tier2_eligible`` above, where they belong.
        credit_risk_reserve=None,
    )


def _table3_row(year: ProjectedYear, label: str, opening_retained: Decimal | None) -> Table3Row:
    pnl = year.pnl
    return Table3Row(
        label=label,
        opening_retained_earnings=(
            None if opening_retained is None else thousands(opening_retained)
        ),
        net_interest_income=thousands(pnl.nii),
        fees_and_commissions=thousands(pnl.fees),
        trading_income=None,
        other_income=None,
        operating_expenses=thousands(pnl.opex),
        impairment_losses=thousands(pnl.credit_losses),
        depreciation_amortisation=None,
        profit_before_tax=thousands(pnl.pre_tax),
        tax=thousands(pnl.tax),
        profit_after_tax=thousands(pnl.net_income),
        distributions=thousands(pnl.dividends),
        adjusted_retained_earnings_for_car=thousands(pnl.retained),
    )


def _table4_row(year: ProjectedYear, label: str) -> Table4Row:
    bs = year.balance_sheet
    return Table4Row(
        label=label,
        cash_and_balances=thousands(bs.cash),
        short_term_investments=thousands(bs.short_term_investments),
        loans=thousands(bs.loans),
        other_assets=thousands(bs.other_assets),
        total_assets=thousands(bs.total_assets),
        demand_deposits=thousands(bs.demand_deposits),
        savings_deposits=thousands(bs.savings_deposits),
        time_deposits=thousands(bs.time_deposits),
        other_deposits=thousands(bs.other_deposits),
        borrowings=thousands(bs.borrowings),
        total_liabilities=thousands(bs.total_liabilities),
        capital=thousands(bs.capital),
    )


def _table5_row(
    year: ProjectedYear,
    label: str,
    car_target_pct: Decimal,
    pillar2: Pillar2Requirement | None = None,
) -> Table5Row:
    rwa: RwaResult = year.rwa
    requirement = rwa.total_rwa * car_target_pct / _HUNDRED
    if pillar2 is None:
        pillar2 = Pillar2Requirement()  # unmodelled where Phase 4 derives nothing
    # The Pillar-1 requirement is converted to GHS'000; ``pillar2`` values are
    # already GHS'000 (the service applies ``thousands`` before building the
    # add-ons), so the total is the sum of two '000 quantities — never a full-
    # GHS Pillar-2 folded into a '000 requirement.
    return Table5Row(
        label=label,
        credit_rwa=thousands(rwa.credit_rwa),
        operational_rwa=thousands(rwa.operational_rwa),
        market_rwa=thousands(rwa.market_rwa),
        total_pillar1_rwa=thousands(rwa.total_rwa),
        pillar1_requirement=thousands(requirement),
        pillar2=pillar2,
        total_capital_requirement=thousands(requirement) + pillar2.total,
    )


def _exposure_class_losses(
    base_year: ProjectedYear, stress_year: ProjectedYear
) -> tuple[ExposureClassLoss, ...]:
    """Allocate the adverse impairment across CRD classes by credit-RWA share.

    The adverse credit impact is the stress-minus-base impairment for the year;
    it is allocated to the CRD exposure classes in proportion to each class's
    share of the stressed credit RWA. A documented allocation — the exposure-
    level decomposition is Phase 4.
    """
    adverse_loss = max(stress_year.pnl.credit_losses - base_year.pnl.credit_losses, _ZERO)
    shares: dict[str, Decimal] = {name: _ZERO for name in CRD_EXPOSURE_CLASSES}
    total_credit_rwa = _ZERO
    for line in stress_year.rwa.line_items:
        if line.section != "credit_rwa":
            continue
        weight = line.weighted_amount
        if weight <= _ZERO:
            continue
        shares[_crd_class(line)] += weight
        total_credit_rwa += weight
    losses: list[ExposureClassLoss] = []
    allocated = _ZERO
    ordered = list(CRD_EXPOSURE_CLASSES)
    for name in ordered:
        if total_credit_rwa > _ZERO and name != ordered[-1]:
            loss = (adverse_loss * shares[name] / total_credit_rwa).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        elif name == ordered[-1]:
            loss = adverse_loss - allocated  # residual to the last class (no rounding drift)
        else:
            loss = _ZERO
        allocated += loss
        losses.append(ExposureClassLoss(exposure_class=name, loss=thousands(loss)))
    return tuple(losses)


def _bottom_up_class_losses(losses_ghs: Mapping[str, Decimal]) -> tuple[ExposureClassLoss, ...]:
    """Convert a bottom-up ``{crd_class: loss_ghs}`` map into Table 1 rows (GHS'000)."""
    return tuple(
        ExposureClassLoss(exposure_class=name, loss=thousands(losses_ghs.get(name, _ZERO)))
        for name in CRD_EXPOSURE_CLASSES
    )


def _post_cap_snapshot(year: PostActionYear) -> CapitalSnapshot:
    """A post-management-action (post-capitalisation) snapshot (GHS'000)."""
    return CapitalSnapshot(
        label=f"post_cap_y{year.year}",
        cet1=thousands(year.cet1),
        tier1=thousands(year.tier1),
        tier2=thousands(year.tier2),
        total_regulatory_capital=thousands(year.total_capital),
        total_rwa=thousands(year.total_rwa),
        cet1_ratio_pct=year.cet1_ratio_pct,
        tier1_ratio_pct=year.tier1_ratio_pct,
        car_pct=year.car_pct,
        paid_up=thousands(year.paid_up),
    )


def _management_actions_row(
    year: PostActionYear, car_target_pct: Decimal
) -> Table1ManagementActionsRow:
    """Build one Table 1 "Management actions" row from a post-action year (GHS'000)."""
    agg = year.aggregate
    car_frac = car_target_pct / _HUNDRED
    cet1_raised = thousands(agg.capital_raise_cet1)
    at1_raised = thousands(agg.capital_raise_at1)
    tier2_raised = thousands(agg.capital_raise_tier2)
    capital_raised_total = cet1_raised + at1_raised + tier2_raised
    dividend = thousands(agg.dividend_preserved)
    # RWA relief expressed as freed capital at the CAR target.
    strategy = thousands(agg.rwa_reduction_business_strategy * car_frac)
    sale = thousands(agg.rwa_reduction_sale_of_assets * car_frac)
    risk = thousands(agg.rwa_reduction_risk_reduction * car_frac)
    other = thousands(agg.rwa_reduction_other * car_frac)
    total_ma = capital_raised_total + dividend + strategy + sale + risk + other
    return Table1ManagementActionsRow(
        year=year.year,
        capital_raised_cet1=cet1_raised,
        capital_raised_at1=at1_raised,
        capital_raised_tier2=tier2_raised,
        capital_raised_total=capital_raised_total,
        revision_of_dividend_policy=dividend,
        change_in_business_strategy=strategy,
        sale_of_assets=sale,
        risk_reduction=risk,
        other=other,
        total_management_actions=total_ma,
        rwa_relief_total=thousands(agg.rwa_reduction_total),
    )


def _build_management_blocks(
    mgmt: ManagementActionsResult, car_target_pct: Decimal
) -> tuple[Table1ManagementActions, tuple[CapitalSnapshot, ...], Table1Residual]:
    rows = tuple(_management_actions_row(year, car_target_pct) for year in mgmt.post_action)
    actions_block = Table1ManagementActions(
        plan_id=mgmt.plan_id,
        stays_above_all_minima=mgmt.stays_above_all_minima,
        first_breach_year=mgmt.first_breach_year,
        binding_minima=mgmt.binding_minima,
        rows=rows,
    )
    post_cap = tuple(_post_cap_snapshot(year) for year in mgmt.post_action)
    residual_rows = tuple(
        Table1ResidualRow(
            year=year.year, residual_capital_required=thousands(year.residual_capital_required)
        )
        for year in mgmt.post_action
    )
    residual = Table1Residual(
        rows=residual_rows, worst=thousands(mgmt.residual_capital_required)
    )
    return actions_block, post_cap, residual


def _require_full_horizon_losses(
    projection: EnterpriseProjection,
    exposure_class_losses: Mapping[int, Mapping[str, Decimal]] | None,
) -> None:
    """A supplied bottom-up decomposition must cover every stress year."""
    if exposure_class_losses is None:
        return
    uncovered = tuple(
        str(year.year) for year in projection.stress if year.year not in exposure_class_losses
    )
    if not uncovered:
        return
    raise NotComputable(
        outcome(
            OutcomeState.MISSING_REQUIRED_INPUT,
            metric_id="appendix_ii.table1.impact_of_adverse",
            reason=(
                "The exposure-level bottom-up loss decomposition was supplied but does "
                "not cover every stress year. The uncovered years would silently fall "
                "back to the credit-RWA-share allocation, mixing two methodologies "
                "inside one filed Table 1."
            ),
            items=tuple(f"stress_year:{year}" for year in uncovered),
            context={"uncovered": list(uncovered)},
        )
    )


def _table1(
    projection: EnterpriseProjection,
    car_target_pct: Decimal,
    paid_up_min: Decimal,
    exposure_class_losses: Mapping[int, Mapping[str, Decimal]] | None = None,
    management_actions: ManagementActionsResult | None = None,
) -> Table1Summary:
    current = _snapshot(projection.current, "current")
    pre = tuple(_snapshot(year, f"base_y{year.year}") for year in projection.base)
    post = tuple(_snapshot(year, f"stress_y{year.year}") for year in projection.stress)

    # Phase 4: use the real exposure-level bottom-up decomposition when the
    # service supplies it; otherwise the documented credit-RWA-share allocation
    # (the Phase-2 contract when there is no canonical book). Those are two
    # different methodologies, and a per-year ``.get`` used to mix them inside one
    # filed table whenever the supplied map covered only part of the horizon —
    # silently, with nothing in the table saying which line came from which
    # method (audit 2026-08-22 D-8).
    _require_full_horizon_losses(projection, exposure_class_losses)
    impact: list[tuple[int, tuple[ExposureClassLoss, ...]]] = []
    for base_year, stress_year in zip(projection.base, projection.stress, strict=True):
        if exposure_class_losses is not None:
            impact.append(
                (stress_year.year, _bottom_up_class_losses(exposure_class_losses[stress_year.year]))
            )
        else:
            impact.append((stress_year.year, _exposure_class_losses(base_year, stress_year)))

    required_car: list[tuple[int, Decimal]] = []
    required_paid_up: list[tuple[int, Decimal]] = []
    worst_residual = _ZERO
    for stress_year in projection.stress:
        needed = max(
            stress_year.rwa.total_rwa * car_target_pct / _HUNDRED
            - stress_year.ratios.total_capital,
            _ZERO,
        )
        paid_up_shortfall = max(paid_up_min - stress_year.paid_up, _ZERO)
        required_car.append((stress_year.year, thousands(needed)))
        required_paid_up.append((stress_year.year, thousands(paid_up_shortfall)))
        worst_residual = max(worst_residual, needed, paid_up_shortfall)

    management_block: Table1ManagementActions | None = None
    post_capitalisation: tuple[CapitalSnapshot, ...] | None = None
    residual_after: Table1Residual | None = None
    if management_actions is not None:
        management_block, post_capitalisation, residual_after = _build_management_blocks(
            management_actions, car_target_pct
        )

    return Table1Summary(
        car_target_pct=car_target_pct,
        paid_up_min=thousands(paid_up_min),
        current=current,
        pre_adverse=pre,
        post_adverse=post,
        impact_of_adverse=tuple(impact),
        capital_required_car_target=tuple(required_car),
        capital_required_paid_up=tuple(required_paid_up),
        capital_gap=thousands(worst_residual),
        management_actions=management_block,
        post_capitalisation=post_capitalisation,
        residual_capital_required_after_actions=residual_after,
    )


def _retained_trajectory(years: Sequence[ProjectedYear], opening: Decimal) -> list[Decimal | None]:
    """Opening retained earnings for each projected year (Table 3)."""
    openings: list[Decimal | None] = []
    running = opening
    for year in years:
        openings.append(running)
        running = running + year.pnl.retained
    return openings


def _opening_retained(projection: EnterpriseProjection) -> Decimal:
    """As-of retained earnings from the current-year CET1 build."""
    return _retained_from_ratios(projection.current.ratios)


def _retained_from_ratios(ratios: CapitalRatiosResult) -> Decimal:
    for item in ratios.line_items:
        if item.section != "capital_component":
            continue
        _, _, category = item.line_code.partition(":")
        if category == "retained_earnings":
            return item.weighted_amount
    return _ZERO


def build_appendix_ii(  # noqa: PLR0913 - the builder names its full optional-overlay set
    projection: EnterpriseProjection,
    scenario_paths: Sequence[MacroPathPoint],
    *,
    currency: str,
    car_target_pct: Decimal,
    paid_up_min: Decimal | None = None,
    source: str | None = None,
    exposure_class_losses: Mapping[int, Mapping[str, Decimal]] | None = None,
    pillar2_by_stress_year: Mapping[int, Pillar2Requirement] | None = None,
    management_actions: ManagementActionsResult | None = None,
    basel_applicable: bool = True,
) -> AppendixIITables:
    """Assemble Tables 1–6 from a base+stress enterprise projection.

    ``scenario_paths`` are the authored macro paths (for Table 6).

    ``car_target_pct`` is REQUIRED and carries no default (audit 2026-08-22
    D-15): it is the institution's own governed minimum CAR — the floor the
    Table 1 "capital required" and Table 5 Pillar-1-requirement lines are
    measured against, and the rate at which a management action's RWA relief is
    valued as freed capital. The caller resolves it from the regulatory-parameter
    control plane (``CapitalParams.car_min_pct``), which is effective-dated and
    institution-class aware; a bank modelling an internal target ABOVE that floor
    passes it here. This module never assumes one — see the note where the old
    ``DEFAULT_CAR_TARGET_PCT`` literal used to live.

    ``paid_up_min`` defaults to the floor carried on the projection's minima
    checks.

    Phase 4 overlays (both optional; absent ⇒ Phase-2 behaviour preserved):
    ``exposure_class_losses`` — the real bottom-up ``{stress_year: {crd_class:
    loss_ghs}}`` decomposition for Table 1's "Impact of Adverse"; and
    ``pillar2_by_stress_year`` — the derived Pillar-2 add-ons (credit
    concentration, IRRBB, sovereign, country & FX, reputational) per stress year
    for Table 5. Neither perturbs the Pillar-1 RWA, so the T5 == T1 tie holds.
    """
    if paid_up_min is None:
        # The ¶77 paid-up floor is a governed parameter. Defaulting it to zero on
        # an empty stress leg would report "no paid-up shortfall in any year" from
        # the absence of the projection, not from the bank's capital (audit
        # 2026-08-22 D-8).
        if not projection.stress:
            raise NotComputable(
                outcome(
                    OutcomeState.MISSING_REQUIRED_INPUT,
                    metric_id="appendix_ii.table1.capital_required_paid_up",
                    reason=(
                        "No paid-up minimum was supplied and the projection carries no "
                        "stress years to read it from, so the paid-up shortfall cannot "
                        "be established."
                    ),
                    items=("param:paid_up_min",),
                )
            )
        paid_up_min = projection.stress[0].minima.paid_up_min

    table1 = _table1(
        projection, car_target_pct, paid_up_min, exposure_class_losses, management_actions
    )

    # Table 2 is the Basel CET1/AT1/Tier2 3-tier capital build — banks-only. Under
    # the SDI simplified s.29 regime it is structurally excluded (docs/sdi.md §4.6,
    # Phase H) and rendered empty; Tables 1/3/4/5/6 remain (Table 5 collapses to
    # the credit-RWA base automatically because s.29 zeroes market/operational RWA).
    table2_rows: list[Table2Row] = []
    if basel_applicable:
        table2_rows.append(_table2_row(projection.current, "current"))
        table2_rows += [_table2_row(year, f"base_y{year.year}") for year in projection.base]
        table2_rows += [_table2_row(year, f"stress_y{year.year}") for year in projection.stress]

    opening = _opening_retained(projection)
    base_openings = _retained_trajectory(projection.base, opening)
    stress_openings = _retained_trajectory(projection.stress, opening)
    table3_rows = [
        _table3_row(projection.base[index], f"base_y{year.year}", base_openings[index])
        for index, year in enumerate(projection.base)
    ]
    table3_rows += [
        _table3_row(projection.stress[index], f"stress_y{year.year}", stress_openings[index])
        for index, year in enumerate(projection.stress)
    ]

    table4_rows = [_table4_row(projection.current, "current")]
    table4_rows += [_table4_row(year, f"base_y{year.year}") for year in projection.base]
    table4_rows += [_table4_row(year, f"stress_y{year.year}") for year in projection.stress]

    table5_rows = [_table5_row(projection.current, "current", car_target_pct)]
    table5_rows += [
        _table5_row(year, f"base_y{year.year}", car_target_pct) for year in projection.base
    ]
    table5_rows += [
        _table5_row(
            year,
            f"stress_y{year.year}",
            car_target_pct,
            None
            if pillar2_by_stress_year is None
            else pillar2_by_stress_year.get(year.year),
        )
        for year in projection.stress
    ]

    table6 = build_table6(scenario_paths, source=source)

    tables = AppendixIITables(
        not_computable=(CREDIT_RISK_RESERVE_NOT_COMPUTABLE,),
        currency=currency,
        scenario_code=projection.scenario_code,
        horizon_years=projection.horizon_years,
        table1_summary=table1,
        table2_capital=Table2Capital(rows=tuple(table2_rows)),
        table3_profit_and_loss=Table3ProfitAndLoss(rows=tuple(table3_rows)),
        table4_financial_position=Table4FinancialPosition(rows=tuple(table4_rows)),
        table5_rwa=Table5Rwa(car_target_pct=car_target_pct, rows=tuple(table5_rows)),
        table6_risk_drivers=table6,
    )
    _assert_rwa_tie(tables, projection)
    return tables


def build_table6(
    paths: Sequence[MacroPathPoint], *, source: str | None = None
) -> Table6RiskDrivers:
    """Key risk drivers & forecasting assumptions (base + stress, per year)."""
    rows = tuple(
        Table6DriverRow(
            variable=point.variable,
            year_index=point.year_index,
            base_value=point.base_value,
            stress_value=point.stress_value,
        )
        for point in sorted(paths, key=lambda point: (point.variable, point.year_index))
    )
    return Table6RiskDrivers(rows=rows, source=source)


def _assert_rwa_tie(tables: AppendixIITables, projection: EnterpriseProjection) -> None:
    """The directive invariant: Table 5 stressed Pillar-1 RWA == Table 1 stressed RWA."""
    t5_by_label = {row.label: row.total_pillar1_rwa for row in tables.table5_rwa.rows}
    for snapshot in tables.table1_summary.post_adverse:
        t5_rwa = t5_by_label.get(snapshot.label)
        if t5_rwa is None or t5_rwa != snapshot.total_rwa:
            msg = (
                f"Appendix II tie broken for {snapshot.label}: Table 1 stressed RWA "
                f"{snapshot.total_rwa} != Table 5 Total Pillar-1 RWA {t5_rwa}."
            )
            raise AssertionError(msg)


# --- Serialization -----------------------------------------------------------


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _serialize_snapshot(snapshot: CapitalSnapshot) -> dict[str, object]:
    return {
        "label": snapshot.label,
        "cet1": _s(snapshot.cet1),
        "tier1": _s(snapshot.tier1),
        "tier2": _s(snapshot.tier2),
        "total_regulatory_capital": _s(snapshot.total_regulatory_capital),
        "total_rwa": _s(snapshot.total_rwa),
        "cet1_ratio_pct": _s(snapshot.cet1_ratio_pct),
        "tier1_ratio_pct": _s(snapshot.tier1_ratio_pct),
        "car_pct": _s(snapshot.car_pct),
        "paid_up": _s(snapshot.paid_up),
    }


def _serialize_management_actions(
    block: Table1ManagementActions | None,
) -> dict[str, object] | None:
    if block is None:
        return None
    return {
        "plan_id": block.plan_id,
        "stays_above_all_minima": block.stays_above_all_minima,
        "first_breach_year": block.first_breach_year,
        "binding_minima": list(block.binding_minima),
        "rows": [
            {
                "year": row.year,
                "capital_raised_cet1": _s(row.capital_raised_cet1),
                "capital_raised_at1": _s(row.capital_raised_at1),
                "capital_raised_tier2": _s(row.capital_raised_tier2),
                "capital_raised_total": _s(row.capital_raised_total),
                "revision_of_dividend_policy": _s(row.revision_of_dividend_policy),
                "change_in_business_strategy": _s(row.change_in_business_strategy),
                "sale_of_assets": _s(row.sale_of_assets),
                "risk_reduction": _s(row.risk_reduction),
                "other": _s(row.other),
                "total_management_actions": _s(row.total_management_actions),
                "rwa_relief_total": _s(row.rwa_relief_total),
            }
            for row in block.rows
        ],
    }


def _serialize_residual(residual: Table1Residual | None) -> dict[str, object] | None:
    if residual is None:
        return None
    return {
        "worst": _s(residual.worst),
        "rows": [
            {"year": row.year, "residual_capital_required": _s(row.residual_capital_required)}
            for row in residual.rows
        ],
    }


def _serialize_tables(tables: AppendixIITables) -> dict[str, object]:
    t1 = tables.table1_summary
    return {
        "scenario_code": tables.scenario_code,
        "horizon_years": tables.horizon_years,
        "unit": f"{tables.currency}'000",
        "not_computable": [detail.to_dict() for detail in tables.not_computable],
        "table1_summary": {
            "car_target_pct": _s(t1.car_target_pct),
            "paid_up_min": _s(t1.paid_up_min),
            "current": _serialize_snapshot(t1.current),
            "pre_adverse": [_serialize_snapshot(s) for s in t1.pre_adverse],
            "post_adverse": [_serialize_snapshot(s) for s in t1.post_adverse],
            "impact_of_adverse": [
                {
                    "year": year,
                    "losses": [
                        {"exposure_class": loss.exposure_class, "loss": _s(loss.loss)}
                        for loss in losses
                    ],
                }
                for year, losses in t1.impact_of_adverse
            ],
            "capital_required_car_target": [
                {"year": year, "amount": _s(amount)}
                for year, amount in t1.capital_required_car_target
            ],
            "capital_required_paid_up": [
                {"year": year, "amount": _s(amount)}
                for year, amount in t1.capital_required_paid_up
            ],
            "capital_gap": _s(t1.capital_gap),
            "management_actions": _serialize_management_actions(t1.management_actions),
            "post_capitalisation": (
                None
                if t1.post_capitalisation is None
                else [_serialize_snapshot(s) for s in t1.post_capitalisation]
            ),
            "residual_capital_required_after_actions": _serialize_residual(
                t1.residual_capital_required_after_actions
            ),
        },
        "table2_capital": [
            {
                "label": row.label,
                "total_rwa": _s(row.total_rwa),
                "cet1": {
                    "paid_up": _s(row.cet1.paid_up),
                    "retained_earnings": _s(row.cet1.retained_earnings),
                    "statutory_reserves": _s(row.cet1.statutory_reserves),
                    "other_reserves": _s(row.cet1.other_reserves),
                    "minority_interest": _s(row.cet1.minority_interest),
                    "other_cet1": _s(row.cet1.other_cet1),
                    "gross_cet1": _s(row.cet1.gross_cet1),
                    "deduction_intangibles": _s(row.cet1.deduction_intangibles),
                    "deduction_fi_investments": _s(row.cet1.deduction_fi_investments),
                    "deduction_oci": _s(row.cet1.deduction_oci),
                    "deduction_dta": _s(row.cet1.deduction_dta),
                    "deduction_commercial_entity": _s(row.cet1.deduction_commercial_entity),
                    "deduction_other": _s(row.cet1.deduction_other),
                    "total_deductions": _s(row.cet1.total_deductions),
                    "cet1_after_deductions": _s(row.cet1.cet1_after_deductions),
                },
                "at1_nominal": _s(row.at1_nominal),
                "at1_cap": _s(row.at1_cap),
                "at1_eligible": _s(row.at1_eligible),
                "tier2_nominal": _s(row.tier2_nominal),
                "tier2_cap": _s(row.tier2_cap),
                "tier2_eligible": _s(row.tier2_eligible),
                "total_regulatory_capital": _s(row.total_regulatory_capital),
                "credit_risk_reserve": _s(row.credit_risk_reserve),
            }
            for row in tables.table2_capital.rows
        ],
        "table3_profit_and_loss": [
            {
                "label": row.label,
                "opening_retained_earnings": _s(row.opening_retained_earnings),
                "net_interest_income": _s(row.net_interest_income),
                "fees_and_commissions": _s(row.fees_and_commissions),
                "trading_income": _s(row.trading_income),
                "other_income": _s(row.other_income),
                "operating_expenses": _s(row.operating_expenses),
                "impairment_losses": _s(row.impairment_losses),
                "depreciation_amortisation": _s(row.depreciation_amortisation),
                "profit_before_tax": _s(row.profit_before_tax),
                "tax": _s(row.tax),
                "profit_after_tax": _s(row.profit_after_tax),
                "distributions": _s(row.distributions),
                "adjusted_retained_earnings_for_car": _s(row.adjusted_retained_earnings_for_car),
            }
            for row in tables.table3_profit_and_loss.rows
        ],
        "table4_financial_position": [
            {
                "label": row.label,
                "cash_and_balances": _s(row.cash_and_balances),
                "short_term_investments": _s(row.short_term_investments),
                "loans": _s(row.loans),
                "other_assets": _s(row.other_assets),
                "total_assets": _s(row.total_assets),
                "demand_deposits": _s(row.demand_deposits),
                "savings_deposits": _s(row.savings_deposits),
                "time_deposits": _s(row.time_deposits),
                "other_deposits": _s(row.other_deposits),
                "borrowings": _s(row.borrowings),
                "total_liabilities": _s(row.total_liabilities),
                "capital": _s(row.capital),
            }
            for row in tables.table4_financial_position.rows
        ],
        "table5_rwa": {
            "car_target_pct": _s(tables.table5_rwa.car_target_pct),
            "rows": [
                {
                    "label": row.label,
                    "credit_rwa": _s(row.credit_rwa),
                    "operational_rwa": _s(row.operational_rwa),
                    "market_rwa": _s(row.market_rwa),
                    "total_pillar1_rwa": _s(row.total_pillar1_rwa),
                    "pillar1_requirement": _s(row.pillar1_requirement),
                    "pillar2": {
                        "credit_concentration": _s(row.pillar2.credit_concentration),
                        "irrbb": _s(row.pillar2.irrbb),
                        "sovereign": _s(row.pillar2.sovereign),
                        "country_and_fx": _s(row.pillar2.country_and_fx),
                        "reputational": _s(row.pillar2.reputational),
                        "other": _s(row.pillar2.other),
                        "total": _s(row.pillar2.total),
                    },
                    "total_capital_requirement": _s(row.total_capital_requirement),
                }
                for row in tables.table5_rwa.rows
            ],
        },
        "table6_risk_drivers": {
            "source": tables.table6_risk_drivers.source,
            "rows": [
                {
                    "variable": row.variable,
                    "year_index": row.year_index,
                    "base_value": _s(row.base_value),
                    "stress_value": _s(row.stress_value),
                }
                for row in tables.table6_risk_drivers.rows
            ],
        },
    }
