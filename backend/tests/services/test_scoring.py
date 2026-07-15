from __future__ import annotations

import pytest

from app.services import scoring


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "low"),
        (24, "low"),
        (25, "medium"),
        (49, "medium"),
        (50, "high"),
        (74, "high"),
        (75, "critical"),
        (100, "critical"),
    ],
)
def test_risk_level_boundaries(score: int, expected: str) -> None:
    assert scoring.risk_level_for_score(score) == expected


@pytest.mark.parametrize(
    ("impact", "expected"),
    [
        (0, "low"),
        (14, "low"),
        (15, "medium"),
        (29, "medium"),
        (30, "high"),
        (39, "high"),
        (40, "critical"),
    ],
)
def test_severity_boundaries(impact: int, expected: str) -> None:
    assert scoring.severity_for_impact(impact) == expected


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        (
            {"vendorCriticality": "high", "debtToEbitda": "4.5", "cashRunwayMonths": "5"},
            {
                "vendor_criticality": "high",
                "debt_to_ebitda": 4.5,
                "cash_runway_months": 5.0,
            },
        ),
        (
            {"requiredDocuments": "soc2", "providedDocuments": None},
            {"required_documents": ["soc2"]},
        ),
        (
            {
                "required_documents": ["soc2", "", None],
                "provided_documents": {"name": "soc2"},
                "debt_to_ebitda": "",
                "cash_runway_months": {"months": 4},
                "vendor_criticality": ["critical"],
            },
            {
                "required_documents": ["soc2"],
                "provided_documents": ["{'name': 'soc2'}"],
            },
        ),
        ({"debt_to_ebitda": True, "cash_runway_months": False, "unknown": "ignored"}, {}),
    ],
)
def test_scoring_input_normalization(raw: dict[str, object], normalized: dict[str, object]) -> None:
    scoring_input = scoring.normalize_structured_data(raw)

    assert scoring_input.model_dump(exclude_none=True, exclude_defaults=True) == normalized


def test_evaluate_rules_returns_missing_data_finding_for_empty_input() -> None:
    findings = scoring.evaluate_rules(scoring.normalize_structured_data({}))

    assert [finding.rule_id for finding in findings] == ["missing_structured_data"]
    assert findings[0].score_impact == 20


@pytest.mark.parametrize("raw", [{}, {"cashRunwayMonthz": 2}, {"debt_to_ebitda": ""}])
def test_malformed_or_unrecognized_structured_data_counts_as_missing_input(
    raw: dict[str, object],
) -> None:
    findings = scoring.evaluate_rules(scoring.normalize_structured_data(raw))

    assert [finding.rule_id for finding in findings] == ["missing_structured_data"]


def test_evaluate_rules_uses_configured_structured_rule_list() -> None:
    findings = scoring.evaluate_rules(
        scoring.normalize_structured_data(
            {
                "required_documents": ["soc2", "financials", "insurance"],
                "provided_documents": ["financials"],
                "vendor_criticality": "critical",
                "debt_to_ebitda": 6,
                "cash_runway_months": 2,
            }
        )
    )

    assert scoring.rules_evaluated_count() == 5
    assert {finding.rule_id for finding in findings} == {
        "missing_required_documents",
        "vendor_criticality",
        "elevated_debt_to_ebitda",
        "low_cash_runway",
    }
    assert sum(finding.score_impact for finding in findings) == 155
