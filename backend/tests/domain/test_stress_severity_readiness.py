"""The ¶81 severity requirement is discoverable BEFORE the run that enforces it.

An approved scenario that declares no severity band can no longer be run with a
band-priced management-action plan (audit 2026-08-22 D-8, WS-A3 open item 2):
the old ``None`` fallback of ``1.0`` IS the severe factor in the default
register, so an undeclared band silently pulled every action to its full
authored magnitude and published the most flattering post-action capital
position the plan can produce.

Refusing is right — the Appendix II "Post-capitalisation" block is FILED, and a
conservative default would still report a position on an assumption the bank
never made. But a gate an analyst only meets at the moment they press run is a
gate that wastes their afternoon, so the requirement is also stated on the PLAN,
where it is a property of how the actions were priced and knowable before any
scenario is chosen.

Two surfaces, one rule. These tests exist because that is the failure worth
guarding: a readiness screen that says the plan is fine and a run that refuses
it is worse than no screen at all. Every case therefore asserts the advertised
answer and the enforced answer TOGETHER, never one alone.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
    ManagementActionNotComputable,
    ManagementActionPlan,
    _severity_factor,
    default_severity_factors,
    is_severity_priced,
    severity_priced_action_ids,
    severity_pricing_binds,
)


def _action(action_id: str, factors: dict[str, Decimal] | None) -> ManagementAction:
    kwargs: dict[str, object] = {
        "action_id": action_id,
        "kind": "raise_capital",
        "label": f"Action {action_id}",
        "trigger": ActionTrigger(kind="always"),
        "capital_raise_ghs": Decimal("1000"),
        "capital_raise_tier": "cet1",
    }
    if factors is not None:
        kwargs["severity_factors"] = factors
    return ManagementAction(**kwargs)  # pyright: ignore[reportArgumentType]


def _refuses_without_a_band(action: ManagementAction) -> bool:
    """Whether the RUN-TIME gate refuses this action on an undeclared band."""
    try:
        _severity_factor(action, None)
    except ManagementActionNotComputable:
        return True
    return False


# --- The two surfaces agree ------------------------------------------------


@pytest.mark.parametrize(
    ("case", "factors"),
    [
        ("inherits the default register", None),
        ("prices the three bands differently", {"mild": Decimal("0.5"), "severe": Decimal("1")}),
        ("prices every band identically", {"mild": Decimal("1"), "severe": Decimal("1")}),
        ("prices a single band", {"severe": Decimal("1")}),
        ("prices no band at all", {}),
    ],
)
def test_the_readiness_signal_and_the_run_time_gate_never_disagree(
    case: str, factors: dict[str, Decimal] | None
) -> None:
    """What the plan screen advertises is exactly what the run enforces.

    The whole reason the readiness surface is allowed to exist. If these two
    could drift, an analyst would be sent to a screen reporting the plan is
    runnable and a run that refuses it — or, worse, the other way round.
    """
    action = _action("A1", factors)
    assert is_severity_priced(action) == _refuses_without_a_band(action), case


# --- Absent pricing is NOT band-free ---------------------------------------


def test_an_action_that_prices_no_bands_still_needs_a_declared_band() -> None:
    """The default register is not neutral, and the readiness surface says so.

    An item storing no factors inherits ``{mild: 0.5, moderate: 0.75, severe: 1}``
    — three distinct factors — so the band still moves its magnitude. Reading
    absent pricing as "no band needed" understates the requirement in the
    direction that hurts: it would advertise a plan as runnable against an
    undeclared scenario that the run then refuses.
    """
    inherited = default_severity_factors()
    assert len(set(inherited.values())) > 1
    assert severity_pricing_binds(inherited) is True

    action = _action("A1", None)
    assert action.severity_factors == inherited
    assert is_severity_priced(action) is True
    assert _refuses_without_a_band(action) is True


# --- The plan-level roll-up -------------------------------------------------


def test_a_plan_names_exactly_the_actions_that_need_a_band() -> None:
    """The ids, not just a boolean — an analyst has to know WHICH action to fix."""
    plan = ManagementActionPlan(
        plan_id="P1",
        name="Recovery plan",
        actions=(
            _action("flat", {"mild": Decimal("1"), "severe": Decimal("1")}),
            _action("banded", {"mild": Decimal("0.5"), "severe": Decimal("1")}),
            _action("inherits-default", None),
        ),
    )
    assert severity_priced_action_ids(plan) == ("banded", "inherits-default")


def test_a_wholly_flat_plan_runs_against_a_scenario_with_no_band() -> None:
    """Empty means empty: the plan imposes no requirement on the scenario.

    This is the distinction between needing an input and demanding one. When the
    band cannot change any magnitude there is nothing to resolve, so the run
    proceeds rather than insisting on a declaration it would never read.
    """
    plan = ManagementActionPlan(
        plan_id="P2",
        name="Flat plan",
        actions=(
            _action("a", {"mild": Decimal("1"), "severe": Decimal("1")}),
            _action("b", {"severe": Decimal("0.4")}),
        ),
    )
    assert severity_priced_action_ids(plan) == ()
    for action in plan.actions:
        assert _refuses_without_a_band(action) is False


def test_an_empty_plan_imposes_nothing() -> None:
    assert severity_priced_action_ids(ManagementActionPlan("P3", "Empty", ())) == ()


# --- The refusal still says what to do about it -----------------------------


def test_the_refusal_names_the_action_and_both_ways_out() -> None:
    """A refusal an analyst cannot act on is a 500 with better manners."""
    action = _action("CAP-1", {"mild": Decimal("0.5"), "severe": Decimal("1")})
    with pytest.raises(ManagementActionNotComputable) as caught:
        _severity_factor(action, None)

    detail = caught.value
    assert detail.code == "scenario_severity_undeclared"
    reason = str(detail)
    assert "CAP-1" in reason
    # Both escapes are stated, because either one is a single edit.
    assert "Declare the scenario's severity" in reason
    assert "price the action identically" in reason
    # And it never silently becomes a number.
    assert "management_action.severity_factor" in repr(detail.to_dict())
