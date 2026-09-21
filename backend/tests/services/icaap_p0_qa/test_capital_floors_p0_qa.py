"""Independent QA of ICAAP P0 item B2 — the CRD capital floors (D-008).

Agent 11 (Test/QA), 2026-09-19. Checks the governed seeds in the hermetic
reference data, the tighten-only clamp for banks (leverage 3 -> 6; CET1 /
Tier 1 unchanged at 6.5 / 8 — no conservation buffer, M20 left open), that
SDIs are untouched by the bank-only seeds, that a real capital run applies the
floors, and the migration's data logic (idempotence, scoped downgrade) run
through alembic's own ``Operations`` against the hermetic database.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.policy.resolver import PARAMETER_DIRECTION
from app.models import (
    Bank,
    BankReportingPeriod,
    ParamCapitalThreshold,
    RegulatoryMetricResult,
    RegulatoryParameter,
    RegulatoryRun,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital, regulatory_parameters
from app.services.parameter_register import BANK_CAPITAL_THRESHOLDS
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
    set_board_threshold,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
AS_OF = date(2026, 3, 31)

B2 = {
    "cet1_min": (Decimal("6.5"), "floor"),
    "tier1_min": (Decimal("8"), "floor"),
    "leverage_min": (Decimal("6"), "floor"),
    "ccb1_pct": (Decimal("3"), "floor"),
    "ccyb_pct": (Decimal("0"), "floor"),
    "dsib_buffer_pct": (Decimal("0"), "floor"),
    "at1_cap_pct_rwa": (Decimal("1.5"), "ceiling"),
    "tier2_cap_pct_rwa": (Decimal("2"), "ceiling"),
}

MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "202609190054_crd_capital_floor_parameters.py"
)


def _bank(db: Session) -> Bank:
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank


def _b2_rows(db: Session) -> list[RegulatoryParameter]:
    return list(
        db.scalars(
            select(RegulatoryParameter).where(RegulatoryParameter.param_code.in_(tuple(B2)))
        ).all()
    )


# --- seeds + directions --------------------------------------------------------


#: D-042: the SDI class carries the two recognition caps only, at the values the
#: platform applied before 2026-09-19, pending (no SDI-specific basis identified).
SDI_CAPS = {"at1_cap_pct_rwa": Decimal("1.5"), "tier2_cap_pct_rwa": Decimal("2")}


def test_hermetic_reference_data_carries_every_b2_row_for_banks_only(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    rows = _b2_rows(db_session)
    sdi_rows = [row for row in rows if row.scope_key == "sdi"]
    rows = [row for row in rows if row.scope_key != "sdi"]
    by_code = {row.param_code: row for row in rows}
    assert sorted(by_code) == sorted(B2)
    assert len(rows) == len(B2), "exactly one row per code"
    assert {row.param_code: Decimal(str(row.value_numeric)) for row in sdi_rows} == SDI_CAPS
    assert len(sdi_rows) == len(SDI_CAPS)
    for row in sdi_rows:
        assert row.confirmation_status == "pending"
        assert "no SDI-specific regulatory basis identified" in row.source_citation
    for code, (value, _direction) in B2.items():
        row = by_code[code]
        assert (row.scope_type, row.scope_key) == ("institution_class", "bank"), code
        assert row.jurisdiction_code == "GH"
        assert Decimal(str(row.value_numeric)) == value, code
        assert row.unit == "percent"
        assert row.status == "approved"
        # D-024 (founder, 2026-09-19): CET1 / Tier 1 await stakeholder
        # confirmation of the conservation-buffer treatment (M20).
        pending = code in {"cet1_min", "tier1_min"}
        assert row.confirmation_status == ("pending" if pending else "confirmed"), code
        assert row.effective_to is None
        assert "Capital Requirements Directive 2018" in row.source_citation
    # The pre-existing car_min generations are untouched (bank 13, SDI 10).
    car = {
        row.scope_key: Decimal(str(row.value_numeric))
        for row in db_session.scalars(
            select(RegulatoryParameter).where(RegulatoryParameter.param_code == "car_min")
        ).all()
    }
    assert car == {"bank": Decimal("13"), "sdi": Decimal("10")}


def test_b2_directions_are_declared() -> None:
    for code, (_value, direction) in B2.items():
        assert PARAMETER_DIRECTION[code] == direction, code


def test_register_default_leverage_is_six_and_tiers_unchanged(db_session: Session) -> None:
    """D-024 / D-042: a governed minimum has no literal default and is not seeded
    into the register at all; the clamp supplies the control-plane value, so the
    effective leverage floor is 6 and the tiers are the unchanged 6.5 / 8."""
    for code in ("car_min", "cet1_min", "tier1_min", "leverage_min"):
        assert code not in BANK_CAPITAL_THRESHOLDS, code
    materialize_canonical_test_book(db_session)
    register = {
        row.threshold_code: Decimal(str(row.value_pct))
        for row in db_session.scalars(
            select(ParamCapitalThreshold).where(
                ParamCapitalThreshold.organization_id == DEMO_ORG_ID
            )
        ).all()
    }
    assert not {"car_min", "cet1_min", "tier1_min", "leverage_min"} & set(register)
    effective = regulatory_parameters.clamp_overrides(
        db_session, _bank(db_session), register, as_of=AS_OF
    ).values
    assert effective["leverage_min"] == Decimal("6")
    assert effective["cet1_min"] == Decimal("6.5")
    assert effective["tier1_min"] == Decimal("8")
    assert effective["car_min"] == Decimal("13")


# --- the clamp -------------------------------------------------------------------


def test_bank_clamp_raises_leverage_3_to_6_and_holds_cet1_tier1_without_ccb(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    report = regulatory_parameters.clamp_overrides(
        db_session,
        bank,
        {
            "leverage_min": Decimal("3"),
            "cet1_min": Decimal("6.5"),
            "tier1_min": Decimal("8"),
            "car_min": Decimal("10"),
        },
        as_of=AS_OF,
    )
    values = report.values
    assert values["leverage_min"] == Decimal("6")
    # D-008: no conservation buffer folded into the CET1 / Tier 1 floors.
    assert values["cet1_min"] == Decimal("6.5")
    assert values["tier1_min"] == Decimal("8")
    assert values["car_min"] == Decimal("13")
    assert set(report.codes_clamped()) == {"leverage_min", "car_min"}


def test_bank_clamp_on_weaker_tiers_buffers_and_looser_caps(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    values = regulatory_parameters.clamp_overrides(
        db_session,
        _bank(db_session),
        {
            "cet1_min": Decimal("5"),
            "tier1_min": Decimal("7"),
            "ccb1_pct": Decimal("2.5"),
            "ccyb_pct": Decimal("1"),
            "at1_cap_pct_rwa": Decimal("2"),
            "tier2_cap_pct_rwa": Decimal("1"),
        },
        as_of=AS_OF,
    ).values
    assert values["cet1_min"] == Decimal("6.5")
    assert values["tier1_min"] == Decimal("8")
    assert values["ccb1_pct"] == Decimal("3")
    assert values["ccyb_pct"] == Decimal("1")  # stricter board buffer stands
    assert values["at1_cap_pct_rwa"] == Decimal("1.5")  # ceiling: looser cap cut
    assert values["tier2_cap_pct_rwa"] == Decimal("1")  # tighter cap stands


def test_sdi_is_unaffected_by_the_bank_only_seeds(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    bank.institution_type = "savings_and_loans"
    db_session.flush()
    for code in B2:
        resolved = regulatory_parameters.try_resolve(db_session, bank, code, as_of=AS_OF)
        if code in SDI_CAPS:  # D-042: the pre-P0 caps, carried for SDIs
            assert resolved is not None and resolved.decimal == SDI_CAPS[code], code
            assert resolved.is_pending, code
        else:
            assert resolved is None, code
    values = regulatory_parameters.clamp_overrides(
        db_session,
        bank,
        {"leverage_min": Decimal("3"), "cet1_min": Decimal("5"), "car_min": Decimal("8")},
        as_of=AS_OF,
    ).values
    assert values["leverage_min"] == Decimal("3")
    assert values["cet1_min"] == Decimal("5")
    assert values["car_min"] == Decimal("10")


def test_seeds_do_not_apply_before_their_effective_date(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    values = regulatory_parameters.clamp_overrides(
        db_session,
        _bank(db_session),
        {"leverage_min": Decimal("3")},
        as_of=date(2019, 12, 31),
    ).values
    assert values["leverage_min"] == Decimal("3")


def test_a_capital_run_applies_the_governed_leverage_floor_over_a_board_3(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    # D-042: the register carries no governed minimum of its own; the board's 3
    # is stated explicitly.
    set_board_threshold(db_session, "leverage_min", "3")
    db_session.commit()
    rows = db_session.scalars(
        select(ParamCapitalThreshold).where(
            ParamCapitalThreshold.organization_id == DEMO_ORG_ID,
            ParamCapitalThreshold.threshold_code == "leverage_min",
        )
    ).all()
    assert [Decimal(str(row.value_pct)) for row in rows] == [Decimal("3")]
    period_id = db_session.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period_id is not None
    run = regulatory_capital.create_capital_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded", run
    stored = db_session.get(RegulatoryRun, run.id)
    assert stored is not None
    thresholds = {
        row.metric_code: row.threshold_min
        for row in db_session.scalars(
            select(RegulatoryMetricResult).where(RegulatoryMetricResult.run_id == run.id)
        ).all()
    }
    assert Decimal(str(thresholds["leverage_ratio_pct"])) == Decimal("6")
    assert Decimal(str(thresholds["cet1_ratio_pct"])) == Decimal("6.5")
    assert Decimal(str(thresholds["tier1_ratio_pct"])) == Decimal("8")
    assert Decimal(str(thresholds["car_pct"])) == Decimal("13")


# --- migration data logic through alembic Operations ------------------------------


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qa_mig_202609190054", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _ops(db: Session) -> Iterator[None]:
    connection = db.connection()
    context = MigrationContext.configure(connection=connection)
    with Operations.context(context):
        yield


def _all_parameter_rows(db: Session) -> set[tuple[str, str, str, str, str]]:
    return {
        (row.scope_type, row.scope_key, row.param_code, row.jurisdiction_code, row.proposed_by)
        for row in db.scalars(select(RegulatoryParameter)).all()
    }


def test_migration_downgrade_removes_only_its_seed_and_upgrade_is_idempotent(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    migration = _migration()
    assert migration.revision == "202609190054"
    assert migration.down_revision == "202609160053"
    before = _all_parameter_rows(db_session)
    others = {row for row in before if row[2] not in B2}
    assert others, "reference data seeds other codes"

    with _ops(db_session):
        migration.downgrade()
    db_session.expire_all()
    after_down = _all_parameter_rows(db_session)
    assert after_down == others, "downgrade removes the B2 seed rows and nothing else"

    with _ops(db_session):
        migration.upgrade()
    db_session.expire_all()
    assert _all_parameter_rows(db_session) == before

    # Second upgrade: no duplicates.
    with _ops(db_session):
        migration.upgrade()
    db_session.expire_all()
    assert len(_b2_rows(db_session)) == len(B2) + len(SDI_CAPS)


def test_migration_downgrade_keeps_an_operator_generation(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    migration = _migration()
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="leverage_min",
            jurisdiction_code="GH",
            value_numeric=Decimal("7"),
            value_json=None,
            unit="percent",
            source_citation="QA operator generation",
            confirmation_status="confirmed",
            effective_from=date(2027, 1, 1),
            effective_to=None,
            status="approved",
            proposed_by="operator:qa-maker",
            approved_by="operator:qa-checker",
            approved_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    db_session.flush()
    with _ops(db_session):
        migration.downgrade()
    db_session.expire_all()
    remaining = _b2_rows(db_session)
    assert [(row.param_code, row.proposed_by) for row in remaining] == [
        ("leverage_min", "operator:qa-maker")
    ]


def test_a_draft_operator_row_does_not_block_the_governed_seed(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    migration = _migration()
    with _ops(db_session):
        migration.downgrade()
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="leverage_min",
            jurisdiction_code="GH",
            value_numeric=Decimal("5"),
            value_json=None,
            unit="percent",
            source_citation="QA draft",
            confirmation_status="pending",
            effective_from=date(2020, 1, 1),
            effective_to=None,
            status="draft",
            proposed_by="operator:qa-maker",
            approved_by=None,
        )
    )
    db_session.flush()
    with _ops(db_session):
        migration.upgrade()
    db_session.expire_all()
    resolved = regulatory_parameters.try_resolve(
        db_session, _bank(db_session), "leverage_min", as_of=AS_OF
    )
    assert resolved is not None and resolved.decimal == Decimal("6")
