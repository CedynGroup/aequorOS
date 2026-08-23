"""The Policy Resolver (P2) — the database-bound half.

Pins the service adapter over ``app/domain/policy``:

- ``policy_scope`` assembles the whole chain from a ``Bank`` and fails closed at
  every link (P0-12 for the licence class, §6 for the jurisdiction);
- the control-plane resolver resolves through the SAME chain (licence beats
  class, jurisdiction isolates);
- ``clamp_overrides`` applies tighten-only enforcement across a whole tenant
  register, not one hand-picked code.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.domain.policy import PolicyUnresolvedError
from app.models import Bank, Jurisdiction, RegulatoryParameter
from app.services import institution_types, jurisdictions
from app.services import regulatory_parameters as rp
from tests.api.helpers import ORG_1

AS_OF = date(2026, 6, 30)


def _register_nigeria(db: Session) -> None:
    """The hermetic registry seeds Ghana only; a second jurisdiction is what makes
    the isolation assertions meaningful."""
    if db.get(Jurisdiction, "NG") is None:
        db.add(
            Jurisdiction(
                code="NG",
                country_name="Nigeria",
                currency_code="NGN",
                currency_name="Naira",
                locale="en-NG",
                central_bank_name="Central Bank of Nigeria",
                regulator_short="CBN",
            )
        )
        db.flush()


def _bank(
    db: Session,
    *,
    institution_type: str = "universal_bank",
    jurisdiction_code: str = "GH",
    currency: str = "GHS",
    commit: bool = True,
) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Policy Chain Bank",
        short_name="PCB",
        currency=currency,
        jurisdiction_code=jurisdiction_code,
        license_type="universal",
        institution_type=institution_type,
    )
    if commit:
        db.add(bank)
        db.flush()
    return bank


# --------------------------------------------------------------------------
# The chain assembles, or it fails
# --------------------------------------------------------------------------


def test_policy_scope_assembles_every_link(db_session: Session) -> None:
    scope = rp.policy_scope(
        db_session, _bank(db_session, institution_type="savings_and_loans"), as_of=AS_OF
    )
    assert scope.jurisdiction_code == "GH"
    assert scope.currency == "GHS"
    assert scope.regulator_short == "BoG"
    assert scope.regulator_name == "Bank of Ghana"
    assert scope.institution_type == "savings_and_loans"
    assert scope.institution_class == "sdi"
    assert scope.capital_regime == "s29"
    assert scope.return_family == "sdi"
    assert scope.liquidity_binding is True
    assert scope.as_of == AS_OF


def test_policy_scope_fails_closed_on_an_unknown_licence_class(db_session: Session) -> None:
    """P0-12: no regime may be selected for an unknown licence class."""
    bank = _bank(db_session, institution_type="not_a_real_licence_class", commit=False)
    with pytest.raises(institution_types.InstitutionTypeUnresolved):
        rp.policy_scope(db_session, bank, as_of=AS_OF)


def test_policy_scope_fails_closed_on_a_missing_jurisdiction(db_session: Session) -> None:
    bank = _bank(db_session, jurisdiction_code="", commit=False)
    with pytest.raises(PolicyUnresolvedError) as excinfo:
        rp.policy_scope(db_session, bank, as_of=AS_OF)
    assert "has no jurisdiction_code" in str(excinfo.value)


def test_policy_scope_fails_closed_on_an_unregistered_jurisdiction(db_session: Session) -> None:
    bank = _bank(db_session, jurisdiction_code="ZZ", commit=False)
    with pytest.raises(PolicyUnresolvedError) as excinfo:
        rp.policy_scope(db_session, bank, as_of=AS_OF)
    assert "not in the jurisdictions registry" in str(excinfo.value)


def test_jurisdiction_code_helper_never_substitutes_ghana(db_session: Session) -> None:
    _register_nigeria(db_session)
    assert (
        jurisdictions.jurisdiction_code(_bank(db_session, jurisdiction_code="ng", commit=False))
        == "NG"
    )
    with pytest.raises(PolicyUnresolvedError):
        jurisdictions.jurisdiction_code(_bank(db_session, jurisdiction_code="", commit=False))


# --------------------------------------------------------------------------
# Resolution through the chain
# --------------------------------------------------------------------------


def test_resolve_uses_the_class_row_for_a_bank(db_session: Session) -> None:
    resolved = rp.resolve(db_session, _bank(db_session), "car_min", as_of=AS_OF)
    assert resolved.normalized_value == Decimal("13")
    assert resolved.scope_type == "institution_class"
    assert resolved.layer == "institution_class"
    assert resolved.provenance()["jurisdiction_code"] == "GH"


def test_resolve_uses_the_class_row_for_an_sdi(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="savings_and_loans")
    assert rp.resolve(db_session, bank, "car_min", as_of=AS_OF).normalized_value == Decimal("10")


def test_licence_row_beats_the_class_row(db_session: Session) -> None:
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_type",
            scope_key="universal_bank",
            param_code="car_min",
            jurisdiction_code="GH",
            value_numeric=Decimal("14"),
            unit="percent",
            source_citation="test licence-specific floor",
            confirmation_status="confirmed",
            effective_from=date(2020, 1, 1),
            status="approved",
            proposed_by="test",
            approved_by="test",
        )
    )
    db_session.flush()
    resolved = rp.resolve(db_session, _bank(db_session), "car_min", as_of=AS_OF)
    assert resolved.normalized_value == Decimal("14")
    assert resolved.scope_type == "institution_type"


def test_a_foreign_jurisdiction_does_not_inherit_ghanas_parameter_set(
    db_session: Session,
) -> None:
    """The remediation this whole workstream exists for: without the ``or "GH"``
    default, a bank licensed elsewhere resolves NOTHING rather than silently
    inheriting Ghana's CAR floor, provisioning grid and DPD boundaries."""
    _register_nigeria(db_session)
    bank = _bank(db_session, jurisdiction_code="NG", currency="NGN")
    assert rp.try_resolve(db_session, bank, "car_min", as_of=AS_OF) is None
    with pytest.raises(rp.RegulatoryParameterError):
        rp.resolve(db_session, bank, "car_min", as_of=AS_OF)


def test_unseeded_parameter_carries_a_policy_unresolved_detail(db_session: Session) -> None:
    with pytest.raises(rp.RegulatoryParameterError) as excinfo:
        rp.resolve(db_session, _bank(db_session), "not_a_parameter", as_of=AS_OF)
    detail = excinfo.value.detail
    assert detail is not None
    assert detail.state.value == "policy_unresolved"
    assert detail.blocks_filing is True


def test_resolve_class_value_requires_an_explicit_jurisdiction(db_session: Session) -> None:
    assert rp.resolve_class_value(
        db_session, "sdi", "large_exposure_limit_pct", jurisdiction="GH"
    ) == Decimal("15")
    assert (
        rp.resolve_class_value(db_session, "sdi", "large_exposure_limit_pct", jurisdiction="NG")
        is None
    )
    with pytest.raises(TypeError):
        rp.resolve_class_value(db_session, "sdi", "large_exposure_limit_pct")  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# Generalised tighten-only enforcement, through the database
# --------------------------------------------------------------------------


def test_clamp_overrides_enforces_every_governed_code_with_a_seeded_value(
    db_session: Session,
) -> None:
    bank = _bank(db_session, institution_type="savings_and_loans")
    # Deliberately weaker than every seeded SDI value.
    tenant = {
        "car_min": Decimal("5"),
        "primary_liquidity_reserve_pct": Decimal("1"),
        "secondary_liquidity_reserve_pct": Decimal("2"),
        "statutory_reserve_fund_pct": Decimal("10"),
        "prov_loss": Decimal("50"),
        "large_exposure_limit_pct": Decimal("40"),
        "single_obligor_limit_pct": Decimal("60"),
        "narrow_to_volatile": Decimal("10"),
        # not governed — must pass through untouched
        "car_early_warning": Decimal("5"),
    }
    report = rp.clamp_overrides(db_session, bank, tenant, as_of=AS_OF)
    assert report.values["car_min"] == Decimal("10")
    assert report.values["primary_liquidity_reserve_pct"] == Decimal("10")
    assert report.values["secondary_liquidity_reserve_pct"] == Decimal("15")
    assert report.values["statutory_reserve_fund_pct"] == Decimal("50")
    assert report.values["prov_loss"] == Decimal("100")
    assert report.values["large_exposure_limit_pct"] == Decimal("15")
    assert report.values["single_obligor_limit_pct"] == Decimal("25")
    assert report.values["narrow_to_volatile"] == Decimal("90")
    assert report.values["car_early_warning"] == Decimal("5")
    assert "car_early_warning" not in report.codes_clamped()


def test_a_board_override_cannot_widen_the_filing_tolerance(db_session: Session) -> None:
    """NEW-39, through the seeded control plane.

    ``balance_identity_tolerance_pct`` is seeded at 0.10% for both institution
    classes and is a CEILING: a board register may hold itself to a tighter
    balance-sheet identity gate, never a looser one. Widening it is precisely
    how a broken book would become a filed return with an approval trail.
    """
    for institution_type in ("universal_bank", "savings_and_loans"):
        bank = _bank(db_session, institution_type=institution_type)
        widened = rp.clamp_overrides(
            db_session,
            bank,
            {"balance_identity_tolerance_pct": Decimal("5.00")},
            as_of=AS_OF,
        )
        assert widened.values["balance_identity_tolerance_pct"] == Decimal("0.10")
        assert widened.codes_clamped() == ("balance_identity_tolerance_pct",)
        tightened = rp.clamp_overrides(
            db_session,
            bank,
            {"balance_identity_tolerance_pct": Decimal("0.02")},
            as_of=AS_OF,
        )
        assert tightened.any_clamped is False
        assert tightened.values["balance_identity_tolerance_pct"] == Decimal("0.02")


def test_clamp_overrides_leaves_stricter_board_values_alone(db_session: Session) -> None:
    bank = _bank(db_session)
    report = rp.clamp_overrides(
        db_session,
        bank,
        {"car_min": Decimal("18"), "large_exposure_limit_pct": Decimal("10")},
        as_of=AS_OF,
    )
    assert report.any_clamped is False
    assert report.values["car_min"] == Decimal("18")
    assert report.values["large_exposure_limit_pct"] == Decimal("10")


def test_clamp_overrides_does_not_invent_a_floor_for_an_unseeded_code(
    db_session: Session,
) -> None:
    """``cet1_min``/``tier1_min``/``leverage_min``/``lcr_min``/``nsfr_min`` are
    governed but have NO seeded control-plane value. They must pass through
    unchanged — a regulatory number is never invented to create a constraint."""
    bank = _bank(db_session)
    unseeded = {
        "cet1_min": Decimal("0.5"),
        "tier1_min": Decimal("0.5"),
        "leverage_min": Decimal("0.5"),
        "lcr_min": Decimal("0.5"),
        "nsfr_min": Decimal("0.5"),
    }
    report = rp.clamp_overrides(db_session, bank, unseeded, as_of=AS_OF)
    assert report.values == unseeded
    assert report.any_clamped is False
    assert rp.control_values(db_session, bank, unseeded, as_of=AS_OF) == dict.fromkeys(unseeded)


def test_control_values_reports_seeded_and_unseeded_side_by_side(db_session: Session) -> None:
    bank = _bank(db_session)
    values = rp.control_values(db_session, bank, ["car_min", "cet1_min"], as_of=AS_OF)
    assert values == {"car_min": Decimal("13"), "cet1_min": None}


def test_effective_dating_supersedes_a_generation(db_session: Session) -> None:
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="car_min",
            jurisdiction_code="GH",
            value_numeric=Decimal("14.5"),
            unit="percent",
            source_citation="test successor generation",
            confirmation_status="confirmed",
            effective_from=date(2026, 4, 1),
            status="approved",
            proposed_by="test",
            approved_by="test",
            approved_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    )
    db_session.flush()
    bank = _bank(db_session)
    before = rp.resolve(db_session, bank, "car_min", as_of=date(2026, 3, 31))
    after = rp.resolve(db_session, bank, "car_min", as_of=date(2026, 4, 1))
    assert before.normalized_value == Decimal("13")
    assert after.normalized_value == Decimal("14.5")


def test_a_draft_generation_never_resolves(db_session: Session) -> None:
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="car_min",
            jurisdiction_code="GH",
            value_numeric=Decimal("99"),
            unit="percent",
            source_citation="unapproved proposal",
            confirmation_status="confirmed",
            effective_from=date(2026, 5, 1),
            status="draft",
            proposed_by="test",
        )
    )
    db_session.flush()
    resolved = rp.resolve(db_session, _bank(db_session), "car_min", as_of=AS_OF)
    assert resolved.normalized_value == Decimal("13")
