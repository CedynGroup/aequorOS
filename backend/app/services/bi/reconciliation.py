"""Reconciliation of the BI marts to the figures the platform already files (R1–R12).

Every check compares something the mart builder WROTE against something the
platform COMPUTED elsewhere, and grades it ``green`` / ``amber`` / ``red`` /
``grey``. ``grey`` means "could not be assessed" — no engine figure at this
date, no live plane, no ledger — and is never read as a pass. The twelve checks
(``.ai/BI_ARCHITECTURE.md`` §Reconciliation → trust; R10 is D-042):

=====  ===================================================  ===============================
check  left-hand side (the mart)                            right-hand side (the platform)
=====  ===================================================  ===============================
R1     portfolio NPL % under the CLASSIFICATION FX rule     live credit ``npl_ratio_pct``
       (``classification_exposure_rc``, D-015)              (engine mart, tier ``live``)
R2     Σ ``balance_rc`` of LOAN rows (DERIVATION rule)      live fact ``loans_gross``
R3     Σ ``balance_rc`` of DEPOSIT rows                     the five live deposit lines
R4     Σ ``pl_sign × ytd_rc`` per BSD7A line and slice      BSD7A's own ``bsd7.pl_line``
                                                            resolver, period-to-date
R5     mart row count for the date                          included current snapshots
R6     rows with no reporting-currency balance              (count only; no platform total)
R7     share of |balance| on unmapped / absent branches     (coverage; no platform twin)
R8     the build's as-of                                    the live plane's date
R9     the balance-sheet identity control's own record      ``current_reconciliation_record``
R10    worse of the LOAN rows / exposure with no             (completeness; no platform twin)
       days-past-due band
R11    Σ ``ytd_rc`` per (account, month, currency) over      ``bi_fact_gl_monthly.ytd_rc``
       ``bi_fact_gl_branch_monthly``'s branches
R12    worse of the LOAN rows / exposure with no stated      (completeness; no platform twin)
       ``arrears_amount_rc``
=====  ===================================================  ===============================

Tolerances (:data:`TOLERANCES`) are stated once, per check, in the unit of
the comparison, and each has a reason:

* **R1** ``1e-6`` percentage points — both sides are exact Decimal arithmetic
  over the same exposures with the engine's own rounding of the fraction
  applied (:data:`ENGINE_RATIO_QUANTUM`); the allowance is for the ``str(...)``
  round trip through the live payload.
* **R2** ``0.0001`` — the fact plane quantises to four decimals
  (``fact_derivation.MONEY``); one rounding on one total.
* **R3** ``0.0005`` — the five deposit lines are each quantised to four
  decimals, so their sum may differ from the exact total by up to five
  half-units.
* **R4** exact — the mart is computed by the SAME ``pl_mapping`` functions the
  return resolver delegates to (D-021); any difference is a defect.
* **R5** exact — a row count.
* **R6 / R7** thresholds, not tolerances: ``green`` at zero, ``amber`` above
  it. Both are coverage statements, not disagreements.
* **R8** exact (a date); ``amber`` when the mart is behind the live plane.
* **R9** the control's own verdict: ``within_tolerance`` → green,
  ``exception_applied`` → amber, ``blocked`` → red; ``None`` with live facts
  present is a book that balanced exactly (green), ``None`` without live facts
  is unassessed (grey).
* **R10** a completeness threshold (D-042) over the WORSE of two shares —
  missing rows and missing exposure (D-049): green at 0, amber above it, red at
  100 % of either. Red is what a book with no arrears data at all scores, which
  is the case that rendered ``loans.par_90_pct`` as ``0.00 %``; it is also what
  a small count of loans carrying the whole book's exposure scores, because the
  guarded ratio is exposure-weighted. Grey when the day has no LOAN rows
  (nothing to be complete about).
* **R11** exact — both sides are the SAME Decimal ledger figures, and the branch
  rows are written with a residual that makes the identity hold by construction
  (``domain/bi/extract.gl_branch_monthly_rows``), so any difference at all is a
  defect in the writing, not a rounding. Amber carries the second, softer
  statement: the identity holds but part of the ledger sits on the unallocated
  remainder, so the breakdown is incomplete even though its total is right.
  ``green`` with ``accounts_compared: 0`` is a month with NO branch rows — the
  mart makes no branch claim, so there is no figure whose trust went unassessed;
  the absence is reported where absences belong, as the dataset the surface needs.
* **R12** a completeness threshold, R10's twin over ``arrears_amount_rc`` — the
  figure the bank STATES rather than the band the platform derives. Same
  WORSE-of-two shares and same gradings, on a DIFFERENT population: only loans
  with a positive classified exposure, because ``arrears_amount_rc`` is NULL for
  an unconverted foreign-currency loan whatever the bank stated, and a gap the
  bank cannot close by stating arrears is R6's message rather than this one.
  Grey when nothing is left to assess.

Storage vs evaluation
---------------------
:data:`CHECK_IDS` is what this module EVALUATES and :data:`STORABLE_CHECK_IDS`
what ``bi_reconciliation_results`` can hold; both are the model's own vocabulary
(``app/models/bi.RECONCILIATION_CHECK_IDS``, mirrored by migration
``202609220066``'s CHECK literal, widened for R11 by ``202609280075`` and for R12
by ``202609280076``), so the live badge (:func:`trust_of`) and the
stored one (:func:`trust_for`) carry the same checks. They stay separate
names because a check can be written here before that vocabulary admits it — R10
was, for one wave — and :func:`persist` must then skip it with a log instead of
failing the whole build on the CHECK, while :func:`trust_for` must not report a
permanent grey for a row the database cannot hold.

Trust (:func:`overall_trust`): ``red`` if any check is red; else ``amber`` if
any is amber; else ``grey`` if any is grey or missing; else ``green``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.domain.ingestion.reference_schemas import gl_segment_balances
from app.models import (
    Bank,
    BankReportingPeriod,
    BiDimBranch,
    BiFactEngineMetric,
    BiFactGlBranchMonthly,
    BiFactGlMonthly,
    BiFactPositionDaily,
    BiReconciliationResult,
    CanonicalPositionSnapshot,
    CurrentFinancialFact,
)
from app.models.bi import RECONCILIATION_CHECK_IDS
from app.models.canonical import is_current_generation
from app.services import fact_derivation, jurisdictions

logger = logging.getLogger(__name__)

GREEN, AMBER, RED, GREY = "green", "amber", "red", "grey"
OVERALL = "overall"

#: The completeness check D-042 added beside R1–R9, which is what the model
#: carried at the time. It now runs R1–R12 (R11 GL branch identity, R12 arrears
#: completeness).
DPD_COMPLETENESS = "R10"

#: The general-ledger-by-branch identity (P5-B). Its own id rather than a second
#: comparison folded into R4: a branch split that does not sum is a different
#: fault from a mart that disagrees with the filed return, and one check per
#: comparison is what makes either diagnosable.
GL_BRANCH_IDENTITY = "R11"

#: The arrears completeness share (P5-A) — R10's argument on the field the bank
#: STATES rather than the band the platform derives. A bank that supplies
#: ``arrears_amount`` for half its book would show a sum reading as the whole
#: book's arrears and a share silently understated, with no visible defect.
ARREARS_COMPLETENESS = "R12"

#: Every check this module evaluates, in order.
CHECK_IDS: tuple[str, ...] = RECONCILIATION_CHECK_IDS

#: Every check ``bi_reconciliation_results`` can store — the model's own
#: vocabulary, mirrored by migration ``202609220066``'s CHECK. Identical to
#: :data:`CHECK_IDS` now that R10 has landed there; kept as its own name because
#: :func:`persist` and :func:`trust_for` answer a different question from
#: :func:`trust_of`, and a check written ahead of its vocabulary must degrade
#: (evaluated, badged, logged) rather than fail the build on the CHECK.
STORABLE_CHECK_IDS: tuple[str, ...] = RECONCILIATION_CHECK_IDS

#: Per-check tolerance in the unit of the comparison (see the module docstring).
#: ``None`` = a threshold check (green at zero, amber above) rather than a tolerance.
TOLERANCES: dict[str, Decimal | None] = {
    "R1": Decimal("0.000001"),
    "R2": Decimal("0.0001"),
    "R3": Decimal("0.0005"),
    "R4": Decimal("0"),
    "R5": Decimal("0"),
    "R6": None,
    "R7": None,
    "R8": Decimal("0"),
    "R9": None,
    DPD_COMPLETENESS: None,
    GL_BRANCH_IDENTITY: Decimal("0"),
    ARREARS_COMPLETENESS: None,
}

#: The balance-sheet lines R2 / R3 read (``fact_derivation._derive_balance_sheet_block``).
LOANS_LINE = "loans_gross"
DEPOSIT_LINES: tuple[str, ...] = (
    "retail_deposits_stable",
    "retail_deposits_less_stable",
    "wholesale_operational",
    "wholesale_non_op_sme",
    "wholesale_non_op_corporate",
)
BALANCE_SHEET_GROUP = "balance_sheet"
#: The canonical position types R1 / R2 / R3 / R10 select on.
LOAN_TYPE = "LOAN"
DEPOSIT_TYPE = "DEPOSIT"
#: The live credit metric R1 reconciles to.
NPL_METRIC_ID = "npl_ratio_pct"
CREDIT_MODULE = "credit"
#: The engine quantises the NPL FRACTION to six decimals before the live view
#: scales it to percent (``domain.capital.loan_classification._RATIO_Q``);
#: R1 applies the same rounding so it compares like with like. Pinned by test.
ENGINE_RATIO_QUANTUM = Decimal("0.000001")

#: BSD7A's P&L columns R4 compares: the period-to-date domestic / foreign slices.
_R4_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ptd_domestic", "domestic"),
    ("ptd_foreign", "foreign"),
)
_R4_FORM = "BSD7A"
_R4_SOURCE = "bsd7.pl_line"

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: str
    lhs: Decimal | None = None
    rhs: Decimal | None = None
    difference: Decimal | None = None
    tolerance: Decimal | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _grey(check_id: str, reason: str, **detail: Any) -> CheckResult:
    return CheckResult(
        check_id, GREY, tolerance=TOLERANCES[check_id], detail={"reason": reason, **detail}
    )


def _compare(check_id: str, lhs: Decimal, rhs: Decimal, **detail: Any) -> CheckResult:
    tolerance = TOLERANCES[check_id]
    assert tolerance is not None  # noqa: S101 - the table names a tolerance for every compared check
    difference = lhs - rhs
    status = GREEN if abs(difference) <= tolerance else RED
    return CheckResult(check_id, status, lhs, rhs, difference, tolerance, detail)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else _ZERO


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------


def _mart_scope(organization_id: str, bank_id: str, as_of: date) -> tuple[Any, ...]:
    return (
        BiFactPositionDaily.organization_id == organization_id,
        BiFactPositionDaily.bank_id == bank_id,
        BiFactPositionDaily.as_of_date == as_of,
    )


def _live_facts(
    db: Session, organization_id: str, bank_id: str
) -> tuple[date | None, dict[str, Decimal]]:
    """The live balance-sheet lines and the live plane's date (``None`` = no live plane)."""
    live_date = db.scalar(
        select(func.max(CurrentFinancialFact.source_as_of_date)).where(
            CurrentFinancialFact.organization_id == organization_id,
            CurrentFinancialFact.bank_id == bank_id,
        )
    )
    if live_date is None:
        return None, {}
    lines = {
        category: _dec(amount)
        for category, amount in db.execute(
            select(CurrentFinancialFact.category, func.sum(CurrentFinancialFact.amount))
            .where(
                CurrentFinancialFact.organization_id == organization_id,
                CurrentFinancialFact.bank_id == bank_id,
                CurrentFinancialFact.fact_group == BALANCE_SHEET_GROUP,
            )
            .group_by(CurrentFinancialFact.category)
        )
    }
    return live_date, lines


def check_r1_npl(db: Session, organization_id: str, bank_id: str, as_of: date) -> CheckResult:
    loan_rows, exposure, non_performing = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(BiFactPositionDaily.classification_exposure_rc), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            BiFactPositionDaily.non_performing.is_(True),
                            BiFactPositionDaily.classification_exposure_rc,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(
            *_mart_scope(organization_id, bank_id, as_of),
            BiFactPositionDaily.position_type == LOAN_TYPE,
        )
    ).one()
    engine = db.scalar(
        select(BiFactEngineMetric.value).where(
            BiFactEngineMetric.organization_id == organization_id,
            BiFactEngineMetric.bank_id == bank_id,
            BiFactEngineMetric.as_of_date == as_of,
            BiFactEngineMetric.module == CREDIT_MODULE,
            BiFactEngineMetric.metric_id == NPL_METRIC_ID,
            BiFactEngineMetric.tier == "live",
        )
    )
    if engine is None:
        return _grey("R1", "The live credit engine has no NPL ratio at this date.")
    if int(loan_rows or 0) == 0:
        # Absence is not agreement (audit A360): an empty loan mart and an engine
        # ratio of 0 are not two measurements that match, they are one
        # measurement and nothing. R5 grades a mart that lost its rows.
        return _grey(
            "R1",
            "The mart carries no loan rows at this date, so there is no NPL ratio to "
            "reconcile to the engine's.",
            engine_npl_ratio_pct=str(_dec(engine)),
        )
    total = _dec(exposure)
    ratio = (
        (_dec(non_performing) / total).quantize(ENGINE_RATIO_QUANTUM) * _HUNDRED
        if total > _ZERO
        else _ZERO
    )
    return _compare(
        "R1",
        ratio,
        _dec(engine),
        classification_exposure_rc=str(total),
        non_performing_exposure_rc=str(_dec(non_performing)),
    )


def _balance_sum(
    db: Session, organization_id: str, bank_id: str, as_of: date, position_type: str
) -> Decimal:
    return _dec(
        db.scalar(
            select(func.coalesce(func.sum(BiFactPositionDaily.balance_rc), 0)).where(
                *_mart_scope(organization_id, bank_id, as_of),
                BiFactPositionDaily.position_type == position_type,
            )
        )
    )


def check_r2_loans(db: Session, organization_id: str, bank_id: str, as_of: date) -> CheckResult:
    live_date, lines = _live_facts(db, organization_id, bank_id)
    if live_date is None:
        return _grey("R2", "No live balance-sheet facts exist for this bank.")
    if live_date != as_of:
        return _grey(
            "R2",
            "The live balance sheet is at another date.",
            live_as_of=live_date.isoformat(),
        )
    if LOANS_LINE not in lines:
        return _grey("R2", f"The live balance sheet carries no {LOANS_LINE} line.")
    return _compare(
        "R2", _balance_sum(db, organization_id, bank_id, as_of, LOAN_TYPE), lines[LOANS_LINE]
    )


def check_r3_deposits(db: Session, organization_id: str, bank_id: str, as_of: date) -> CheckResult:
    live_date, lines = _live_facts(db, organization_id, bank_id)
    if live_date is None:
        return _grey("R3", "No live balance-sheet facts exist for this bank.")
    if live_date != as_of:
        return _grey(
            "R3",
            "The live balance sheet is at another date.",
            live_as_of=live_date.isoformat(),
        )
    present = [line for line in DEPOSIT_LINES if line in lines]
    if not present:
        return _grey("R3", "The live balance sheet carries no deposit lines.")
    return _compare(
        "R3",
        _balance_sum(db, organization_id, bank_id, as_of, DEPOSIT_TYPE),
        sum((lines[line] for line in present), _ZERO),
        lines={line: str(lines[line]) for line in present},
    )


def _bsd7a_line_params() -> list[dict[str, Any]]:
    """The ``bsd7.pl_line`` params of every BSD7A ledger row, from the line map itself."""
    # Lazy: the line-map package imports every BoG form's layout and resolver
    # registry, which the builder must not pay for unless R4 actually runs.
    from app.services.regulatory_reporting.bog_forms.linemaps import (  # noqa: PLC0415
        line_maps_for,
    )

    seen: dict[str, dict[str, Any]] = {}
    for line in line_maps_for(_R4_FORM).get(_R4_FORM, ()):
        if line.source != _R4_SOURCE:
            continue
        tag = str(line.params.get("line") or "")
        if tag:
            seen.setdefault(tag, dict(line.params))
    return [seen[tag] for tag in sorted(seen)]


def _mart_pl_line(  # noqa: PLR0913 - the slice is six explicit keys
    db: Session,
    organization_id: str,
    bank_id: str,
    month_end: date,
    tag: str,
    classes: Iterable[str] | None,
    currency_rule: str,
    line_sign: Decimal,
) -> Decimal | None:
    """``line_sign × Σ (pl_sign × ytd_rc)`` for one line and currency slice;
    ``None`` when the line selects no account at all (BSD7's ``None``).

    Two signs multiply here, exactly as they do in the return
    (``bsd7._pl_line``): the REGISTER's per-account sign, which the mart stores
    per row as ``pl_sign``, and the LINE MAP's own ``sign`` param, which applies
    to the whole line. Every BSD7A ``pl()`` row leaves the second at 1 today, so
    omitting it was invisible — and would have turned R4 red with no mart defect
    the day a line map declared ``sign=-1`` (A5-08)."""
    predicates: list[Any] = [
        BiFactGlMonthly.organization_id == organization_id,
        BiFactGlMonthly.bank_id == bank_id,
        BiFactGlMonthly.month_end == month_end,
        BiFactGlMonthly.pl_line == tag,
    ]
    if classes:
        predicates.append(BiFactGlMonthly.account_class.in_(list(classes)))
    selected = db.scalar(select(func.count()).select_from(BiFactGlMonthly).where(*predicates))
    if not selected:
        return None
    if currency_rule == "domestic":
        predicates.append(BiFactGlMonthly.currency == "")
    elif currency_rule == "foreign":
        predicates.append(BiFactGlMonthly.currency != "")
    total = db.scalar(
        select(func.coalesce(func.sum(BiFactGlMonthly.pl_sign * BiFactGlMonthly.ytd_rc), 0)).where(
            *predicates
        )
    )
    return _dec(total) * line_sign


def check_r4_gl_pl(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> CheckResult:
    from app.services.regulatory_reporting.bog_forms.sources import (  # noqa: PLC0415
        ResolveContext,
        get_resolver,
    )

    first = as_of.replace(day=1)
    month_end = db.scalar(
        select(func.max(BiFactGlMonthly.month_end)).where(
            BiFactGlMonthly.organization_id == ctx.organization_id,
            BiFactGlMonthly.bank_id == bank.id,
            BiFactGlMonthly.calendar_month == first,
        )
    )
    if month_end is None:
        return _grey("R4", "No P&L ledger rows exist for this month.")
    # A transient period object for the resolver: BSD7 reads only its bounds.
    period = BankReportingPeriod(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        period_start=first,
        period_end=month_end,
        label=first.strftime("%Y-%m"),
        status="open",
    )
    resolve = get_resolver(_R4_SOURCE)
    cache: dict[str, Any] = {}
    mismatches: list[dict[str, Any]] = []
    compared = 0
    lhs_total = _ZERO
    rhs_total = _ZERO
    for params in _bsd7a_line_params():
        tag = str(params["line"])
        classes = params.get("gl_classes")
        line_sign = Decimal(str(params.get("sign", 1)))
        for column, currency_rule in _R4_COLUMNS:
            rc = ResolveContext(
                db=db, ctx=ctx, bank=bank, period=period, column=column, cache=cache
            )
            expected = resolve(rc, params)
            actual = _mart_pl_line(
                db,
                ctx.organization_id,
                bank.id,
                month_end,
                tag,
                classes,
                currency_rule,
                line_sign,
            )
            if expected is None and actual is None:
                continue
            compared += 1
            expected_dec = _dec(expected) if expected is not None else None
            lhs_total += actual or _ZERO
            rhs_total += expected_dec or _ZERO
            if actual is None or expected_dec is None or actual != expected_dec:
                mismatches.append(
                    {
                        "line": tag,
                        "column": column,
                        "mart": str(actual) if actual is not None else None,
                        "return": str(expected_dec) if expected_dec is not None else None,
                    }
                )
    if compared == 0:
        return _grey("R4", "No ledger account is mapped to a BSD7A line this month.")
    result = _compare(
        "R4", lhs_total, rhs_total, month_end=month_end.isoformat(), lines_compared=compared
    )
    if mismatches:
        return CheckResult(
            "R4",
            RED,
            result.lhs,
            result.rhs,
            result.difference,
            result.tolerance,
            {**result.detail, "mismatches": mismatches},
        )
    return result


def check_r5_completeness(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> CheckResult:
    mart = db.scalar(
        select(func.count())
        .select_from(BiFactPositionDaily)
        .where(*_mart_scope(organization_id, bank_id, as_of))
    )
    canonical = db.scalar(
        select(func.count(CanonicalPositionSnapshot.id)).where(
            CanonicalPositionSnapshot.organization_id == organization_id,
            CanonicalPositionSnapshot.bank_id == bank_id,
            *is_current_generation(CanonicalPositionSnapshot),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPositionSnapshot.as_of_date == as_of,
        )
    )
    mart_rows, canonical_rows = int(mart or 0), int(canonical or 0)
    if mart_rows == 0 and canonical_rows == 0:
        # ``0 == 0`` is not completeness (audit A360): nothing was projected
        # because nothing was there, and a pass here would badge an empty date.
        return _grey(
            "R5",
            "No included snapshot and no mart row exist at this date; there is nothing "
            "to reconcile, and an empty match is not a pass.",
        )
    return _compare("R5", Decimal(mart_rows), Decimal(canonical_rows))


def check_r6_unconverted(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> CheckResult:
    """Count only: the derivation exposes its unconverted count in warnings and
    per-fact attributes, not as a total, so there is nothing to compare to."""
    by_currency = {
        currency: int(count)
        for currency, count in db.execute(
            select(BiFactPositionDaily.currency, func.count())
            .where(
                *_mart_scope(organization_id, bank_id, as_of),
                BiFactPositionDaily.fx_unconverted.is_(True),
            )
            .group_by(BiFactPositionDaily.currency)
        )
    }
    total = sum(by_currency.values())
    return CheckResult(
        "R6",
        GREEN if total == 0 else AMBER,
        lhs=Decimal(total),
        detail={"by_currency": by_currency},
    )


def check_r7_branch_coverage(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> CheckResult:
    unmapped_codes = set(
        db.scalars(
            select(BiDimBranch.branch_code).where(
                BiDimBranch.organization_id == organization_id,
                BiDimBranch.bank_id == bank_id,
                BiDimBranch.mapped.is_(False),
            )
        )
    )
    rows = db.execute(
        select(BiFactPositionDaily.branch_code, func.sum(func.abs(BiFactPositionDaily.balance_rc)))
        .where(*_mart_scope(organization_id, bank_id, as_of))
        .group_by(BiFactPositionDaily.branch_code)
    ).all()
    total = _ZERO
    absent = _ZERO
    unmapped = _ZERO
    for code, balance in rows:
        amount = _dec(balance)
        total += amount
        if code is None:
            absent += amount
        elif code in unmapped_codes:
            unmapped += amount
    if total == _ZERO:
        return _grey("R7", "No reporting-currency balance is carried at this date.")
    share = (absent + unmapped) / total * _HUNDRED
    return CheckResult(
        "R7",
        GREEN if share == _ZERO else AMBER,
        lhs=share,
        detail={
            "uncovered_share_pct": str(share),
            "no_branch_rc": str(absent),
            "unmapped_branch_rc": str(unmapped),
            "unmapped_codes": sorted(unmapped_codes),
        },
    )


def check_r8_freshness(db: Session, organization_id: str, bank_id: str, as_of: date) -> CheckResult:
    live_date, _lines = _live_facts(db, organization_id, bank_id)
    if live_date is None:
        return _grey("R8", "No live plane exists for this bank.")
    behind = (live_date - as_of).days
    detail = {"mart_as_of": as_of.isoformat(), "live_as_of": live_date.isoformat()}
    if behind == 0:
        return CheckResult("R8", GREEN, difference=_ZERO, tolerance=TOLERANCES["R8"], detail=detail)
    return CheckResult(
        "R8",
        AMBER,
        difference=Decimal(behind),
        tolerance=TOLERANCES["R8"],
        detail={**detail, "reason": "The mart is not at the live plane's date."},
    )


_R9_STATUS = {"within_tolerance": GREEN, "exception_applied": AMBER, "blocked": RED}


def check_r9_balance_identity(db: Session, ctx: TenantContext, bank_id: str) -> CheckResult:
    live_date, lines = _live_facts(db, ctx.organization_id, bank_id)
    record = fact_derivation.current_reconciliation_record(db, ctx, bank_id)
    if record is None:
        if live_date is None:
            return _grey("R9", "No live plane exists for this bank.")
        if not lines:
            # The derivation stamps its record on a BALANCE-SHEET line; with no
            # such lines there is no book that could have balanced, and reading
            # the absent stamp as "balanced exactly" is absence read as agreement
            # (audit A360).
            return _grey(
                "R9",
                "The live plane carries no balance-sheet lines, so there is no book to "
                "have balanced.",
            )
        return CheckResult(
            "R9",
            GREEN,
            detail={
                "reason": "The live book balanced exactly; no plug was recorded.",
                "balance_sheet_lines": len(lines),
            },
        )
    status = _R9_STATUS.get(str(record.get("status")), GREY)
    assets = record.get("assets")
    funding = record.get("funding")
    gap = record.get("gap")
    return CheckResult(
        "R9",
        status,
        lhs=Decimal(str(assets)) if assets is not None else None,
        rhs=Decimal(str(funding)) if funding is not None else None,
        difference=Decimal(str(gap)) if gap is not None else None,
        detail={"record": record},
    )


def check_r10_dpd_completeness(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> CheckResult:
    """How much of the loan book carries no days-past-due band (D-042, D-049).

    ``dpd_band`` is NULL exactly when the loan states no ``days_past_due``
    (``domain/credit/dpd_bands``), and the PAR measures SELECT on it — so a book
    that never supplied the attribute reports 0 % portfolio-at-risk, which reads
    as a clean book rather than as an absent dataset. This check is what stops
    the trust badge going green over that.

    **The status follows the WORSE of two shares (D-049): missing ROWS and
    missing EXPOSURE.** ``green`` at 0, ``amber`` above it, ``red`` at 100 % of
    either. The figure being guarded is exposure-weighted — the engine's
    ``_portfolio_at_risk`` divides a DPD-covered numerator by the WHOLE book's
    exposure — so a gap of 1 % of rows carrying all of the value understates PAR
    completely while a row-share badge would read mild amber. ``max`` is
    deliberately conservative: it can only make the verdict more cautious.
    Both shares are always disclosed. Grey when the day holds no LOAN rows.

    The exposure basis is ``classification_exposure_rc``, which is the SAME
    basis the guarded ratio uses: ``_portfolio_at_risk``'s denominator is
    ``ClassificationResult.total_exposure_ghs``, summed from
    ``_load_loan_exposures``, which takes ``balance_ghs`` else the base-currency
    balance else ZERO for an unconverted foreign-currency loan — the D-015
    classification rule this column stores. So the share means what a reader
    comparing it against a certified ``par_90_pct`` would assume.

    It badges the COPIED engine PAR metrics too (D-046), and that is sound
    because both sides read the SAME attribute over the same population: the
    engine's ``loan_classification._load_raw_dpd_exposures`` skips a loan whose
    ``days_past_due`` is absent, and ``dpd_band`` is NULL for exactly those
    loans. One caveat stated rather than assumed: the equivalence holds only
    while the mart slice IS this date's book — R5 (row completeness) and R8
    (freshness) are the checks that say so, and the trust rollup composes them.

    A book whose loans are ALL unconverted foreign currency carries
    ``classification_exposure_rc = 0`` on every row (the same D-015 rule), so
    there is no value to weight and the exposure limb is 0: the verdict
    degenerates to the row share, which is exactly why ``max`` is the operator —
    a silent exposure limb can never make the badge greener. R6 reports those
    rows separately, and the two checks make DIFFERENT claims about them (no
    reporting-currency conversion vs no arrears data), so a thin book can
    legitimately raise both.
    """
    missing_band = BiFactPositionDaily.dpd_band.is_(None)
    loans, missing, exposure, missing_exposure = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((missing_band, 1), else_=0)), 0),
            func.coalesce(func.sum(BiFactPositionDaily.classification_exposure_rc), 0),
            func.coalesce(
                func.sum(
                    case((missing_band, BiFactPositionDaily.classification_exposure_rc), else_=0)
                ),
                0,
            ),
        )
        .select_from(BiFactPositionDaily)
        .where(
            *_mart_scope(organization_id, bank_id, as_of),
            BiFactPositionDaily.position_type == LOAN_TYPE,
        )
    ).one()
    loan_rows = int(loans or 0)
    without_band = int(missing or 0)
    if loan_rows == 0:
        return _grey(DPD_COMPLETENESS, "The day's slice carries no loan rows.")
    row_share = Decimal(without_band) / Decimal(loan_rows) * _HUNDRED
    total_exposure = _dec(exposure)
    # No exposure to weight (every loan unconverted, or a genuinely zero book):
    # the limb goes silent at 0 and ``max`` falls back to the row share.
    exposure_share = (
        _dec(missing_exposure) / total_exposure * _HUNDRED if total_exposure > _ZERO else _ZERO
    )
    # Both shares are exact: ``n/n * 100`` is 100 exactly, so the thresholds
    # below are equalities, not float comparisons.
    worst = max(row_share, exposure_share)
    status = GREEN if worst == _ZERO else RED if worst >= _HUNDRED else AMBER
    detail: dict[str, Any] = {
        "loan_rows": loan_rows,
        "loans_without_dpd_band": without_band,
        "missing_share_pct": str(row_share),
        "classification_exposure_rc": str(total_exposure),
        "missing_share_of_exposure_pct": str(exposure_share),
        "worst_share_pct": str(worst),
    }
    if status == RED:
        detail["reason"] = (
            "Every days-past-due figure over this book would read as zero arrears "
            "rather than as absent data: "
            + (
                "no loan states days past due."
                if without_band == loan_rows
                else "the loans that state none carry the whole book's exposure."
            )
        )
    return CheckResult(DPD_COMPLETENESS, status, lhs=worst, detail=detail)


def check_r11_gl_branch_identity(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> CheckResult:
    """The branch breakdown of the ledger sums to the ledger, per account.

    The left-hand side is ``Σ ytd_rc`` over ``bi_fact_gl_branch_monthly``'s
    branches — the reported branches AND the computed unallocated remainder — per
    (account, month, currency); the right-hand side is the institution row in
    ``bi_fact_gl_monthly``, which R4 has separately reconciled to BSD7A's own
    resolver. So a green R4 and a green R11 together say the branch figures add up
    to the figures the return files.

    The identity holds BY CONSTRUCTION when the rows are written
    (``extract.gl_branch_monthly_rows`` derives the residual from exactly this
    subtraction), which is the point: this check exists so the construction is
    PROVEN on the stored rows rather than asserted in the module that wrote them.
    Any difference is therefore a defect in the writing, not a rounding — hence an
    exact tolerance.

    Three gradings, one subject:

    * ``red`` — an account's branch rows do not sum to its institution row, or the
      branch mart carries an account the institution mart does not (which would
      make a branch total exceed the ledger).
    * ``amber`` — the identity holds, but part of the month's ledger is not
      attributed to any branch: either on the unallocated remainder, or on an
      account the register did not mention at all. The total is right and the
      breakdown is incomplete, and those are different statements.
    * ``green`` — the identity holds and every account's whole balance sits on a
      real branch. Also the answer for a month with no branch rows at all
      (``accounts_compared: 0``): the mart makes no branch claim, so nothing went
      unassessed. Which dataset would produce one is the surface's business, not
      this badge's.
    """
    calendar_month = as_of.replace(day=1)
    institution = {
        (str(code), str(currency)): _dec(ytd)
        for code, currency, ytd in db.execute(
            select(
                BiFactGlMonthly.gl_account_code,
                BiFactGlMonthly.currency,
                BiFactGlMonthly.ytd_rc,
            ).where(
                BiFactGlMonthly.organization_id == organization_id,
                BiFactGlMonthly.bank_id == bank_id,
                BiFactGlMonthly.calendar_month == calendar_month,
            )
        )
    }
    residual_id = gl_segment_balances.RESIDUAL_BRANCH_ID
    branch = {
        (str(code), str(currency)): (_dec(total), _dec(residual))
        for code, currency, total, residual in db.execute(
            select(
                BiFactGlBranchMonthly.gl_account_code,
                BiFactGlBranchMonthly.currency,
                func.sum(BiFactGlBranchMonthly.ytd_rc),
                func.sum(
                    case(
                        (
                            BiFactGlBranchMonthly.branch_code == residual_id,
                            BiFactGlBranchMonthly.ytd_rc,
                        ),
                        else_=0,
                    )
                ),
            )
            .where(
                BiFactGlBranchMonthly.organization_id == organization_id,
                BiFactGlBranchMonthly.bank_id == bank_id,
                BiFactGlBranchMonthly.calendar_month == calendar_month,
            )
            .group_by(BiFactGlBranchMonthly.gl_account_code, BiFactGlBranchMonthly.currency)
        )
    }
    detail: dict[str, Any] = {
        "month": calendar_month.isoformat(),
        "accounts_compared": len(branch),
        "accounts_in_ledger": len(institution),
    }
    if not branch:
        detail["reason"] = (
            "The ledger carries no branch breakdown for this month, so no branch figure is shown "
            "and none is claimed."
        )
        return CheckResult(
            GL_BRANCH_IDENTITY, GREEN, tolerance=TOLERANCES[GL_BRANCH_IDENTITY], detail=detail
        )

    mismatches: list[dict[str, Any]] = []
    inverted: list[dict[str, Any]] = []
    lhs_total = _ZERO
    rhs_total = _ZERO
    unattributed_abs = _ZERO
    for key in sorted(branch):
        total, residual = branch[key]
        expected = institution.get(key)
        lhs_total += total
        unattributed_abs += abs(residual)
        if expected is not None and abs(residual) > abs(expected):
            # PER ACCOUNT (audit A360 R11): the branch rows for this account pull
            # AWAY from its ledger figure — a residual larger than the ledger it
            # completes, which a partial breakdown of the right sign cannot
            # produce. The aggregate share below cannot see this: one inverted
            # account beside a fully allocated large one dilutes to an ordinary
            # amber, and a ZERO ledger against non-zero branch rows leaves the
            # share at zero. So the rule is evaluated where the fault is.
            inverted.append(
                {
                    "account": key[0],
                    "currency": key[1],
                    "ledger": str(expected),
                    "branch_total": str(total),
                    "residual": str(residual),
                }
            )
        if expected is None:
            mismatches.append(
                {
                    "account": key[0],
                    "currency": key[1],
                    "branch_total": str(total),
                    "ledger": None,
                    "reason": "the institution ledger carries no row for this account this month",
                }
            )
            continue
        rhs_total += expected
        if total != expected:
            mismatches.append(
                {
                    "account": key[0],
                    "currency": key[1],
                    "branch_total": str(total),
                    "ledger": str(expected),
                }
            )
    # Accounts the register never mentioned are not a mismatch — they simply have
    # no breakdown — but their balance is just as unattributed as a residual, and a
    # coverage statement that ignored them would flatter the breakdown.
    for key, expected in institution.items():
        if key not in branch:
            unattributed_abs += abs(expected)
    ledger_abs = sum((abs(value) for value in institution.values()), _ZERO)
    share = _ZERO if ledger_abs == _ZERO else unattributed_abs / ledger_abs * _HUNDRED
    detail["unattributed_share_pct"] = str(share)
    detail["unattributed_rc"] = str(unattributed_abs)
    detail["ledger_abs_rc"] = str(ledger_abs)
    if mismatches:
        detail["mismatches"] = mismatches
        return CheckResult(
            GL_BRANCH_IDENTITY,
            RED,
            lhs_total,
            rhs_total,
            lhs_total - rhs_total,
            TOLERANCES[GL_BRANCH_IDENTITY],
            detail,
        )
    if inverted:
        detail["sign_convention"] = inverted
        detail["reason"] = (
            "the branch figures for "
            + ", ".join(f"{item['account']}" for item in inverted)
            + " move further from the ledger than the ledger's own size, which a partial "
            "breakdown cannot do — check the register's sign convention against the "
            "ledger's for these accounts"
        )
        return CheckResult(
            GL_BRANCH_IDENTITY,
            RED,
            lhs_total,
            rhs_total,
            lhs_total - rhs_total,
            TOLERANCES[GL_BRANCH_IDENTITY],
            detail,
        )
    # A share ABOVE 100% is impossible for a correctly-signed register, and the
    # identity cannot catch it: the residual absorbs whatever the branches did not
    # account for, so the sum still equals the ledger by construction (audit
    # A11-F4). What it means is that the reported branch figures pull AWAY from the
    # ledger rather than toward it — overwhelmingly because the bank sent its
    # profit-and-loss under the opposite sign convention, credits positive where
    # the ledger has them negative. A ledger of -1000 against a register of +900
    # leaves a residual of -1900 and reads as "190% unattributed".
    #
    # That is a data-quality fault, not partial coverage, so it is RED with a reason
    # rather than amber with a nonsense percentage. Amber says "part of the ledger
    # is not yet broken down", which a reader can act on by asking for more of the
    # register; this needs them to fix the register they already sent, and the two
    # are not the same request.
    if share > _HUNDRED:
        detail["reason"] = (
            "the branch figures move further from the ledger than the ledger's own "
            "size, which a partial breakdown cannot do — check the register's sign "
            "convention against the ledger's"
        )
        return CheckResult(
            GL_BRANCH_IDENTITY,
            RED,
            lhs_total,
            rhs_total,
            lhs_total - rhs_total,
            TOLERANCES[GL_BRANCH_IDENTITY],
            detail,
        )
    return CheckResult(
        GL_BRANCH_IDENTITY,
        GREEN if share == _ZERO else AMBER,
        lhs_total,
        rhs_total,
        _ZERO,
        TOLERANCES[GL_BRANCH_IDENTITY],
        detail,
    )


def check_r12_arrears_completeness(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> CheckResult:
    """How much of the loan book states no arrears amount (P5-A §6.6).

    R10's argument, on the figure the bank STATES rather than the band the
    platform derives from days past due. ``arrears_amount_rc`` is NULL exactly
    when the bank supplied no ``attributes.arrears_amount`` for the facility (or
    the facility is unconverted foreign currency, the D-015 derivation rule), and
    ``loans.arrears_amount_rc`` sums only the rows that state one. So a bank that
    states arrears for HALF its book shows a sum that reads as the whole book's
    arrears and a share silently understated — with no visible defect anywhere.
    This check is what stops the trust badge going green over that.

    **The status follows the WORSE of two shares (D-049): missing ROWS and
    missing EXPOSURE.** ``green`` at 0, ``amber`` above it, ``red`` at 100 % of
    either. The same reason as R10: the guarded figure
    (``loans.arrears_share_pct``) is exposure-weighted, so a gap of 1 % of rows
    carrying all of the value understates it completely while a row-share badge
    would read mild amber. ``max`` can only make the verdict more cautious, and
    both shares are always disclosed.

    **The population is loans with a POSITIVE classified exposure, and that is
    what separates this check from R10.** ``arrears_amount_rc`` is NULL for an
    unconverted foreign-currency loan whatever the bank stated (the derivation
    rule, exactly like ``balance_rc``), so counting those rows as unstated arrears
    would report a gap the bank cannot close by stating arrears — it closes it by
    supplying conversions, which is **R6's** message, not this one. Under the
    classification rule such a loan carries ``classification_exposure_rc = 0``, so
    ``> 0`` excludes precisely them; it also excludes a facility with nothing
    outstanding, which has nothing to be in arrears on. R10 keeps those rows
    because ``dpd_band`` is stated or not stated independently of FX, and the
    engine's PAR denominator includes them. Two checks, two populations, each
    matching the figure it guards.

    On what remains, ``classification_exposure_rc`` equals ``balance_rc`` for
    every row — the two FX rules only diverge where a conversion is missing — so
    weighting by it is the same weighting ``loans.arrears_share_pct``'s
    ``balance_rc`` denominator uses, stated in the column that also expresses the
    population filter. Grey when nothing is left: no loan row of the day carries a
    positive classified exposure, so there is nothing to be complete about.

    One consequence of that population, stated so it is not read as an untested
    implication of ``max``: **the two limbs cannot disagree on the COLOUR here,
    only on the magnitude.** Red is 100 % of either, and a zero-exposure row is
    excluded, so "every row silent" and "all the exposure silent" coincide and
    neither limb reaches 100 % alone. ``max`` therefore chooses the figure the
    badge REPORTS — the exposure-weighted one when it is worse, which is what a
    reader compares against ``loans.arrears_share_pct`` — and remains the
    conservative direction if the population ever changes. R10's ``max`` does
    decide a colour, because its population keeps the zero-exposure rows.
    """
    exposure = BiFactPositionDaily.classification_exposure_rc
    missing_arrears = BiFactPositionDaily.arrears_amount_rc.is_(None)
    loans, missing, total_exposure, missing_exposure, stated = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((missing_arrears, 1), else_=0)), 0),
            func.coalesce(func.sum(exposure), 0),
            func.coalesce(func.sum(case((missing_arrears, exposure), else_=0)), 0),
            func.coalesce(func.sum(BiFactPositionDaily.arrears_amount_rc), 0),
        )
        .select_from(BiFactPositionDaily)
        .where(
            *_mart_scope(organization_id, bank_id, as_of),
            BiFactPositionDaily.position_type == LOAN_TYPE,
            exposure.is_not(None),
            exposure > _ZERO,
        )
    ).one()
    loan_rows = int(loans or 0)
    without_arrears = int(missing or 0)
    if loan_rows == 0:
        return _grey(
            ARREARS_COMPLETENESS,
            "The day's slice carries no loan rows with a classified exposure to be in arrears on.",
        )
    row_share = Decimal(without_arrears) / Decimal(loan_rows) * _HUNDRED
    exposure_total = _dec(total_exposure)
    exposure_share = (
        _dec(missing_exposure) / exposure_total * _HUNDRED if exposure_total > _ZERO else _ZERO
    )
    worst = max(row_share, exposure_share)
    status = GREEN if worst == _ZERO else RED if worst >= _HUNDRED else AMBER
    detail: dict[str, Any] = {
        "loan_rows": loan_rows,
        "loans_without_arrears_amount": without_arrears,
        "missing_share_pct": str(row_share),
        "classification_exposure_rc": str(exposure_total),
        "missing_share_of_exposure_pct": str(exposure_share),
        "worst_share_pct": str(worst),
        "stated_arrears_rc": str(_dec(stated)),
    }
    if status == RED:
        detail["reason"] = (
            "Every arrears figure over this book would read as a performing book rather "
            "than as absent data: "
            + (
                "no loan states an arrears amount."
                if without_arrears == loan_rows
                else "the loans that state none carry the whole book's exposure."
            )
        )
    elif status == AMBER:
        detail["reason"] = (
            "The arrears figures cover part of the loan book, so a total or a share "
            "over them understates the whole book by whatever was not stated."
        )
    return CheckResult(ARREARS_COMPLETENESS, status, lhs=worst, detail=detail)


# ---------------------------------------------------------------------------
# evaluate / persist / trust
# ---------------------------------------------------------------------------


def evaluate(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> dict[str, CheckResult]:
    """Run every check for ``(bank, as_of)``; one that raises is ``grey`` with its error."""
    organization_id, bank_id = ctx.organization_id, bank.id
    _ = jurisdictions.base_currency(bank)  # fail loud on a bank with no reporting currency
    runners = {
        "R1": lambda: check_r1_npl(db, organization_id, bank_id, as_of),
        "R2": lambda: check_r2_loans(db, organization_id, bank_id, as_of),
        "R3": lambda: check_r3_deposits(db, organization_id, bank_id, as_of),
        "R4": lambda: check_r4_gl_pl(db, ctx, bank, as_of),
        "R5": lambda: check_r5_completeness(db, organization_id, bank_id, as_of),
        "R6": lambda: check_r6_unconverted(db, organization_id, bank_id, as_of),
        "R7": lambda: check_r7_branch_coverage(db, organization_id, bank_id, as_of),
        "R8": lambda: check_r8_freshness(db, organization_id, bank_id, as_of),
        "R9": lambda: check_r9_balance_identity(db, ctx, bank_id),
        DPD_COMPLETENESS: lambda: check_r10_dpd_completeness(db, organization_id, bank_id, as_of),
        GL_BRANCH_IDENTITY: lambda: check_r11_gl_branch_identity(
            db, organization_id, bank_id, as_of
        ),
        ARREARS_COMPLETENESS: lambda: check_r12_arrears_completeness(
            db, organization_id, bank_id, as_of
        ),
    }
    results: dict[str, CheckResult] = {}
    for check_id in CHECK_IDS:
        try:
            results[check_id] = runners[check_id]()
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is grey, never a build failure
            logger.warning(
                "bi.reconciliation.%s failed bank=%s as_of=%s: %s", check_id, bank_id, as_of, exc
            )
            results[check_id] = _grey(
                check_id, f"The check could not run: {type(exc).__name__}: {exc}"[:500]
            )
    return results


def persist(  # noqa: PLR0913 - the row identity plus the build stamp
    db: Session,
    results: Mapping[str, CheckResult],
    *,
    organization_id: str,
    bank_id: str,
    as_of: date,
    builder_version: int,
    evaluated_at: datetime,
) -> None:
    """Upsert one ``bi_reconciliation_results`` row per check for ``(bank, as_of)``."""
    existing = {
        row.check_id: row
        for row in db.scalars(
            select(BiReconciliationResult).where(
                BiReconciliationResult.organization_id == organization_id,
                BiReconciliationResult.bank_id == bank_id,
                BiReconciliationResult.as_of_date == as_of,
            )
        )
    }
    for check_id, result in results.items():
        if check_id not in STORABLE_CHECK_IDS:
            # Not a silent drop: the check ran, the build's own badge carries it,
            # and the storage vocabulary is named in the log and the docstring.
            logger.info(
                "bi.reconciliation.%s evaluated %s but not stored: "
                "bi_reconciliation_results admits only %s",
                check_id,
                result.status,
                ", ".join(STORABLE_CHECK_IDS),
            )
            continue
        row = existing.get(check_id)
        if row is None:
            row = BiReconciliationResult(
                organization_id=organization_id,
                bank_id=bank_id,
                as_of_date=as_of,
                check_id=check_id,
            )
            db.add(row)
        row.status = result.status
        row.lhs = result.lhs
        row.rhs = result.rhs
        row.difference = result.difference
        row.tolerance = result.tolerance
        row.detail = dict(result.detail)
        row.builder_version = builder_version
        row.evaluated_at = evaluated_at
    db.flush()


def overall_trust(statuses: Iterable[str | None]) -> str:
    """``red`` beats ``amber`` beats ``grey`` beats ``green``; a missing check is grey."""
    seen = set(statuses)
    if RED in seen:
        return RED
    if AMBER in seen:
        return AMBER
    if GREY in seen or None in seen:
        return GREY
    return GREEN


def trust_of(results: Mapping[str, CheckResult]) -> dict[str, str]:
    """The badge for a build: every EVALUATED check (:data:`CHECK_IDS`)."""
    statuses = {
        check_id: (results[check_id].status if check_id in results else GREY)
        for check_id in CHECK_IDS
    }
    return {**statuses, OVERALL: overall_trust(statuses.values())}


def trust_for(db: Session, organization_id: str, bank_id: str, as_of: date) -> dict[str, str]:
    """The persisted trust of ``(bank, as_of)``: every STORED check plus ``overall``.

    Reads :data:`STORABLE_CHECK_IDS`, so a check the database cannot hold yet is
    absent rather than a permanent grey that would drag every tenant's stored
    badge down (module docstring, "Storage vs evaluation").
    """
    stored = {
        check_id: status
        for check_id, status in db.execute(
            select(BiReconciliationResult.check_id, BiReconciliationResult.status).where(
                BiReconciliationResult.organization_id == organization_id,
                BiReconciliationResult.bank_id == bank_id,
                BiReconciliationResult.as_of_date == as_of,
            )
        )
    }
    statuses = {check_id: stored.get(check_id, GREY) for check_id in STORABLE_CHECK_IDS}
    return {**statuses, OVERALL: overall_trust(statuses.values())}


__all__ = [
    "AMBER",
    "ARREARS_COMPLETENESS",
    "CHECK_IDS",
    "DEPOSIT_LINES",
    "DPD_COMPLETENESS",
    "ENGINE_RATIO_QUANTUM",
    "GL_BRANCH_IDENTITY",
    "GREEN",
    "GREY",
    "LOANS_LINE",
    "NPL_METRIC_ID",
    "OVERALL",
    "RED",
    "STORABLE_CHECK_IDS",
    "TOLERANCES",
    "CheckResult",
    "check_r10_dpd_completeness",
    "check_r11_gl_branch_identity",
    "evaluate",
    "overall_trust",
    "persist",
    "trust_for",
    "trust_of",
]
