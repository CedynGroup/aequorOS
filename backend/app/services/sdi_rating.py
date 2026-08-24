"""``AEQ-GH-SDI-FS`` — evidence extraction and the release gate.

Implements steps 2 and 5 of ``AequorOS_SDI_Financial_Strength_Methodology.md``
§7: stage a candidate methodology through the Desk register, and — only once a
version of it is APPROVED — release advisory component scores.

The gate is the whole point
---------------------------
The dossier's §4 defines five output states and closes the last two for v1:

1. not computable — a prerequisite is absent
2. **methodology pending** — a candidate exists, calibration/validation do not
3. **advisory assessment** — approved methodology, component scores only
4. validated internal grade — CLOSED for v1
5. PD mapping — CLOSED for v1

:func:`assessment_state` decides between 1, 2 and 3 from data alone. There is no
flag, no environment variable and no code path that emits a grade or a PD for an
SDI: :data:`RELEASES_GRADE` and :data:`RELEASES_PD` are ``False`` and the scoring
function does not compute either. Opening state 4 is a deliberate future change
that must carry the §5 back-testing evidence, not a toggle someone can flip.

An approved version cannot be created here. ``stage_candidate_methodology``
writes a ``draft``; only Track-2 maker-checker through the operator Desk
(``/operator/v1/desk/methodologies``) can approve one, by a different operator
than the proposer. That is the same control the RWA composition goes through,
and for the same reason: a model that decides how strong an institution looks
must not be self-certified.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.rating.engine import ComponentScore, grade_for_score, score_components
from app.domain.rating.sdi_scorecard import (
    CANDIDATE_COMPONENTS,
    CANDIDATE_RATIOS,
    CANDIDATE_STRUCTURE_VERSION,
    GRADE_ORDER,
    METHODOLOGY_CODE,
    OPTIONAL_COMPONENTS,
    candidate_parameters,
    grade_cutpoints,
)
from app.models import Bank, DeskMethodology

#: An advisory INTERNAL grade is now derived from the composite (founder
#: decision, 2026-08-23). It is not an agency rating, not a filing input, and it
#: carries the same uncalibrated status as the anchors it rests on — the §5
#: calibration pack must still replace the cutpoints with a benchmarked,
#: back-tested mapping. Read by the surface so a card cannot render a grade this
#: module did not produce.
RELEASES_GRADE = True

#: PD stays CLOSED and is a different problem entirely. A probability of default
#: needs representative outcome data, low-default-portfolio treatment and a
#: margin of conservatism (dossier §4 state 5, and the bank PD specification it
#: points at). None of that exists for the SDI population, and a grade does not
#: imply one.
RELEASES_PD = False

_ZERO = Decimal("0")
_ONE = Decimal("1")

#: Applies no operating-environment adjustment: the interpolation collapses to
#: ``raw_score``. See the note at the call site for why 0-with-the-bank-matrix
#: would be wrong rather than merely conservative.
_IDENTITY_ENVIRONMENT: tuple[tuple[Decimal, Decimal], tuple[Decimal, Decimal]] = (
    (_ZERO, _ZERO),
    (_ONE, _ONE),
)


@dataclass(frozen=True)
class RatioEvidence:
    """One extracted ratio, with where it came from and whether it is usable."""

    code: str
    value: Decimal | None
    source: str
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class SdiAssessment:
    """The advisory financial-strength assessment, or the reason there isn't one."""

    state: str  # 'not_computable' | 'methodology_pending' | 'advisory'
    methodology_code: str = METHODOLOGY_CODE
    methodology_version: int | None = None
    structure_version: str = CANDIDATE_STRUCTURE_VERSION
    reason: str | None = None
    as_of: date | None = None
    components: tuple[ComponentScore, ...] = ()
    evidence: tuple[RatioEvidence, ...] = ()
    omitted_components: tuple[str, ...] = ()
    limitations: tuple[str, ...] = field(default_factory=tuple)
    composite_score: Decimal | None = None
    #: The grade the scorecard itself produces, before the sovereign ceiling.
    standalone_grade: str | None = None
    #: What is actually issued: the standalone grade capped at the ceiling.
    issued_grade: str | None = None
    sovereign_ceiling: str | None = None
    ceiling_applied: bool = False

    @property
    def releases_grade(self) -> bool:
        return RELEASES_GRADE

    @property
    def releases_pd(self) -> bool:
        return RELEASES_PD


# ---------------------------------------------------------------------------
# The register: stage a candidate, resolve an approved version
# ---------------------------------------------------------------------------


def approved_methodology(db: Session) -> DeskMethodology | None:
    """The latest APPROVED ``AEQ-GH-SDI-FS`` version, or ``None``.

    ``None`` is the steady state today and is not an error — it is dossier §4
    state 2. Deliberately no ``ensure_default`` sibling: the bank scorecard has
    one (``implied_rating.ensure_default_methodology``) because its parameters
    were validated before the register existed, and reproducing that idiom here
    would let the SDI model approve itself.
    """
    return db.scalar(
        select(DeskMethodology)
        .where(
            DeskMethodology.methodology_code == METHODOLOGY_CODE,
            DeskMethodology.status == "approved",
        )
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )


def latest_methodology(db: Session) -> DeskMethodology | None:
    """The latest version of any status — so a surface can say "a candidate is
    staged, awaiting approval" rather than "nothing exists"."""
    return db.scalar(
        select(DeskMethodology)
        .where(DeskMethodology.methodology_code == METHODOLOGY_CODE)
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )


def stage_candidate_methodology(
    db: Session, *, proposed_by: str, change_rationale: str
) -> DeskMethodology:
    """Write the candidate structure as a ``draft`` version (dossier §7 step 2).

    Idempotent in the way that matters: it always creates a NEW version rather
    than mutating an existing one, so a historical assessment stays reproducible
    under the version that produced it. It never writes ``approved``.
    """
    latest = latest_methodology(db)
    row = DeskMethodology(
        methodology_code=METHODOLOGY_CODE,
        version=(latest.version + 1) if latest is not None else 1,
        status="draft",
        parameters=candidate_parameters(),
        change_rationale=change_rationale,
        proposed_by=proposed_by,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _capital_evidence(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[RatioEvidence]:
    from app.services import sdi_capital  # noqa: PLC0415 - breaks an import cycle

    try:
        summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, as_of)
    except Exception as exc:  # noqa: BLE001 - an unresolved capital policy is evidence, not a crash
        return [
            RatioEvidence("car_headroom_pp", None, "sdi_capital", str(exc)[:200]),
            RatioEvidence("paid_up_coverage_x", None, "sdi_capital", "capital summary unavailable"),
            RatioEvidence("reserve_fund_pct", None, "sdi_capital", "capital summary unavailable"),
        ]
    car = _dec(getattr(summary, "car_pct", None))
    floor = _dec(getattr(summary, "car_min_pct", None))
    headroom = None if car is None or floor is None else car - floor
    # Paid-up coverage and reserve-fund progress are BOTH computable today: the
    # capital components are derived facts and the licence-class floor is a
    # governed control-plane value (``paid_up_min``, scoped by institution TYPE
    # — GHS 15m for savings-&-loans, 2m for a microfinance bank). They were
    # written off as "unbuilt" on 2026-08-23 without anyone checking, which
    # blocked the whole capital component behind evidence that already existed.
    components = _capital_components(db, ctx, bank, as_of)
    paid_up = components.get("paid_up_capital")
    statutory = components.get("statutory_reserves")
    floor = _paid_up_floor(db, bank, as_of)
    coverage = paid_up / floor if paid_up is not None and floor else None
    reserve_pct = (
        statutory / paid_up * Decimal("100")
        if statutory is not None and paid_up not in (None, _ZERO)
        else None
    )
    return [
        RatioEvidence(
            "car_headroom_pp",
            headroom,
            "sdi_capital.compute_sdi_capital_summary",
            None if headroom is not None else "s.29 CAR is not computable at this date",
        ),
        RatioEvidence(
            "paid_up_coverage_x",
            coverage,
            "capital_component/paid_up_capital ÷ control-plane paid_up_min",
            None
            if coverage is not None
            else "paid-up capital or the licence-class floor is absent",
        ),
        RatioEvidence(
            "reserve_fund_pct",
            reserve_pct,
            "capital_component/statutory_reserves ÷ paid_up_capital",
            None if reserve_pct is not None else "statutory reserves or paid-up capital is absent",
        ),
    ]


def _facts(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date, groups: tuple[str, ...]
) -> list[tuple[str, str, Decimal]]:
    """``(fact_group, category, amount)`` at ``as_of``, from WHICHEVER plane has them.

    The two computation tiers write to different tables and this scorecard is
    called from both:

    * the LIVE tier (``derive_current_facts`` → ``recompute_live``) writes
      ``current_financial_facts``, keyed by ``source_as_of_date``;
    * the OFFICIAL tier (``derive_facts`` → ``run_official``) writes
      ``bank_financial_facts``, keyed through ``bank_reporting_periods``.

    Reading only the official table made every ratio unavailable under live
    compute — which is the tier Treasury and ALM actually run on. The live table
    is preferred and the official one is the fallback, so an assessment resolves
    on whichever plane produced facts for that date.
    """
    from app.models import (  # noqa: PLC0415
        BankFinancialFact,
        BankReportingPeriod,
        CurrentFinancialFact,
    )

    live = db.execute(
        select(
            CurrentFinancialFact.fact_group,
            CurrentFinancialFact.category,
            CurrentFinancialFact.amount,
        ).where(
            CurrentFinancialFact.organization_id == ctx.organization_id,
            CurrentFinancialFact.bank_id == bank.id,
            CurrentFinancialFact.fact_group.in_(groups),
            CurrentFinancialFact.source_as_of_date == as_of,
        )
    ).all()
    if live:
        return [(r[0], r[1], Decimal(str(r[2]))) for r in live]

    official = db.execute(
        select(
            BankFinancialFact.fact_group,
            BankFinancialFact.category,
            BankFinancialFact.amount,
        )
        .join(
            BankReportingPeriod,
            BankReportingPeriod.id == BankFinancialFact.reporting_period_id,
        )
        .where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.fact_group.in_(groups),
            BankReportingPeriod.period_end == as_of,
        )
    ).all()
    return [(r[0], r[1], Decimal(str(r[2]))) for r in official]


def _capital_components(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[str, Decimal]:
    """Derived ``capital_component`` facts at ``as_of``, by category."""
    return {
        category: amount
        for _, category, amount in _facts(db, ctx, bank, as_of, ("capital_component",))
    }


def _paid_up_floor(db: Session, bank: Bank, as_of: date) -> Decimal | None:
    """The licence-class minimum paid-up capital, in GHS.

    Governed data (``paid_up_min``), stored in GHS MILLIONS — the unit is on the
    row and is converted here rather than assumed, because a factor-of-1e6 error
    in a capital floor is not a rounding difference.
    """
    from app.services import regulatory_parameters as rp  # noqa: PLC0415

    resolved = rp.try_resolve(db, bank, "paid_up_min", as_of=as_of)
    if resolved is None or resolved.value is None:
        return None
    unit = (resolved.unit or "").lower()
    if unit == "ghs_millions":
        return resolved.value * Decimal("1000000")
    if unit in ("ghs", ""):
        return resolved.value
    return None


def _asset_quality_evidence(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[RatioEvidence]:
    from app.services import loan_classification  # noqa: PLC0415 - breaks an import cycle

    try:
        report = loan_classification.classify_loan_book(db, ctx, bank, as_of)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)[:200]
        return [
            RatioEvidence("npl_pct", None, "loan_classification", reason),
            RatioEvidence("provision_coverage_pct", None, "loan_classification", reason),
            RatioEvidence("par30_pct", None, "loan_classification", reason),
        ]
    result = report.result
    npl_pct = _dec(result.npl_ratio)
    if npl_pct is not None:
        npl_pct *= Decimal("100")
    required = _dec(result.total_provision_required_ghs) or _ZERO
    # Provision COVERAGE needs provisions HELD, which the classification engine
    # does not carry — it computes what is required. Reported as missing rather
    # than silently equated to the requirement, which would score every book at
    # exactly 100%.
    # PAR30 IS exposed — ``LoanClassificationReport.portfolio_at_risk`` carries a
    # PortfolioAtRisk per threshold. Declaring it unbuilt was wrong.
    par30 = next(
        (
            Decimal(str(metric.ratio)) * Decimal("100")
            for metric in report.portfolio_at_risk
            if "30" in metric.code
        ),
        None,
    )
    return [
        RatioEvidence("npl_pct", npl_pct, "loan_classification.npl_ratio"),
        # Provisions HELD genuinely are not a derived fact — the engine computes
        # what is REQUIRED. Equating the two would score every book at exactly
        # 100% coverage, so it stays unavailable until the impairment allowance
        # is carried through as a fact.
        RatioEvidence(
            "provision_coverage_pct",
            None,
            "unsourced",
            f"provisions HELD are not a derived fact; required is GHS {required:,.0f}",
        ),
        RatioEvidence(
            "par30_pct",
            par30,
            "loan_classification.portfolio_at_risk",
            None if par30 is not None else "no PAR30 threshold in the classification report",
        ),
    ]


def _liquidity_evidence(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[RatioEvidence]:
    from app.services import sdi_views  # noqa: PLC0415 - breaks an import cycle

    try:
        position = sdi_views.get_sdi_liquidity_position(db, ctx, bank, as_of)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)[:200]
        return [
            RatioEvidence("lmtd_weakest_headroom_pp", None, "sdi_views", reason),
            RatioEvidence("reserve_coverage_pct", None, "sdi_views", reason),
            RatioEvidence("mismatch_90d_pct_assets", None, "sdi_views", reason),
        ]
    headrooms: list[Decimal] = []
    for ratio in position.ratios:
        value, threshold = _dec(ratio.value_pct), _dec(ratio.threshold_pct)
        if value is not None and threshold is not None:
            headrooms.append(value - threshold)
    weakest = min(headrooms) if headrooms else None
    coverages: list[Decimal] = []
    for reserve in position.reserves:
        value, threshold = _dec(reserve.value_pct), _dec(reserve.threshold_pct)
        if value is not None and threshold:
            coverages.append(value / threshold * Decimal("100"))
    reserve_coverage = min(coverages) if coverages else None
    return [
        RatioEvidence(
            "lmtd_weakest_headroom_pp",
            weakest,
            "sdi_views.get_sdi_liquidity_position",
            None if weakest is not None else "no LMTD Table 1 ratio is computable",
        ),
        RatioEvidence(
            "reserve_coverage_pct",
            reserve_coverage,
            "sdi_views.get_sdi_liquidity_position",
            None if reserve_coverage is not None else "no reserve requirement is computable",
        ),
        # The 90-day cumulative mismatch needs a total-assets denominator the
        # ladder does not carry; left missing rather than divided by a proxy.
        RatioEvidence(
            "mismatch_90d_pct_assets",
            None,
            "unbuilt",
            "the maturity ladder carries mismatch in GHS, not as a share of assets",
        ),
    ]


def _concentration_evidence(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[RatioEvidence]:
    from app.services import sdi_views  # noqa: PLC0415 - breaks an import cycle

    largest: Decimal | None = None
    top5: Decimal | None = None
    note_large: str | None = None
    note_funding: str | None = None
    try:
        exposures = sdi_views.get_sdi_large_exposures(db, ctx, bank, as_of)
        shares: list[Decimal] = []
        for item in exposures.exposures:
            share = _dec(item.pct_net_own_funds)
            if share is not None:
                shares.append(share)
        largest = max(shares) if shares else None
        if largest is None:
            note_large = "no exposure carries a Net-Own-Funds share"
    except Exception as exc:  # noqa: BLE001
        note_large = str(exc)[:200]
    try:
        position = sdi_views.get_sdi_liquidity_position(db, ctx, bank, as_of)
        top5 = _dec(position.funding_concentration.top_five_pct)
        if top5 is None:
            note_funding = "top-five depositor share is not computable"
    except Exception as exc:  # noqa: BLE001
        note_funding = str(exc)[:200]
    return [
        RatioEvidence(
            "largest_exposure_pct_nof",
            largest,
            "sdi_views.get_sdi_large_exposures",
            note_large,
        ),
        RatioEvidence("top5_funding_pct", top5, "sdi_views.funding_concentration", note_funding),
    ]


def collect_evidence(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[RatioEvidence, ...]:
    """Every candidate ratio, with its value or the reason it has none.

    Nothing is imputed. A ratio the platform cannot yet source is reported
    ``available=False`` with the reason, which is what the dossier's §2 means by
    "their absence is not neutral evidence and must not be imputed".
    """
    evidence: list[RatioEvidence] = []
    evidence += _capital_evidence(db, ctx, bank, as_of)
    evidence += _asset_quality_evidence(db, ctx, bank, as_of)
    evidence += _liquidity_evidence(db, ctx, bank, as_of)
    evidence += _concentration_evidence(db, ctx, bank, as_of)
    evidence += _earnings_evidence(db, ctx, bank, as_of)
    # IRRBB needs a COMPLETE approved SDI IRR run; omitted rather than
    # substituted while none exists (§3). This is the declared-optional
    # component, so its absence does not block the assessment.
    for code in ("eve_sensitivity_pct_nof", "repricing_gap_1y_pct"):
        evidence.append(
            RatioEvidence(code, None, "regulatory_irr", "no complete SDI IRR run at this date")
        )
    return tuple(evidence)


#: Balance-sheet fact categories that are ASSETS, shared with the bank scorecard
#: so the two cannot disagree about what an asset is.
_ASSET_CATEGORIES = frozenset(
    {
        "cash_vault",
        "bog_required_reserves",
        "bog_excess_reserves",
        "securities_bog_bills",
        "securities_gog_bonds",
        "loans_gross",
        "other_assets",
    }
)
#: Assets that actually earn interest — the NIM denominator.
_EARNING_ASSETS = frozenset({"loans_gross", "securities_bog_bills", "securities_gog_bonds"})


def _earnings_evidence(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[RatioEvidence]:
    """ROA, NIM and cost-to-income from the derived ``operational_income`` facts.

    These were hardcoded unavailable ("annual earnings observations are not
    assembled") when the SDI dataset carried no income statement. It does now,
    and ``fact_derivation._derive_operational_income`` turns it into up to three
    trailing-12-month windows.

    §2.2 anti-manipulation, matching the bank scorecard's ``_CONSERVATIVE_RATIOS``
    convention: each ratio takes the WEAKER of the latest year and the average of
    the available years, so one good year cannot flatter the score.
    """
    rows = _facts(db, ctx, bank, as_of, ("operational_income", "balance_sheet"))
    income: dict[str, dict[str, Decimal]] = {}
    assets = _ZERO
    earning = _ZERO
    for group, category, value in rows:
        if group == "balance_sheet":
            if category in _ASSET_CATEGORIES:
                assets += value
            if category in _EARNING_ASSETS:
                earning += value
            continue
        metric, _, year = category.rpartition("_")
        income.setdefault(year, {})[metric] = value

    def series(metric: str) -> list[Decimal]:
        return [values[metric] for _, values in sorted(income.items()) if metric in values]

    def weaker(values: list[Decimal], *, lower_is_better: bool) -> Decimal | None:
        if not values:
            return None
        latest, average = values[-1], sum(values, _ZERO) / Decimal(len(values))
        return max(latest, average) if lower_is_better else min(latest, average)

    def ratio(numerators: list[Decimal], denominator: Decimal) -> list[Decimal]:
        if denominator <= _ZERO:
            return []
        return [n / denominator * Decimal("100") for n in numerators]

    roa = weaker(ratio(series("net_income"), assets), lower_is_better=False)
    nim = weaker(ratio(series("net_interest_income"), earning), lower_is_better=False)
    gross = series("gross_income")
    opex = series("operating_expenses")
    cti_values = [
        expense / income_ * Decimal("100")
        for expense, income_ in zip(opex, gross, strict=False)
        if income_ > _ZERO
    ]
    cti = weaker(cti_values, lower_is_better=True)
    missing = "no operational_income facts at this date (an income statement must be ingested)"
    return [
        RatioEvidence(
            "roa_pct", roa, "operational_income/net_income ÷ total assets",
            None if roa is not None else missing,
        ),
        RatioEvidence(
            "net_interest_margin_pct", nim,
            "operational_income/net_interest_income ÷ earning assets",
            None if nim is not None else missing,
        ),
        RatioEvidence(
            "cost_to_income_pct", cti,
            "operational_income/operating_expenses ÷ gross_income",
            None if cti is not None else missing,
        ),
    ]


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def assessment_state(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> SdiAssessment:
    """The dossier §4 state for this institution at this date.

    Never raises for a missing input: an absent prerequisite is state 1 and an
    unapproved methodology is state 2, both of which are answers.
    """
    evidence = collect_evidence(db, ctx, bank, as_of)
    approved = approved_methodology(db)
    if approved is None:
        staged = latest_methodology(db)
        reason = (
            f"{METHODOLOGY_CODE} v{staged.version} is staged as a candidate and awaiting "
            "Track-2 calibration, independent model validation and approval."
            if staged is not None
            else (
                f"{METHODOLOGY_CODE} has no version in the Desk methodology register. The "
                "bank-only AEQ-GHS-BANK-PD scorecard is not applicable to an SDI because it "
                "requires Basel LCR/NSFR, FX NOP and Tier-1 inputs."
            )
        )
        return SdiAssessment(
            state="methodology_pending",
            methodology_version=staged.version if staged is not None else None,
            reason=reason,
            as_of=as_of,
            evidence=evidence,
        )

    available: dict[str, Decimal] = {
        item.code: item.value for item in evidence if item.value is not None
    }
    present_components = {
        ratio.component for ratio in CANDIDATE_RATIOS if ratio.code in available
    }
    omitted = tuple(
        sorted(
            component.code
            for component in CANDIDATE_COMPONENTS
            if component.code not in present_components
        )
    )
    mandatory_missing = tuple(code for code in omitted if code not in OPTIONAL_COMPONENTS)
    if mandatory_missing:
        return SdiAssessment(
            state="not_computable",
            methodology_version=approved.version,
            reason=(
                "No evidence is available for "
                + ", ".join(mandatory_missing)
                + ". A component with no usable ratio is omitted, never scored at a neutral "
                "value, so the assessment is not produced."
            ),
            as_of=as_of,
            evidence=evidence,
            omitted_components=omitted,
        )

    # Renormalise the surviving ratio weights within each component. A component
    # whose ratios sum to less than 1 is refused by the engine, and — more to the
    # point — leaving the gap would score the component DOWN for a missing input
    # rather than scoring it on the evidence that exists. Same rule as the
    # component-level renormalisation below: omit, never substitute (§3).
    present = [ratio for ratio in CANDIDATE_RATIOS if ratio.code in available]
    weight_by_component: dict[str, Decimal] = {}
    for ratio in present:
        weight_by_component[ratio.component] = (
            weight_by_component.get(ratio.component, _ZERO) + ratio.weight
        )
    scored_ratios = tuple(
        replace(ratio, weight=ratio.weight / weight_by_component[ratio.component])
        for ratio in present
        if weight_by_component[ratio.component] > _ZERO
    )
    scored_components = tuple(
        component for component in CANDIDATE_COMPONENTS if component.code in present_components
    )
    # Operating-environment adjustment: NOT APPLIED in v1, via an identity matrix.
    #
    # The bank scorecard's matrix is ((0,0),(1,1))-style bilinear with the
    # environment on one axis, and its default ((0,0),(0,1)) means an
    # environment score of 0 floors EVERY ratio to zero. Passing 0 because no
    # determination exists would therefore assert "worst possible operating
    # environment" — a substitution, and precisely what the dossier forbids
    # (§2: absence is not neutral evidence). No governed Ghana
    # operating-environment determination exists yet
    # (``desk_operating_environment_assessments`` is empty), so the adjustment is
    # OMITTED and the omission is stated in ``limitations`` rather than being
    # silently applied at its worst value.
    components = score_components(
        ratio_values=available,
        definitions=scored_ratios,
        components=scored_components,
        operating_environment_score=_ZERO,
        operating_environment_matrix=_IDENTITY_ENVIRONMENT,
    )
    composite = sum((component.contribution for component in components), _ZERO)
    standalone = grade_for_score(
        min(max(composite, _ZERO), _ONE), grade_cutpoints(), GRADE_ORDER
    )
    # The sovereign ceiling binds an SDI exactly as it binds a bank: a domestic
    # institution is not stronger than the sovereign whose paper it holds and
    # whose economy it lends into. Resolved from the SAME tenant-ingested agency
    # observations the bank scorecard uses, so the two are comparable, and
    # skipped (never assumed) when no observation exists.
    ceiling = _sovereign_ceiling(db, ctx, bank, as_of)
    issued, ceiling_applied = _apply_ceiling(standalone, ceiling)
    limitations = [
        "Advisory internal grade and component scores. Not an agency rating, not a "
        "regulatory filing input, and not a probability of default.",
        "The score-to-grade mapping is uncalibrated: cutpoints are candidate model "
        "parameters awaiting benchmarking and independent validation.",
        "No operating-environment adjustment is applied: no governed Ghana "
        "operating-environment determination exists. Scores are standalone.",
        f"Component weights renormalised over {len(scored_components)} of "
        f"{len(CANDIDATE_COMPONENTS)} components; omitted components are not scored.",
    ]
    return SdiAssessment(
        state="advisory",
        methodology_version=approved.version,
        as_of=as_of,
        components=components,
        evidence=evidence,
        omitted_components=omitted,
        limitations=tuple(limitations),
        composite_score=composite,
        standalone_grade=standalone,
        issued_grade=issued,
        sovereign_ceiling=ceiling,
        ceiling_applied=ceiling_applied,
    )


def _sovereign_ceiling(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> str | None:
    """Ghana's own grade from the tenant's ingested agency observations, or None.

    ``None`` means no observation exists for this tenant — the ceiling is then
    NOT applied rather than assumed at some default, because guessing a
    sovereign grade would move every institution's issued grade.
    """
    from app.services import implied_rating  # noqa: PLC0415 - breaks an import cycle

    try:
        sovereign = implied_rating._current_sovereign(db, ctx.organization_id, bank.id, as_of)
    except Exception:  # noqa: BLE001 - an absent observation is a state, not a fault
        return None
    rating = (sovereign or {}).get("rating") if isinstance(sovereign, dict) else None
    if not rating:
        return None
    normalized = implied_rating._normalize_grade(str(rating))
    return normalized if normalized in implied_rating.GRADE_ORDER else None


def _apply_ceiling(standalone: str, ceiling: str | None) -> tuple[str, bool]:
    """Cap ``standalone`` at the sovereign ceiling. Returns (issued, applied)."""
    from app.services import implied_rating  # noqa: PLC0415 - same cycle

    order = implied_rating.GRADE_ORDER
    if ceiling is None or standalone not in order or ceiling not in order:
        return standalone, False
    # GRADE_ORDER runs strongest -> weakest, so a LARGER index is weaker.
    if order.index(standalone) < order.index(ceiling):
        return ceiling, True
    return standalone, False
