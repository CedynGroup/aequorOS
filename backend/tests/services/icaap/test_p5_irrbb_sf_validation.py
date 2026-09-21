"""Validating a frozen report against the standardised framework (§1.6 item 5).

These rules run on a FILED document, long after the freeze, so every figure
they measure against is read from the report's own parameter provenance — never
re-resolved (D-024). A console change after the fact must not silently re-grade
a document somebody signed.

The package is assembled by hand rather than frozen end to end: the freeze path
is already covered by P3's own suite, and what is under test here is what the
rules SAY about a snapshot, which a synthesised one exercises exactly and in a
tenth of the time.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.domain.icaap.pillar2 import irrbb_sf_method as sf_method
from app.models import RegulatoryPackage
from app.services.icaap import validation_rules

AS_OF = date(2026, 12, 31)
COMMENCED = date(2026, 12, 31)
MEASURE = "1234.567890"
TIER1 = "10000"


def _facts(**values: Any) -> dict[str, Any]:
    return {key: {"value": None if value is None else str(value)} for key, value in values.items()}


def _block(  # noqa: PLR0913 - one switch per snapshot condition under test
    *,
    method: str = sf_method.METHOD,
    baseline: str = "1234.5679",
    commencement: date | None = COMMENCED,
    sf_bound: bool = True,
    sf_facts: dict[str, Any] | None = None,
    capital_facts: dict[str, Any] | None = None,
    tolerance: str | None = "1",
) -> dict[str, Any]:
    parameters: list[dict[str, Any]] = []
    if commencement is not None:
        parameters.append(
            {
                "param_code": validation_rules.PARAM_SF_MANDATORY_FROM,
                "resolved": True,
                "value_json": {
                    "schema": "icaap-effective-date-v1",
                    "date": commencement.isoformat(),
                },
            }
        )
    if tolerance is not None:
        parameters.append(
            {
                "param_code": validation_rules.PARAM_SOURCE_TOLERANCE,
                "resolved": True,
                "value": tolerance,
            }
        )
    blocks: list[dict[str, Any]] = [
        {
            "block_type": "pillar2_summary",
            "block_key": "pillar2_summary",
            "seq": 1,
            "facts": {},
            "payload": {
                "raw": {
                    "register": {
                        "items": [
                            {
                                "item_key": "irrbb",
                                "method": method,
                                "baseline": baseline,
                                "stressed": baseline,
                            }
                        ]
                    }
                }
            },
        },
        {
            "block_type": "capital_position",
            "block_key": "capital_position",
            "seq": 1,
            "facts": capital_facts
            if capital_facts is not None
            else _facts(tier1_ratio_pct="20", total_rwa="50000"),
            "payload": {},
        },
    ]
    if sf_bound:
        blocks.append(
            {
                "block_type": "irrbb_sf",
                "block_key": "irrbb_sf",
                "seq": 1,
                "facts": sf_facts
                if sf_facts is not None
                else _facts(
                    eve_risk_measure=MEASURE,
                    tier1=TIER1,
                    assumption_defaults_applied="0",
                    representative_parameters="0",
                ),
                "payload": {},
            }
        )
    return {"sections": [], "blocks": blocks, "parameters": parameters, "annexes": []}


def _package(block: dict[str, Any]) -> RegulatoryPackage:
    return RegulatoryPackage(
        return_code="ICAAP-REPORT",
        reporting_date=AS_OF,
        snapshot={"metadata": {"icaap": block}},
    )


def _rules(
    db: Session, block: dict[str, Any], prefix: str = "icaap.irrbb_sf"
) -> list[dict[str, str]]:
    return [
        finding
        for finding in validation_rules.findings(db, _package(block))
        if finding["rule"].startswith(prefix)
    ]


def test_a_report_that_uses_the_framework_from_the_governed_date_is_clean(
    db_session: Session,
) -> None:
    errors = [
        finding for finding in _rules(db_session, _block()) if finding["severity"] == "ERROR"
    ]
    assert errors == []


def test_the_interim_method_after_the_commencement_date_is_an_error(
    db_session: Session,
) -> None:
    findings = _rules(db_session, _block(method="irrbb_interim_delta_eve"))
    assert any(
        finding["rule"] == "icaap.irrbb_sf.method" and finding["severity"] == "ERROR"
        for finding in findings
    )
    assert COMMENCED.isoformat() in findings[0]["detail"]


def test_the_interim_method_before_the_commencement_date_is_accepted(
    db_session: Session,
) -> None:
    findings = _rules(
        db_session,
        _block(method="irrbb_interim_delta_eve", commencement=date(2027, 12, 31), sf_bound=False),
    )
    assert findings == []


def test_a_report_with_no_governed_commencement_row_asserts_nothing(
    db_session: Session,
) -> None:
    """The rule reads the row the report carries; without one it stays silent."""
    findings = _rules(
        db_session, _block(method="irrbb_interim_delta_eve", commencement=None, sf_bound=False)
    )
    assert findings == []


def test_a_framework_figure_with_no_framework_result_in_the_report_is_an_error(
    db_session: Session,
) -> None:
    findings = _rules(db_session, _block(sf_bound=False))
    assert any(
        finding["rule"] == "icaap.irrbb_sf.source" and finding["severity"] == "ERROR"
        for finding in findings
    )


def test_a_capital_line_that_disagrees_with_its_own_source_is_an_error(
    db_session: Session,
) -> None:
    """The narrative table and the capital line have to be the same figure."""
    findings = _rules(db_session, _block(baseline="999.0000"))
    matching = [finding for finding in findings if finding["rule"] == "icaap.irrbb_sf.source"]
    assert matching and matching[0]["severity"] == "ERROR"
    assert "999.0000" in matching[0]["detail"]
    assert MEASURE in matching[0]["detail"]


def test_the_register_and_the_run_are_compared_in_the_unit_the_register_states(
    db_session: Session,
) -> None:
    """D-062: six places in the engine, four in the register, one figure."""
    findings = _rules(db_session, _block(baseline="1234.5679"))
    assert [finding for finding in findings if finding["rule"] == "icaap.irrbb_sf.source"] == []


def test_a_tier1_denominator_outside_the_governed_tolerance_is_a_warning(
    db_session: Session,
) -> None:
    findings = _rules(
        db_session,
        _block(capital_facts=_facts(tier1_ratio_pct="40", total_rwa="50000")),
    )
    tier1 = [finding for finding in findings if finding["rule"] == "icaap.irrbb_sf.tier1"]
    assert tier1 and tier1[0]["severity"] == "WARNING"


def test_a_tier1_denominator_inside_the_tolerance_says_nothing(db_session: Session) -> None:
    findings = _rules(
        db_session,
        _block(capital_facts=_facts(tier1_ratio_pct="20", total_rwa="50000")),
    )
    assert [finding for finding in findings if finding["rule"] == "icaap.irrbb_sf.tier1"] == []


def test_without_a_governed_tolerance_the_denominator_is_not_graded(
    db_session: Session,
) -> None:
    """No approved row means no comparison — never a platform default (D-024)."""
    findings = _rules(
        db_session,
        _block(
            tolerance=None, capital_facts=_facts(tier1_ratio_pct="40", total_rwa="50000")
        ),
    )
    assert [finding for finding in findings if finding["rule"] == "icaap.irrbb_sf.tier1"] == []


@pytest.mark.parametrize(
    ("facts", "severity"),
    [
        (
            _facts(
                eve_risk_measure=MEASURE,
                tier1=TIER1,
                assumption_defaults_applied="41",
                representative_parameters="0",
            ),
            "INFO",
        ),
        (
            _facts(
                eve_risk_measure=MEASURE,
                tier1=TIER1,
                assumption_defaults_applied="0",
                representative_parameters="1",
            ),
            "WARNING",
        ),
    ],
)
def test_assumptions_and_representative_calibrations_are_stated_in_the_filed_report(
    db_session: Session, facts: dict[str, Any], severity: str
) -> None:
    findings = _rules(db_session, _block(sf_facts=facts))
    stated = [
        finding for finding in findings if finding["rule"] == "icaap.irrbb_sf.assumptions"
    ]
    assert stated and stated[0]["severity"] == severity
