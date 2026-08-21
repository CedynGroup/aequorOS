"""Tenant overrides may only tighten a regulatory floor (QA audit 2026-08-20 P1-5).

A per-tenant board register (``ParamCapitalThreshold`` …) must never be able to
WEAKEN a control-plane regulatory floor/limit. ``regulatory_parameters.tighten``
is the shared unit-aware guard; the capital engine applies it so a board CAR
minimum below the regulatory floor (Ghana 13% = 10% + 3% CCB) is clamped up.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, ParamCapitalThreshold
from app.services import regulatory_capital
from app.services import regulatory_parameters as rp
from tests.api.helpers import ORG_1, USER_1

AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def test_tighten_floor_takes_the_higher_value() -> None:
    # A board minimum below the regulatory floor is raised to the floor …
    assert rp.tighten("car_min", Decimal("10"), Decimal("13")) == Decimal("13")
    # … a stricter board minimum is honoured.
    assert rp.tighten("car_min", Decimal("15"), Decimal("13")) == Decimal("15")


def test_tighten_ceiling_takes_the_lower_value() -> None:
    # A board exposure limit above the regulatory ceiling is capped to it …
    assert rp.tighten("large_exposure_limit_pct", Decimal("25"), Decimal("20")) == Decimal("20")
    # … a stricter (lower) board limit is honoured.
    assert rp.tighten("large_exposure_limit_pct", Decimal("15"), Decimal("20")) == Decimal("15")


def test_undirected_code_returns_the_tenant_value() -> None:
    # A code with no declared direction is unconstrained (used as-is).
    assert rp.tighten("car_early_warning", Decimal("5"), Decimal("13")) == Decimal("5")


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Tightening Test Bank",
        short_name="TTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _set_board_car_min(db: Session, value: str) -> None:
    # ParamCapitalThreshold is org + jurisdiction scoped (not bank-scoped).
    db.add(
        ParamCapitalThreshold(
            organization_id=ORG_1,
            jurisdiction_code="GH",
            threshold_code="car_min",
            value_pct=Decimal(value),
            effective_from=date(2020, 1, 1),
            approved_by="test",
            approval_timestamp=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    db.flush()


def test_capital_engine_clamps_a_weak_board_car_floor_up_to_the_regulatory_minimum(
    db_session: Session,
) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    _set_board_car_min(db_session, "10")  # below the 13% BoG floor
    active = regulatory_capital._load_active_params(db_session, CTX, bank, AS_OF)
    # The effective CAR floor the engine enforces is the regulatory 13%, not the
    # weaker board 10% — the board register could not weaken it.
    assert active.thresholds["car_min"] == Decimal("13")


def test_capital_engine_honours_a_board_car_floor_stricter_than_the_minimum(
    db_session: Session,
) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    _set_board_car_min(db_session, "16")  # stricter than the 13% floor
    active = regulatory_capital._load_active_params(db_session, CTX, bank, AS_OF)
    assert active.thresholds["car_min"] == Decimal("16")
