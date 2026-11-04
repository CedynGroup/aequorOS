"""Binding the IRRBB standardised framework into ICAAP Pillar 2 (P5-DESIGN §1.6).

Four things have to be true, and each one has cost somebody something before:

* the block binds the SEALED run for the cycle's exact date, and the Pillar 2
  figure the register capitalises is the figure that run measured;
* a refused run reaches the card and the register AS A REFUSAL — under the one
  name D-061 fixed — and never as a zero, because an understated capital
  number that looks complete is worse than an honest gap;
* the commencement date is the governed row, so moving it in the console moves
  the mandate with no code change (D-024);
* resolving that row from readiness or a freeze preflight records NO parameter
  provenance (D-078): those paths seal no run, and a row they left in the
  session's ledger would be swallowed by whatever run is sealed next, inside
  the ``content_digest`` an officer signs.

This module's other-suite cases live in ``test_p5_irrbb_sf_integration.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.icaap.domain.pillar2 import irrbb_sf_method as sf_method


def _control(**values: Any) -> SimpleNamespace:
    body: dict[str, Any] = {
        "comparison_key": "stressed:stress_overlay:irrbb",
        "basis": "stressed",
        "comparator": "stress_overlay",
        "row": "irrbb",
        "status": "inconsistent",
        "explanation": None,
        "explanation_current": False,
    }
    body.update(values)
    return SimpleNamespace(**body)


def _register(method: str, control: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        items=[SimpleNamespace(method=method, table5_row="irrbb")],
        consistency=[control],
    )


def test_the_stress_overlay_difference_is_labelled_a_method_difference() -> None:
    """D-015: the annex uses the earlier engine, the ICAAP uses the framework.

    Those two cannot agree and are not meant to. Calling it a break would train
    preparers to explain away the control that catches real ones.
    """
    from app.icaap.service import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register(sf_method.METHOD, _control())
    )
    assert [entry.code for entry in found] == ["irrbb_method_difference"]
    assert found[0].severity == "warning"


def test_the_same_difference_under_the_interim_method_is_still_a_break() -> None:
    """Both sides then come from the SAME engine, so a gap is a real gap."""
    from app.icaap.service import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register("irrbb_interim_delta_eve", _control())
    )
    assert [entry.code for entry in found] == ["source_consistency_unexplained"]
    assert found[0].severity == "blocking"


def test_a_recorded_explanation_still_answers_the_control() -> None:
    from app.icaap.service import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register(
            sf_method.METHOD,
            _control(explanation="Measured on different engines.", explanation_current=True),
        )
    )
    assert found == []


def test_a_baseline_difference_is_never_excused_by_the_method_difference() -> None:
    """The overlay is a STRESSED comparison; the baseline sides must still agree."""
    from app.icaap.service import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register(
            sf_method.METHOD,
            _control(
                comparison_key="baseline:capital_plan:irrbb",
                basis="baseline",
                comparator="capital_plan",
            ),
        )
    )
    assert [entry.code for entry in found] == ["source_consistency_unexplained"]


def test_the_method_difference_reads_as_something_to_record_not_to_correct() -> None:
    from app.icaap.service import readiness_p2  # noqa: PLC0415

    message = readiness_p2.MESSAGES["irrbb_method_difference"]
    assert "differ because the methods differ" in message
    assert "nothing needs correcting" in message
