"""``bi_fact_target``: the bank's budget, PRE-MATCHED to its actual (D-064).

The founder's decision moved one rule into the builder — **a target declared
for exactly this scope wins, the bank-wide target is the fallback, and the two
are NEVER summed** — so that rule gets its own table of cases here, over all
four states a scope can be in, plus the end-to-end proof that what the builder
writes is what the catalogue's variants then read.

The figures are worked from the canonical fixture's own amounts
(``tests/support/factories/canonical.py``): seven converted loans totalling 84 850 000
in the reporting currency at 2026-06-30.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import catalogue
from app.domain.bi.catalogue.targets import TARGET_VERSION, variant_id
from app.models import CanonicalReferenceRow
from app.models.bi import BiFactTarget
from app.schemas.bi import BiQuery, BiTime
from app.services.bi import mart_builder
from app.services.bi.compiler import compile_query
from app.services.bi.mart_builder import (
    BANK_WIDE_SCOPE,
    DeclaredTarget,
    resolve_target_for_scope,
    target_window,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_mart_builder import (
    AS_OF,
    FIXTURE_LOANS_RC,
    build,
    new_batch,
    seed_book,
)
from tests.support.helpers import ORG_1

LOANS = "loans.balance_rc"
NPL_PCT = "loans.npl_ratio_pct"
QUARTER_END = AS_OF  # 2026-06-30 is both the fixture's date and Q2's last day


# --- the scope-matching rule, as a table ----------------------------------------------------


def _declared(scope_dimension: str, scope_value: str, value: str) -> DeclaredTarget:
    return DeclaredTarget(
        measure_id=LOANS,
        scope_dimension=scope_dimension,
        scope_value=scope_value,
        version=TARGET_VERSION,
        grain="quarter",
        time_behaviour="stock",
        period_start=date(2026, 4, 1),
        period_end=QUARTER_END,
        value=Decimal(value),
    )


BANK_WIDE = _declared("", "", "100")
BRANCH_ONE = _declared("branch.code", "BR-001", "40")
SCOPE_ONE = ("branch.code", "BR-001")

#: (case, candidates, scope asked for, expected value, expected basis)
_Case = tuple[str, list[DeclaredTarget], tuple[str, str], str | None, str | None]
SCOPE_CASES: tuple[_Case, ...] = (
    ("exact only", [BRANCH_ONE], SCOPE_ONE, "40", "exact"),
    ("bank-wide only", [BANK_WIDE], SCOPE_ONE, "100", "bank_wide"),
    ("both present", [BANK_WIDE, BRANCH_ONE], SCOPE_ONE, "40", "exact"),
    ("neither", [], SCOPE_ONE, None, None),
    ("bank-wide asked, declared", [BANK_WIDE, BRANCH_ONE], BANK_WIDE_SCOPE, "100", "exact"),
    ("bank-wide asked, only scoped", [BRANCH_ONE], BANK_WIDE_SCOPE, None, None),
)


@pytest.mark.parametrize(
    ("case", "candidates", "scope", "value", "basis"),
    SCOPE_CASES,
    ids=[case[0] for case in SCOPE_CASES],
)
def test_exact_scope_wins_bank_wide_is_the_fallback(
    case: str,
    candidates: list[DeclaredTarget],
    scope: tuple[str, str],
    value: str | None,
    basis: str | None,
) -> None:
    resolved = resolve_target_for_scope(candidates, scope=scope)
    if value is None:
        assert resolved is None, case
        return
    assert resolved is not None, case
    assert resolved.declared.value == Decimal(value), case
    assert resolved.basis == basis, case


def test_the_two_bases_are_never_summed() -> None:
    """The property, stated separately from the cases that exercise it.

    Whatever the candidate set, at most ONE target comes back, and its value is
    one of the declared ones — never their sum. That is what makes a bank-wide
    target beside a per-branch one impossible to double-count at any grain,
    rather than merely unlikely.
    """
    for scope in (SCOPE_ONE, BANK_WIDE_SCOPE, ("branch.code", "BR-999")):
        resolved = resolve_target_for_scope([BANK_WIDE, BRANCH_ONE], scope=scope)
        if resolved is None:
            continue
        assert resolved.declared.value in (BANK_WIDE.value, BRANCH_ONE.value)
        assert resolved.declared.value != BANK_WIDE.value + BRANCH_ONE.value


def test_a_restated_target_is_the_later_statement_not_a_second_one() -> None:
    first, second = _declared("", "", "100"), _declared("", "", "120")
    resolved = resolve_target_for_scope([first, second], scope=BANK_WIDE_SCOPE)
    assert resolved is not None and resolved.declared.value == Decimal("120")


def test_a_period_that_is_not_its_grains_last_day_names_no_window() -> None:
    """A quarterly target dated 15 March is a mistake about which quarter is meant."""
    assert target_window(date(2026, 3, 31), "quarter") == (date(2026, 1, 1), date(2026, 3, 31))
    assert target_window(date(2026, 3, 15), "quarter") is None
    assert target_window(date(2026, 6, 30), "half_year") == (date(2026, 1, 1), date(2026, 6, 30))
    assert target_window(date(2026, 12, 31), "year") == (date(2026, 1, 1), date(2026, 12, 31))
    assert target_window(date(2026, 2, 28), "month") == (date(2026, 2, 1), date(2026, 2, 28))
    assert target_window(date(2026, 6, 30), "fortnight") is None


# --- the register enters the build fingerprint ----------------------------------------------


def test_the_targets_register_is_a_build_fingerprint_input() -> None:
    """Without this a re-pushed budget serves the OLD variance out of cache."""
    assert mart_builder.TARGETS_KIND == "performance_targets"
    assert mart_builder.TARGETS_KIND in mart_builder.REFERENCE_KINDS


# --- end to end -----------------------------------------------------------------------------


def push_targets(db: Session, rows: list[dict[str, Any]], as_of: date = AS_OF) -> None:
    """One ``performance_targets`` batch, as the Data Engine would land it."""
    common = new_batch(db, as_of)
    for index, payload in enumerate(rows):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=common["ingestion_batch_id"],
                as_of_date=as_of,
                dataset_kind="performance_targets",
                row_index=index,
                payload=payload,
                source_reference=f"target#{index}",
                lineage_id=common["lineage_id"],
            )
        )
    db.commit()


def target_row(db: Session, measure_id: str, scope: tuple[str, str] = BANK_WIDE_SCOPE) -> Any:
    return db.scalar(
        select(BiFactTarget).where(
            BiFactTarget.bank_id == SAMPLE_BANK_ID,
            BiFactTarget.as_of_date == AS_OF,
            BiFactTarget.measure_id == measure_id,
            BiFactTarget.scope_dimension == scope[0],
            BiFactTarget.scope_value == scope[1],
        )
    )


def _target(measure_id: str, value: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "period": QUARTER_END.isoformat(),
        "grain": "quarter",
        "measure_id": measure_id,
        "time_behaviour": "stock",
        "value": value,
        "version": TARGET_VERSION,
    }
    row.update(overrides)
    return row


def _measure_value(db: Session, member_id: str) -> Decimal | float | None:
    compiled = compile_query(
        db,
        catalogue(),
        BiQuery(measures=[member_id], time=BiTime(as_of=AS_OF)),
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
    )
    rows = db.execute(compiled.select).all()
    return rows[0][-1] if rows else None


def test_a_bank_wide_target_is_matched_to_the_actual_the_compiler_reads(
    db_session: Session,
) -> None:
    """The variance is the fixture's own loan book minus the bank's own plan."""
    seed_book(db_session)
    push_targets(db_session, [_target(LOANS, "80000000")])
    build(db_session)

    row = target_row(db_session, LOANS)
    assert row is not None
    assert row.target_value == Decimal("80000000")
    assert row.actual_value == FIXTURE_LOANS_RC
    assert row.variance_value == FIXTURE_LOANS_RC - Decimal("80000000")
    assert (row.scope_basis, row.declared_scope_dimension) == ("exact", "")
    assert (row.period_start, row.period_end, row.period_grain) == (
        date(2026, 4, 1),
        QUARTER_END,
        "quarter",
    )

    # ... and the catalogue's variants read exactly that, through the compiler.
    assert _measure_value(db_session, variant_id(LOANS, "target")) == Decimal("80000000")
    assert _measure_value(db_session, variant_id(LOANS, "actual")) == FIXTURE_LOANS_RC
    assert _measure_value(db_session, variant_id(LOANS, "variance")) == FIXTURE_LOANS_RC - Decimal(
        "80000000"
    )
    attainment = _measure_value(db_session, variant_id(LOANS, "attainment_pct"))
    assert attainment == pytest.approx(float(FIXTURE_LOANS_RC) / 80000000 * 100)


def test_a_measure_with_no_target_is_blank_even_beside_one_that_has_it(
    db_session: Session,
) -> None:
    """The D-015 hazard, in the one place it could still bite.

    A bank that budgets its loan book and not its NPL ratio has ``bi_fact_target``
    rows for the date. If the measure discriminator were a SELECTION the NPL
    variants would read ``0.00`` — a bank exactly on plan — instead of blank.
    """
    seed_book(db_session)
    push_targets(db_session, [_target(LOANS, "80000000")])
    build(db_session)

    assert target_row(db_session, NPL_PCT) is None
    for suffix in ("actual", "target", "variance", "variance_pct"):
        assert _measure_value(db_session, variant_id(NPL_PCT, suffix)) is None, suffix
    assert _measure_value(db_session, variant_id(LOANS, "target")) is not None


def test_a_target_of_zero_is_a_real_target_and_the_percentages_stay_blank(
    db_session: Session,
) -> None:
    """Zero write-offs is a legitimate plan; dividing by it is not a number."""
    seed_book(db_session)
    push_targets(db_session, [_target(LOANS, "0")])
    build(db_session)

    row = target_row(db_session, LOANS)
    assert row is not None and row.target_value == Decimal(0)
    assert _measure_value(db_session, variant_id(LOANS, "target")) == Decimal(0)
    assert _measure_value(db_session, variant_id(LOANS, "variance")) == FIXTURE_LOANS_RC
    assert _measure_value(db_session, variant_id(LOANS, "variance_pct")) is None
    assert _measure_value(db_session, variant_id(LOANS, "attainment_pct")) is None


def test_a_scoped_target_lands_beside_the_bank_wide_one_without_summing_it(
    db_session: Session,
) -> None:
    seed_book(db_session)
    push_targets(
        db_session,
        [
            _target(LOANS, "80000000"),
            _target(LOANS, "30000000", scope_dimension="branch.code", scope_value="BR-001"),
        ],
    )
    build(db_session)

    bank_wide = target_row(db_session, LOANS)
    scoped = target_row(db_session, LOANS, ("branch.code", "BR-001"))
    assert bank_wide is not None and scoped is not None
    assert (bank_wide.target_value, bank_wide.scope_basis) == (Decimal("80000000"), "exact")
    assert (scoped.target_value, scoped.scope_basis) == (Decimal("30000000"), "exact")
    # The bank-wide variant reads the bank-wide row alone — never their sum.
    assert _measure_value(db_session, variant_id(LOANS, "target")) == Decimal("80000000")


def test_a_target_outside_its_own_window_is_not_compared(db_session: Session) -> None:
    """A Q1 target is not a Q2 comparison; the build date decides."""
    seed_book(db_session)
    push_targets(db_session, [_target(LOANS, "80000000", period="2026-03-31")])
    build(db_session)
    assert target_row(db_session, LOANS) is None


def test_a_declared_basis_that_disagrees_with_the_measure_is_refused(
    db_session: Session,
) -> None:
    """Subtracting a period's accumulation from a level is silently a period wrong."""
    seed_book(db_session)
    push_targets(db_session, [_target(LOANS, "80000000", time_behaviour="flow")])
    build(db_session)
    assert target_row(db_session, LOANS) is None


def test_a_measure_the_catalogue_does_not_make_targetable_is_refused(
    db_session: Session,
) -> None:
    seed_book(db_session)
    push_targets(
        db_session,
        [
            _target("loans.sector_hhi", "1200"),
            _target("loans.not_a_measure_at_all", "5"),
            _target(LOANS, "80000000"),
        ],
    )
    build(db_session)
    assert target_row(db_session, "loans.sector_hhi") is None
    assert target_row(db_session, "loans.not_a_measure_at_all") is None
    assert target_row(db_session, LOANS) is not None


def test_a_re_pushed_budget_is_replaced_not_accumulated(db_session: Session) -> None:
    seed_book(db_session)
    push_targets(db_session, [_target(LOANS, "80000000")])
    first = build(db_session)
    push_targets(db_session, [_target(LOANS, "90000000")])
    second = build(db_session)

    assert second.fingerprint != first.fingerprint, (
        "a re-pushed budget must move the build fingerprint, or every BI surface "
        "serves the old variance out of cache"
    )
    rows = db_session.scalars(
        select(BiFactTarget).where(
            BiFactTarget.bank_id == SAMPLE_BANK_ID, BiFactTarget.measure_id == LOANS
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].target_value == Decimal("90000000")
