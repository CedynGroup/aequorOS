"""The tenant board register: seeded at onboarding, regime-scoped, governed.

Founder review 2026-08-23. ``provision_tenant`` created storage, KMS, an SSO
stub and a first admin, then certified the tenant "empty-but-wired … goes live
on its first ingestion" — without ever asking whether the calculation engines
had anything to compute WITH. A tenant could therefore pass provisioning,
ingest 490k canonical position rows, derive a balancing set of facts, and still
produce zero successful runs, so nothing could be published and no return could
be generated.

These tests pin the seeding step, its regime split, and the readiness gate that
now refuses to certify a tenant whose board register is empty.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    Organization,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    RegulatoryParameter,
)
from app.services import parameter_register, regulatory_parameters
from tests.fixtures import canonical_bank_fixture as fixture

ORG = "OR-REGTEST1"
APPROVER = "tenant_provisioning:ops@example.com"
APPROVED_AT = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
SECOND_ORG = "OR-REGTEST2"


@pytest.fixture(autouse=True)
def _organizations(db_session: Session) -> None:
    """The register FKs to ``organizations``; these tests own bare tenants so the
    seeding is isolated from any fixture book."""
    for org_id in (ORG, SECOND_ORG):
        db_session.add(Organization(id=org_id, name=f"Register test {org_id}"))
    db_session.flush()


#: One licence per class; the register reads the class through the policy chain.
_LICENCE = {"bank": "universal_bank", "sdi": "savings_and_loans"}


def _bank(db: Session, institution_class: str, org: str) -> Bank:
    bank = db.scalar(select(Bank).where(Bank.organization_id == org))
    if bank is None:
        bank = Bank(
            organization_id=org,
            name=f"Register test bank {org}",
            short_name=f"Register {org}",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type=_LICENCE[institution_class],
        )
        db.add(bank)
        db.flush()
    return bank


def _seed(db: Session, institution_class: str, *, org: str = ORG, **kwargs: object):
    return parameter_register.seed_tenant_register(
        db,
        bank=_bank(db, institution_class, org),
        approved_by=APPROVER,
        approved_at=APPROVED_AT,
        **kwargs,  # type: ignore[arg-type]
    )


def _count(db: Session, model: type, org: str = ORG) -> int:
    return (
        db.scalar(select(func.count()).select_from(model).where(model.organization_id == org)) or 0
    )


def test_a_bank_gets_the_full_crd_register(db_session: Session) -> None:
    result = _seed(db_session, "bank")
    assert result.total_created > 0
    assert _count(db_session, ParamCapitalThreshold) > 0
    assert _count(db_session, ParamRiskWeight) > 0
    assert _count(db_session, ParamLcrRunoffRate) > 0
    assert _count(db_session, ParamNsfrWeight) > 0
    assert _count(db_session, ParamStressShock) > 0

    codes = set(
        db_session.scalars(
            select(ParamCapitalThreshold.threshold_code).where(
                ParamCapitalThreshold.organization_id == ORG
            )
        ).all()
    )
    # The four the liquidity engine refuses without, and the two IRRBB limits.
    assert {"lcr_min", "lcr_amber_floor", "nsfr_min", "lcr_inflow_cap_pct"} <= codes
    assert {"eve_tier1_limit_pct", "irr_nii_limit_pct"} <= codes


def test_an_sdi_is_never_given_basel_liquidity_floors(db_session: Session) -> None:
    """The point of the regime split.

    BoG imposes no LCR or NSFR on an SDI, so seeding one would assert a
    supervisory requirement that does not exist. The SDI's liquidity is the LMTD
    Table 1 view, and its risk weights come from the control plane — not the CRD
    exposure-class ladder.
    """
    _seed(db_session, "sdi")
    codes = set(
        db_session.scalars(
            select(ParamCapitalThreshold.threshold_code).where(
                ParamCapitalThreshold.organization_id == ORG
            )
        ).all()
    )
    assert not (codes & {"lcr_min", "lcr_amber_floor", "nsfr_min", "lcr_inflow_cap_pct"})
    assert not (codes & {"bia_alpha_pct", "fx_charge_pct"})
    assert _count(db_session, ParamRiskWeight) == 0, "s.29 reads control-plane bucket weights"
    assert _count(db_session, ParamLcrRunoffRate) == 0
    assert _count(db_session, ParamNsfrWeight) == 0


def test_an_sdi_still_gets_its_irrbb_board_limits(db_session: Session) -> None:
    """An SDI keeps IRRBB (``institution_types.SDI_MODULES``), and the IRR run
    refuses without the board's EVE and NII limits."""
    _seed(db_session, "sdi")
    codes = set(
        db_session.scalars(
            select(ParamCapitalThreshold.threshold_code).where(
                ParamCapitalThreshold.organization_id == ORG
            )
        ).all()
    )
    assert codes == {"eve_tier1_limit_pct", "irr_nii_limit_pct"}
    shocks = db_session.scalars(
        select(ParamStressShock.scenario_code).where(ParamStressShock.organization_id == ORG)
    ).all()
    assert "parallel_up_200" in set(shocks)


def test_every_row_records_who_approved_it(db_session: Session) -> None:
    """These tables are maker-checker governed by schema (``approved_by`` is NOT
    NULL). A seeded starting position names the operator who stood it up."""
    _seed(db_session, "bank")
    for model in parameter_register.PARAMETER_MODELS:
        rows = db_session.scalars(select(model).where(model.organization_id == ORG)).all()
        for row in rows:
            assert row.approved_by == APPROVER
            assert row.approval_timestamp is not None


def test_seeding_is_idempotent_and_never_overwrites_a_board_revision(
    db_session: Session,
) -> None:
    """Re-running must not clobber limits a board has since revised."""
    _seed(db_session, "bank")
    before = _count(db_session, ParamCapitalThreshold)

    revised = db_session.scalar(
        select(ParamCapitalThreshold).where(
            ParamCapitalThreshold.organization_id == ORG,
            ParamCapitalThreshold.threshold_code == "irr_nii_limit_pct",
        )
    )
    assert revised is not None
    revised.value_pct = Decimal("7")
    db_session.flush()

    second = _seed(db_session, "bank")
    assert second.total_created == 0
    assert second.skipped_existing, "the second pass reports what it left alone"
    assert _count(db_session, ParamCapitalThreshold) == before

    still = db_session.scalar(
        select(ParamCapitalThreshold.value_pct).where(
            ParamCapitalThreshold.organization_id == ORG,
            ParamCapitalThreshold.threshold_code == "irr_nii_limit_pct",
        )
    )
    assert still == 7, "the board's own revision survives a re-seed"


def test_the_base_curve_is_supplied_not_invented(db_session: Session) -> None:
    """A zero-coupon curve is market data, not board policy.

    Absent one, IRRBB stays unresolved and refuses — which is correct, because
    an invented curve would price the entire banking book.
    """
    _seed(db_session, "bank")
    base_rows = db_session.scalars(
        select(ParamStressShock).where(
            ParamStressShock.organization_id == ORG,
            ParamStressShock.scenario_code == "base_curve",
        )
    ).all()
    assert base_rows == []

    other = SECOND_ORG
    _seed(db_session, "bank", org=other, base_curve={"1.9y": "27.8", "4.0y": "28.9"})
    supplied = db_session.scalars(
        select(ParamStressShock.shock_key).where(
            ParamStressShock.organization_id == other,
            ParamStressShock.scenario_code == "base_curve",
        )
    ).all()
    assert set(supplied) == {"1.9y", "4.0y"}


def test_register_row_count_is_the_readiness_signal(db_session: Session) -> None:
    assert parameter_register.register_row_count(db_session, ORG) == 0
    _seed(db_session, "sdi")
    assert parameter_register.register_row_count(db_session, ORG) > 0


def test_the_catalogue_is_defined_exactly_once() -> None:
    """One definition of the tenant parameter catalogue, not two.

    It was copied into ``tests/fixtures/canonical_bank_fixture.py`` when the
    provisioning register was written (2026-08-23) — 58 codes duplicated with
    identical values, which is the quietest kind of divergence: the hermetic
    book and what tenant provisioning actually writes drift apart and every test
    still passes. The codebase already has the convention that prevents this
    (``institution_types.seed_rows``, ``regulatory_parameters.seed_rows``); this
    pins that the fixture IMPORTS rather than restates.
    """
    pairs = (
        (fixture._CAPITAL_THRESHOLDS, parameter_register.BANK_CAPITAL_THRESHOLDS),
        (fixture._FX_CAPITAL_THRESHOLDS, parameter_register.BANK_FX_THRESHOLDS),
        (fixture._FTP_CAPITAL_THRESHOLDS, parameter_register.BANK_FTP_THRESHOLDS),
        (fixture._RISK_WEIGHTS, parameter_register.BANK_RISK_WEIGHTS),
        (fixture._LCR_OUTFLOW_RATES, parameter_register.BANK_LCR_OUTFLOWS),
        (fixture._LCR_INFLOW_RATES, parameter_register.BANK_LCR_INFLOWS),
        (fixture._NSFR_ASF_WEIGHTS, parameter_register.BANK_NSFR_ASF),
        (fixture._NSFR_RSF_WEIGHTS, parameter_register.BANK_NSFR_RSF),
    )
    for from_fixture, from_app in pairs:
        # `is`, not `==`: equal-but-separate dicts are exactly the duplication
        # this test exists to forbid.
        assert from_fixture is from_app, (
            "the fixture holds its own copy of a catalogue the application "
            "defines — import it from services/parameter_register.py instead"
        )


def test_the_fixture_does_not_redefine_any_catalogue_literal() -> None:
    """Belt and braces: no literal catalogue dict may reappear in the fixture.

    The identity check above passes if someone re-binds the alias; this catches
    a fresh literal being pasted back in.
    """
    source = inspect.getsource(fixture)
    for name in (
        "_CAPITAL_THRESHOLDS",
        "_FX_CAPITAL_THRESHOLDS",
        "_FTP_CAPITAL_THRESHOLDS",
        "_RISK_WEIGHTS",
        "_LCR_OUTFLOW_RATES",
        "_LCR_INFLOW_RATES",
        "_NSFR_ASF_WEIGHTS",
        "_NSFR_RSF_WEIGHTS",
    ):
        literal = re.search(rf"^{name}(: dict\[str, str\])? = \{{", source, re.M)
        assert literal is None, (
            f"{name} is defined as a literal in the fixture; it must alias "
            "services/parameter_register.py"
        )


# --- D-024 / D-042: governed capital minima are never seeded -----------------------

_GOVERNED_BANK_CODES = ("car_min", "cet1_min", "tier1_min", "leverage_min")


def _register(db: Session, org: str = ORG) -> dict[str, Decimal]:
    return {
        row.threshold_code: Decimal(str(row.value_pct))
        for row in db.scalars(
            select(ParamCapitalThreshold).where(ParamCapitalThreshold.organization_id == org)
        ).all()
    }


def test_governed_register_codes_carry_no_literal_default() -> None:
    """Founder directive D-024: no second copy of a governed number."""
    assert parameter_register.CONTROL_PLANE_REGISTER_CODES["bank"] == _GOVERNED_BANK_CODES
    assert parameter_register.CONTROL_PLANE_REGISTER_CODES["sdi"] == ()
    for catalogue in (
        parameter_register.BANK_CAPITAL_THRESHOLDS,
        parameter_register.BANK_FX_THRESHOLDS,
        parameter_register.BANK_FTP_THRESHOLDS,
    ):
        assert not set(_GOVERNED_BANK_CODES) & set(catalogue)


def test_a_bank_register_carries_no_governed_minimum_and_the_clamp_supplies_it(
    db_session: Session,
) -> None:
    """D-042: nothing governed is written; the tighten-only clamp supplies the
    control-plane value for every code the register has no row for."""
    _seed(db_session, "bank")
    register = _register(db_session)
    assert not set(_GOVERNED_BANK_CODES) & set(register)
    bank = _bank(db_session, "bank", ORG)
    effective = regulatory_parameters.clamp_overrides(
        db_session, bank, register, as_of=APPROVED_AT.date()
    ).values
    for code in _GOVERNED_BANK_CODES:
        governed = regulatory_parameters.try_resolve(
            db_session, bank, code, as_of=APPROVED_AT.date()
        )
        assert governed is not None
        assert effective[code] == governed.normalized_value, code


def test_seeding_never_depends_on_a_governed_value(db_session: Session) -> None:
    """D-042: a jurisdiction or licence with no governed capital minima still gets
    its board register; the missing value refuses at calculation time instead."""
    for row in db_session.scalars(
        select(RegulatoryParameter).where(RegulatoryParameter.param_code.in_(_GOVERNED_BANK_CODES))
    ).all():
        db_session.delete(row)
    db_session.flush()
    result = _seed(db_session, "bank")
    assert result.total_created > 0
    bank = _bank(db_session, "bank", ORG)
    effective = regulatory_parameters.clamp_overrides(
        db_session, bank, _register(db_session), as_of=APPROVED_AT.date()
    ).values
    assert not set(_GOVERNED_BANK_CODES) & set(effective), "nothing is invented"


def test_an_sdi_register_resolves_no_governed_capital_code(db_session: Session) -> None:
    """The s.29 floor is read from the control plane at calculation time; the SDI
    register never carries a copy of it."""
    _seed(db_session, "sdi")
    assert not set(_GOVERNED_BANK_CODES) & set(_register(db_session))
