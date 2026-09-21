"""What the templates SAY about the engines must be true (ICAAP P0, 2026-09-19).

A template note is printed on a filed return. Two were stale or overclaimed:

* the IRRBB notes said the engine "computes the fixed Basel scenario set today"
  and that ±450 bp was "a documented gap" — the engine computes the ±450 bp
  parallel rows whenever the parameter set carries that calibration
  (``domain/irr/engine.IRR_OPTIONAL_SCENARIO_CODES``), but excludes them from
  the outlier column and still uses 9 buckets;
* the CAR minimum was printed as a literal "13%" in headers that the governed,
  effective-dated floor can disagree with (covered in the Appendix II tests).
"""

from __future__ import annotations

from app.domain.irr.engine import IRR_BUCKETS, IRR_OPTIONAL_SCENARIO_CODES
from app.services.regulatory_reporting.templates import get_template


def _notes(template_id: str, section_code: str) -> str:
    template = get_template(template_id)
    assert template is not None
    layout = next(s for s in template.sections if s.section_code == section_code)
    return " ".join(layout.notes)


def test_the_irrbb_notes_describe_what_the_engine_computes() -> None:
    eve = _notes("bog-irrbb-pilot-v1", "eve_scenarios")
    ear = _notes("bog-irrbb-pilot-v1", "earnings_at_risk")
    for stale in ("documented gap", "fixed Basel scenario set today", "pending"):
        assert stale not in eve, stale
        assert stale not in ear, stale
    # The ±450 rows exist in the engine and are described as conditional + informational.
    assert set(IRR_OPTIONAL_SCENARIO_CODES) == {"parallel_up_450", "parallel_down_450"}
    assert "computed and shown ONLY when the active parameter set carries" in eve
    assert "informational" in eve
    assert "six Basel scenarios only" in eve
    # The method limits it states are the engine's actual ones.
    assert f"{len(IRR_BUCKETS)} repricing buckets" in eve
    assert "19" in eve and "not implemented" in eve
    assert "computed and shown when the active parameter set carries" in ear


def test_the_sdi_irrbb_packet_inherits_the_corrected_earnings_note() -> None:
    assert _notes("bog-sdi-irrbb-quarterly-v1", "earnings_at_risk") == _notes(
        "bog-irrbb-pilot-v1", "earnings_at_risk"
    )


def test_the_capital_return_cites_the_crd_minima_without_a_literal_floor() -> None:
    template = get_template("bog-bsd2-capital-v1")
    assert template is not None
    ratios = next(s for s in template.sections if s.section_code == "capital_ratios")
    assert "CRD 2018 ¶71–91" in ratios.source_citation
    for literal in ("13%", "10%", "6.5%", "6%"):
        assert literal not in ratios.source_citation, literal
