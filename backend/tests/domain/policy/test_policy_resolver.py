"""The Policy Resolver (shared primitive P2) — the pure half.

Pins the one resolution chain every governed regulatory number goes through:

    Jurisdiction -> Regulator -> Institution Type -> Regime -> Return Family
                 -> Parameter Set -> Effective Date

and the generalised tighten-only enforcement that replaced the two hand-written
per-code clamps (QA audit 2026-08-20 P1-5). Everything here runs without a
database, because the rules are pure — which is the point: two call sites can no
longer disagree about how a code resolves or whether it is clamped.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.policy import (
    PARAMETER_DIRECTION,
    Direction,
    ParameterCandidate,
    PolicyLayer,
    PolicyScope,
    PolicyUnresolvedError,
    clamp_overrides,
    direction_for,
    from_candidate,
    governed_codes,
    is_active_on,
    policy_unresolved,
    resolution_order,
    select_active,
    tighten,
)

AS_OF = date(2026, 6, 30)


def _scope(**overrides: object) -> PolicyScope:
    base: dict[str, object] = {
        "jurisdiction_code": "GH",
        "currency": "GHS",
        "regulator_short": "BoG",
        "regulator_name": "Bank of Ghana",
        "institution_type": "savings_and_loans",
        "institution_class": "sdi",
        "capital_regime": "s29",
        "return_family": "sdi",
        "liquidity_binding": True,
        "as_of": AS_OF,
    }
    base.update(overrides)
    return PolicyScope(**base)  # type: ignore[arg-type]


def _candidate(  # noqa: PLR0913 - a candidate row has ten explicit fields
    *,
    scope_type: str,
    scope_key: str,
    value: str,
    param_code: str = "car_min",
    jurisdiction_code: str = "GH",
    effective_from: date = date(2020, 1, 1),
    effective_to: date | None = None,
    status: str = "approved",
    parameter_id: str = "p-1",
    confirmation_status: str = "confirmed",
) -> ParameterCandidate:
    return ParameterCandidate(
        param_code=param_code,
        scope_type=scope_type,
        scope_key=scope_key,
        jurisdiction_code=jurisdiction_code,
        unit="percent",
        source_citation="test",
        confirmation_status=confirmation_status,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        parameter_id=parameter_id,
        value=Decimal(value),
    )


# --------------------------------------------------------------------------
# The chain key
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "jurisdiction_code",
        "currency",
        "institution_type",
        "institution_class",
        "capital_regime",
        "return_family",
    ],
)
def test_scope_refuses_a_missing_link(field: str) -> None:
    """A chain with a missing link must not be constructible — that is exactly
    how ``jurisdiction_code="GH"`` defaults made a Nigerian bank report in cedis."""
    with pytest.raises(ValueError, match=f"PolicyScope.{field} is required"):
        _scope(**{field: ""})


def test_resolution_order_is_licence_then_class() -> None:
    assert resolution_order(_scope()) == (
        ("institution_type", "savings_and_loans"),
        ("institution_class", "sdi"),
    )


# --------------------------------------------------------------------------
# Effective dating
# --------------------------------------------------------------------------


def test_active_window_is_from_inclusive_to_exclusive() -> None:
    row = _candidate(
        scope_type="institution_class",
        scope_key="sdi",
        value="10",
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 7, 1),
    )
    assert is_active_on(row, date(2025, 12, 31)) is False
    assert is_active_on(row, date(2026, 1, 1)) is True
    assert is_active_on(row, date(2026, 6, 30)) is True
    assert is_active_on(row, date(2026, 7, 1)) is False


def test_open_ended_generation_stays_active() -> None:
    row = _candidate(scope_type="institution_class", scope_key="sdi", value="10")
    assert is_active_on(row, date(2099, 1, 1)) is True


def test_superseded_generation_is_not_selected() -> None:
    scope = _scope()
    old = _candidate(
        scope_type="institution_class",
        scope_key="sdi",
        value="8",
        effective_from=date(2019, 1, 1),
        effective_to=date(2020, 1, 1),
        parameter_id="p-old",
    )
    current = _candidate(
        scope_type="institution_class", scope_key="sdi", value="10", parameter_id="p-new"
    )
    assert select_active([old, current], scope, "car_min") is current


def test_newest_effective_generation_wins() -> None:
    scope = _scope()
    older = _candidate(
        scope_type="institution_class",
        scope_key="sdi",
        value="10",
        effective_from=date(2020, 1, 1),
        parameter_id="p-a",
    )
    newer = _candidate(
        scope_type="institution_class",
        scope_key="sdi",
        value="11",
        effective_from=date(2026, 1, 1),
        parameter_id="p-b",
    )
    assert select_active([older, newer], scope, "car_min") is newer


# --------------------------------------------------------------------------
# Precedence and isolation
# --------------------------------------------------------------------------


def test_licence_row_beats_class_row() -> None:
    scope = _scope()
    klass = _candidate(
        scope_type="institution_class", scope_key="sdi", value="10", parameter_id="p-class"
    )
    licence = _candidate(
        scope_type="institution_type",
        scope_key="savings_and_loans",
        value="12",
        parameter_id="p-type",
    )
    winner = select_active([klass, licence], scope, "car_min")
    assert winner is not None
    assert winner is licence
    assert from_candidate(winner, scope).layer is PolicyLayer.INSTITUTION_TYPE


def test_another_jurisdictions_row_never_resolves() -> None:
    """The whole point of removing the GH defaults: a Ghana row must not be
    selected for a Nigerian institution."""
    scope = _scope(jurisdiction_code="NG", currency="NGN", regulator_short="CBN")
    ghana_row = _candidate(scope_type="institution_class", scope_key="sdi", value="10")
    assert select_active([ghana_row], scope, "car_min") is None


def test_another_classes_row_never_resolves() -> None:
    scope = _scope()
    bank_row = _candidate(scope_type="institution_class", scope_key="bank", value="13")
    assert select_active([bank_row], scope, "car_min") is None


def test_draft_row_never_resolves() -> None:
    """Four-eyes is a precondition of a value being USED, not a display flag."""
    scope = _scope()
    draft = _candidate(scope_type="institution_class", scope_key="sdi", value="10", status="draft")
    assert select_active([draft], scope, "car_min") is None


def test_selection_is_deterministic_on_a_tie() -> None:
    """Two rows identical but for their id resolve to the same one every time —
    an input_hash built over a resolution must be reproducible."""
    scope = _scope()
    rows = [
        _candidate(scope_type="institution_class", scope_key="sdi", value="10", parameter_id="b"),
        _candidate(scope_type="institution_class", scope_key="sdi", value="11", parameter_id="a"),
    ]
    first = select_active(rows, scope, "car_min")
    second = select_active(list(reversed(rows)), scope, "car_min")
    assert first is not None and second is not None
    assert first.parameter_id == "a"
    assert second.parameter_id == "a"


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_provenance_carries_the_whole_chain() -> None:
    scope = _scope()
    resolution = from_candidate(
        _candidate(scope_type="institution_class", scope_key="sdi", value="10.000000"), scope
    )
    provenance = resolution.provenance()
    assert provenance["param_code"] == "car_min"
    # Numeric(18,6) round-trip zeros stripped so the value stays byte-identical
    # to the in-code constant it replaced when it lands in a content digest.
    assert provenance["value"] == "10"
    assert provenance["layer"] == "institution_class"
    assert provenance["jurisdiction_code"] == "GH"
    assert provenance["regulator"] == "BoG"
    assert provenance["institution_type"] == "savings_and_loans"
    assert provenance["institution_class"] == "sdi"
    assert provenance["capital_regime"] == "s29"
    assert provenance["return_family"] == "sdi"
    assert provenance["effective_from"] == "2020-01-01"
    assert provenance["as_of"] == "2026-06-30"
    assert provenance["confirmation_status"] == "confirmed"
    assert provenance["clamped"] is False


def test_pending_confirmation_is_visible_on_the_resolution() -> None:
    scope = _scope()
    resolution = from_candidate(
        _candidate(
            scope_type="institution_class",
            scope_key="sdi",
            value="25",
            confirmation_status="pending",
        ),
        scope,
    )
    assert resolution.is_pending is True
    assert resolution.provenance()["confirmation_status"] == "pending"


# --------------------------------------------------------------------------
# Fail-closed vocabulary
# --------------------------------------------------------------------------


def test_policy_unresolved_uses_the_authority_state() -> None:
    detail = policy_unresolved("car_min", _scope())
    assert detail.state is OutcomeState.POLICY_UNRESOLVED
    assert detail.metric_id == "car_min"
    assert detail.blocks_filing is True
    assert detail.items == ("param:car_min",)
    assert "GH/BoG savings_and_loans" in detail.reason


def test_policy_unresolved_error_is_a_not_computable() -> None:
    error = PolicyUnresolvedError(policy_unresolved("lcr_min", _scope()))
    assert isinstance(error, NotComputable)
    assert error.state is OutcomeState.POLICY_UNRESOLVED
    assert error.blocks_filing is True


# --------------------------------------------------------------------------
# Generalised tighten-only enforcement
# --------------------------------------------------------------------------


EXPECTED_GOVERNED = {
    "car_min",
    "cet1_min",
    "tier1_min",
    "leverage_min",
    "lcr_min",
    "nsfr_min",
    "primary_liquidity_reserve_pct",
    "secondary_liquidity_reserve_pct",
    "statutory_reserve_fund_pct",
    "single_obligor_limit_pct",
    "large_exposure_limit_pct",
    "related_party_limit_pct",
    "prov_standard",
    "prov_olem",
    "prov_substandard",
    "prov_doubtful",
    "prov_loss",
    "narrow_to_volatile",
    "broad_to_volatile",
    "narrow_to_short_term",
    "broad_to_short_term",
    "narrow_to_total_assets",
    "broad_to_total_assets",
    "narrow_to_total_deposits",
    "broad_to_total_deposits",
    "balance_identity_tolerance_pct",
    # Credit PR-2 (Notice BG/GOV/SEC/2025/23): NPL prudential ceiling, the
    # dividend-restriction trigger, and the restructured-loan cure counts.
    # Ceilings tighten DOWN; the cure counts are floors (more consecutive
    # payments before a cure = stricter).
    "npl_limit_pct",
    "npl_dividend_restriction_pct",
    "restructure_cure_payments",
    "restructure_cure_payments_semi_annual",
}


def test_the_governed_code_set_is_pinned() -> None:
    """Adding or removing a governed code is a governance decision, not a tidy-up."""
    assert governed_codes() == EXPECTED_GOVERNED
    assert set(PARAMETER_DIRECTION) == EXPECTED_GOVERNED


def test_every_governed_code_declares_a_valid_direction() -> None:
    for code in governed_codes():
        assert direction_for(code) in (Direction.FLOOR, Direction.CEILING), code


def test_tighten_floor_takes_the_higher_value() -> None:
    assert tighten("car_min", Decimal("10"), Decimal("13")) == Decimal("13")
    assert tighten("car_min", Decimal("15"), Decimal("13")) == Decimal("15")


def test_tighten_ceiling_takes_the_lower_value() -> None:
    assert tighten("large_exposure_limit_pct", Decimal("25"), Decimal("20")) == Decimal("20")
    assert tighten("large_exposure_limit_pct", Decimal("15"), Decimal("20")) == Decimal("15")


def test_undeclared_code_is_unconstrained() -> None:
    assert tighten("car_early_warning", Decimal("5"), Decimal("13")) == Decimal("5")


def test_clamp_overrides_covers_every_governed_code_not_just_car_min() -> None:
    """The generalisation. Before 2026-08-21 only ``car_min`` and the eight LMTD
    floors were actually clamped anywhere; the other sixteen codes were declared
    in the direction map and never enforced."""
    tenant = {code: Decimal("1") for code in governed_codes()}
    # Every ceiling gets a LOOSER tenant value so the clamp has work to do
    # (a floor's tenant=1 is already looser than control=50).
    tenant.update(
        {
            code: Decimal("99")
            for code in governed_codes()
            if direction_for(code) is Direction.CEILING
        }
    )
    control: dict[str, Decimal | None] = {
        code: (Decimal("50") if direction_for(code) is Direction.FLOOR else Decimal("20"))
        for code in governed_codes()
    }
    report = clamp_overrides(tenant, control)
    assert set(report.codes_clamped()) == governed_codes()
    for code in governed_codes():
        if direction_for(code) is Direction.FLOOR:
            assert report.values[code] == Decimal("50"), code
        else:
            assert report.values[code] == Decimal("20"), code


def test_a_board_override_cannot_widen_the_balance_identity_tolerance() -> None:
    """NEW-39. The filing gate's tolerance is a CEILING, not a free parameter.

    ``balance_identity_tolerance_pct`` is the width of the
    ``|assets - (liabilities + equity)|`` gap (as a percent of total assets) a
    book may carry and still produce a FILED number. A wider tolerance admits a
    more broken book, so a board register may only move it DOWN. It was seeded
    and documented as tighten-only but was absent from ``PARAMETER_DIRECTION``
    until 2026-08-22, which made it the one governed code an override could
    loosen — on the single control standing between a broken book and a filed
    return.
    """
    assert direction_for("balance_identity_tolerance_pct") is Direction.CEILING
    # A board asking for 5% against a governed 0.10% is clamped back to 0.10%.
    widened = clamp_overrides(
        {"balance_identity_tolerance_pct": Decimal("5.00")},
        {"balance_identity_tolerance_pct": Decimal("0.10")},
    )
    assert widened.values["balance_identity_tolerance_pct"] == Decimal("0.10")
    (record,) = widened.clamped
    assert record.direction is Direction.CEILING
    assert record.tenant_value == Decimal("5.00")
    assert record.effective_value == Decimal("0.10")
    # Tightening is honoured: a board holding itself to 0.02% keeps 0.02%.
    tightened = clamp_overrides(
        {"balance_identity_tolerance_pct": Decimal("0.02")},
        {"balance_identity_tolerance_pct": Decimal("0.10")},
    )
    assert tightened.any_clamped is False
    assert tightened.values["balance_identity_tolerance_pct"] == Decimal("0.02")


def test_clamp_leaves_a_stricter_board_value_alone() -> None:
    report = clamp_overrides(
        {"car_min": Decimal("15"), "large_exposure_limit_pct": Decimal("12")},
        {"car_min": Decimal("13"), "large_exposure_limit_pct": Decimal("20")},
    )
    assert report.any_clamped is False
    assert report.values == {"car_min": Decimal("15"), "large_exposure_limit_pct": Decimal("12")}


def test_clamp_never_invents_a_floor_to_clamp_against() -> None:
    """A code with no seeded governed value passes through untouched — an
    unconfirmed regulatory number is never manufactured to create a constraint."""
    report = clamp_overrides(
        {"cet1_min": Decimal("4"), "car_min": Decimal("10")},
        {"cet1_min": None, "car_min": Decimal("13")},
    )
    assert report.values["cet1_min"] == Decimal("4")
    assert report.values["car_min"] == Decimal("13")
    assert report.codes_clamped() == ("car_min",)


def test_clamp_passes_ungoverned_codes_through() -> None:
    report = clamp_overrides(
        {"car_early_warning": Decimal("10.5"), "bia_alpha_pct": Decimal("15")},
        {"car_early_warning": Decimal("13")},
    )
    assert report.values == {
        "car_early_warning": Decimal("10.5"),
        "bia_alpha_pct": Decimal("15"),
    }
    assert report.any_clamped is False


def test_clamp_record_is_auditable() -> None:
    report = clamp_overrides({"car_min": Decimal("10")}, {"car_min": Decimal("13")})
    (record,) = report.clamped
    assert record.to_dict() == {
        "param_code": "car_min",
        "direction": "floor",
        "tenant_value": "10",
        "control_value": "13",
        "effective_value": "13",
        "source_citation": "",
        "confirmation_status": "",
    }
