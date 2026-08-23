"""Reporting equivalence gate — every engine-backed BoG cell ties to its run.

The forensic audit's reporting verdict (§8):

    "It is **not proven** that every BoG template ratio or total which overlaps
    an engine has an automated equivalence test against that engine."

The WS-A authority registry pass confirmed exactly one such proof existed — the
BSD5A CAR *inequality* in ``bog_forms/test_bsd5.py``, which pins a declared
alternate methodology. This module closes the rest of the gap: it walks every
registered form, finds every declared line bound to an engine-backed resolver
(``provenance.ENGINE_BACKED_RESOLVERS``), and proves the figure that reached the
immutable package snapshot is the figure the source ``RegulatoryRun`` holds.

The comparison deliberately goes around the resolver: it reads the run's
persisted ``RegulatoryLineItem`` / ``metrics`` directly and compares against the
SNAPSHOT cell, so it exercises resolution, dependency ordering, unit scaling and
sealing end to end rather than re-running the same function twice.

What this must never become
---------------------------

An equality test between two DECLARED methodologies. BSD5A's ``E70 = E25/E69``
is BoG's own ratio over 50% of the net open position and 100% of the three-year
average gross income; the capital engine's ``car_pct`` applies an FX charge and
a BIA charge through RWA multipliers. They differ by construction. Likewise
LCR-NSFR's aggregate-capped LCR and LMT Table 11's per-currency 75%-capped LCR
(both cap; the divergence is the cap's source and granularity). Nothing here
compares those.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    BankReportingPeriod,
    RegulatoryLineItem,
    RegulatoryPackage,
    RegulatoryRun,
)
from app.schemas.regulatory_fx import FxScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import regulatory_capital, regulatory_fx
from app.services.regulatory_reporting import generation
from app.services.regulatory_reporting.bog_forms.catalog import all_form_specs
from app.services.regulatory_reporting.bog_forms.spec import UNIT_DIVISOR
from app.services.regulatory_reporting.provenance import ENGINE_BACKED_RESOLVERS
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)

#: Absolute tolerance in the SHEET's unit. The snapshot value is the base-unit
#: figure divided by the sheet divisor and rendered as text, so the only
#: permissible difference is float/decimal representation — not a methodology
#: difference, which would be a finding, not a tolerance.
_TOLERANCE = Decimal("0.000001")

#: measure -> run metric key, for the BOOK-LEVEL FX figures the forms report.
#: Mirrors ``sources_ext/bsd13.py::_BOOK_METRICS`` and
#: ``sources_ext/bsd1b.py::_aggregate`` — duplicated ON PURPOSE so the gate fails
#: if either mapping is edited without review.
_FX_AGGREGATES: dict[str, str] = {
    "afop": "nop_ghs",
    "net_worth": "tier1_ghs",
    "nof": "tier1_ghs",
    "afop_pct_nof": "nop_pct_tier1",
    "aggregate_limit_pct": "nop_aggregate_limit_pct",
    "afop_limit_pct": "nop_aggregate_limit_pct",
    "single_limit_pct": "nop_single_limit_pct",
    "sum_long": "sum_long_ghs",
    "sum_long_ghs": "sum_long_ghs",
    "sum_short": "sum_short_ghs",
    "sum_short_ghs": "sum_short_ghs",
}

#: The FX resolvers are classified ``ENGINE_RUN`` at RESOLVER granularity, but
#: they serve two populations. The book-level measures above read the run's own
#: metrics and are proved here. The per-currency and contract measures read the
#: fx_position facts the run consumed, the canonical contract book, or market
#: spot — the run is a preferred source, not the sole one — and their row-level
#: correctness is proved by ``bog_forms/test_bsd13.py`` and
#: ``bog_forms/test_bsd1b.py``. Declaring the split is the point: a measure in
#: neither set fails ``test_every_bound_fx_measure_is_classified`` rather than
#: passing through this gate unproved.
_FX_ROW_MEASURES: frozenset[str] = frozenset(
    {
        "net",
        "net_ghs",
        "net_ghs_thousands",
        "net_assets",
        "net_trading",
        "net_derivatives",
        "assets",
        "liabilities",
        "spot",
        "spot_long",
        "spot_short",
        "forward_long",
        "forward_short",
        "long",
        "short",
        "pct_nof",
    }
)


def _period_id(db: Session) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


def _seed_with_engine_runs(db: Session) -> None:
    """The book plus the capital and FX baseline runs the BSD forms consume."""
    materialize_canonical_test_book(db)
    period_id = _period_id(db)
    capital = regulatory_capital.create_capital_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert capital.status == "succeeded", capital.status
    batch = regulatory_fx.run_all_fx_scenarios(
        db, MAKER, SAMPLE_BANK_ID, FxScenarioBatchCreate(reporting_period_id=period_id)
    )
    assert any(run.status == "succeeded" for run in batch.runs), [r.status for r in batch.runs]


def _run(db: Session, module: str) -> RegulatoryRun:
    run = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == DEMO_ORG_ID,
            RegulatoryRun.bank_id == SAMPLE_BANK_ID,
            RegulatoryRun.reporting_period_id == _period_id(db),
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == "baseline",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc())
    ).first()
    assert run is not None, f"no succeeded {module} run"
    return run


def _generate(db: Session, code: str) -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code=code, reporting_date=REPORTING_DATE),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _engine_backed_lines() -> list[tuple[str, str, str, str, dict[str, object], bool]]:
    """(form, sheet, cell, resolver, params, unscaled) for every overlap point."""
    found: list[tuple[str, str, str, str, dict[str, object], bool]] = []
    for spec in all_form_specs():
        for sheet in spec.sheets:
            for line in sheet.lines:
                if line.source not in ENGINE_BACKED_RESOLVERS:
                    continue
                for ref in line.cells.values():
                    found.append(
                        (spec.code, sheet.name, ref, line.source, dict(line.params), line.unscaled)
                    )
    return found


def _snapshot_cell(package: RegulatoryPackage, sheet: str, ref: str) -> object:
    return (package.snapshot["bog_form"]["cells"].get(sheet) or {}).get(ref)


def _expected_run_line(db: Session, params: dict[str, object]) -> Decimal | None:
    run = _run(db, "capital")
    field = str(params.get("field", "exposure_amount"))
    items = db.scalars(
        select(RegulatoryLineItem).where(
            RegulatoryLineItem.run_id == run.id,
            RegulatoryLineItem.section == str(params["section"]),
        )
    ).all()
    for item in items:
        if item.line_code == str(params["line_code"]):
            raw = getattr(item, field)
            return None if raw is None else Decimal(str(raw))
    return None


def _expected_avg_gross_income(db: Session, params: dict[str, object]) -> Decimal | None:
    run = _run(db, "capital")
    # ``params`` is the declarative spec table's untyped row, so both reads are
    # coerced through the same str() the neighbouring lines use.
    years = int(str(params.get("years", 3)))
    prefix = str(params.get("prefix", "gross_income"))
    items = db.scalars(
        select(RegulatoryLineItem).where(
            RegulatoryLineItem.run_id == run.id,
            RegulatoryLineItem.section == "operational_rwa",
        )
    ).all()
    dated: list[tuple[int, Decimal]] = []
    for item in items:
        if not item.line_code.startswith(prefix) or item.exposure_amount is None:
            continue
        suffix = item.line_code.rsplit("_", 1)[-1]
        dated.append(
            (int(suffix) if suffix.isdigit() else item.position, Decimal(str(item.exposure_amount)))
        )
    if not dated:
        return None
    latest = sorted(dated)[-years:]
    return sum((amount for _, amount in latest), Decimal(0)) / Decimal(len(latest))


def _expected_run_metric(db: Session, params: dict[str, object]) -> Decimal | None:
    run = _run(db, str(params["module"]))
    raw = (run.metrics or {}).get(str(params["metric"]))
    if raw is None:
        return None
    return Decimal(str(raw)) * Decimal(str(params.get("scale", 1)))


#: Sentinel: this binding is out of THIS gate's scope (see _FX_ROW_MEASURES).
_UNCOVERED = object()


def _expected_fx_aggregate(db: Session, params: dict[str, object]) -> object:
    measure = str(params.get("measure", ""))
    key = _FX_AGGREGATES.get(measure)
    if key is None:
        return _UNCOVERED
    run = _run(db, "fx")
    raw = (run.metrics or {}).get(key)
    return None if raw is None else Decimal(str(raw))


def test_every_bound_fx_measure_is_classified() -> None:
    """No FX binding may sit outside both the proved set and the declared set."""
    bound: set[str] = set()
    for _code, _sheet, _ref, resolver, params, _unscaled in _engine_backed_lines():
        if resolver in ("bsd13.nop", "bsd1b.nop"):
            bound.add(str(params.get("measure", "net")))
    unclassified = sorted(bound - set(_FX_AGGREGATES) - _FX_ROW_MEASURES)
    assert not unclassified, (
        f"FX measures bound by a line map but classified neither as a proved book-level "
        f"figure nor as a declared row-level measure: {unclassified}"
    )


def test_the_overlap_population_is_non_empty_and_fully_handled() -> None:
    """A resolver may not join the overlap set without an equivalence handler.

    This is what keeps the audit's gap closed: adding an engine-backed resolver
    fails here until its figure is proved against the run it comes from.
    """
    handled = {
        "bsd5.run_line",
        "bsd5.avg_gross_income",
        "run.metric",
        "bsd13.nop",
        "bsd1b.nop",
    }
    assert handled >= ENGINE_BACKED_RESOLVERS, sorted(ENGINE_BACKED_RESOLVERS - handled)
    lines = _engine_backed_lines()
    assert lines, "no BoG form binds an engine run — the overlap set cannot be empty"


@pytest.mark.parametrize(
    "form_code",
    sorted({form for form, *_ in _engine_backed_lines()}),
)
def test_every_engine_backed_cell_equals_its_source_run(
    db_session: Session, form_code: str
) -> None:
    """The equivalence proof, per form, over the real package pipeline."""
    _seed_with_engine_runs(db_session)
    package = _generate(db_session, form_code)
    spec = next(s for s in all_form_specs() if s.code == form_code)

    checked = 0
    for code, sheet, ref, resolver, params, _unscaled in _engine_backed_lines():
        if code != form_code:
            continue
        expected = {
            "bsd5.run_line": _expected_run_line,
            "bsd5.avg_gross_income": _expected_avg_gross_income,
            "run.metric": _expected_run_metric,
            "bsd13.nop": _expected_fx_aggregate,
            "bsd1b.nop": _expected_fx_aggregate,
        }[resolver](db_session, params)
        if expected is _UNCOVERED:
            continue
        actual = _snapshot_cell(package, sheet, ref)
        if expected is None:
            # The engine produced no such figure, so the official cell must be
            # BLANK. A number here would be fabricated provenance — the failure
            # mode that matters most.
            assert actual is None, (
                f"{form_code}/{sheet}!{ref} ({resolver}) carries {actual!r} but the "
                "source run holds no such figure"
            )
            continue
        assert actual is not None, (
            f"{form_code}/{sheet}!{ref} ({resolver}) is blank but the source run holds {expected}"
        )
        sheet_spec = spec.sheet(sheet)
        divisor = Decimal(UNIT_DIVISOR[sheet_spec.unit]) if sheet_spec is not None else Decimal(1)
        # The bog_form snapshot's ``cells`` payload is in BASE units; only the
        # section rows are scaled for display. Compare in base units.
        assert isinstance(expected, Decimal)
        assert abs(Decimal(str(actual)) - expected) <= _TOLERANCE * max(divisor, Decimal(1)), (
            f"{form_code}/{sheet}!{ref} ({resolver}) = {actual} but the source run holds "
            f"{expected}. This is a template/engine divergence, not a rounding difference — "
            "do not widen the tolerance."
        )
        checked += 1
    # Every parametrised form must actually have proved something, or the gate
    # is decorative for that form.
    assert checked > 0, f"{form_code}: no engine-backed cell was proved"


def test_the_bsd5a_car_inequality_is_left_alone() -> None:
    """A guard on this suite, not on BSD5A.

    ``bog_forms/test_bsd5.py`` pins that BoG's E70 does NOT equal the capital
    engine's ``car_pct`` — "by construction, not by accident". If someone ever
    tries to make the two agree, they must change that test, not this one; this
    assertion exists so nobody mistakes the equivalence gate above for a licence
    to equate declared alternate methodologies.
    """
    from pathlib import Path  # noqa: PLC0415

    pinned = Path(__file__).resolve().parent / "bog_forms" / "test_bsd5.py"
    text = pinned.read_text()
    assert "by construction, not by accident" in text
    assert "not _close(" in text
