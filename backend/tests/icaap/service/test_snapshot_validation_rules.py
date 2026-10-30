"""Frozen report rules begin after authorized report and current-annex acquisition."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter

from app.icaap.domain.frameworks import registry
from app.icaap.service.validation_rules import validate_report
from tests.icaap.service.p3_support import FRAMEWORK_ROOT
from tests.icaap.testing.annexes import InMemoryCurrentAnnexRepository
from tests.icaap.testing.frozen_reports import ANNEX_CODE, FrozenReportInput, frozen_report_input


@pytest.fixture
def frozen_report(
    extra_frameworks: None,
    tmp_path: Path,
    current_annex_repository: type[InMemoryCurrentAnnexRepository],
) -> Iterator[FrozenReportInput]:
    # Publish a complete synthetic instrument with a declared stress annex.
    # The real registry computes its digest and the real validator checks it.
    source = TypeAdapter(dict[str, object]).validate_json(
        (FRAMEWORK_ROOT / "gh" / "test_icaap_e2e.json").read_bytes()
    )
    source["code"] = "test_annex_rules"
    source["version"] = "rules.1"
    source["filing"] = {
        **cast(dict[str, object], source["filing"]),
        "annexes": [
            {
                "return_code": ANNEX_CODE,
                "required": True,
                "citations": [{"doc": "TEST-ICAAP-DOC", "ref": "1"}],
            }
        ],
        "stress_validation": {
            "horizon_param": "icaap_stress_horizon_years_min",
            "severe_min_param": "icaap_stress_severe_scenarios_min",
        },
    }
    folder = tmp_path / "frameworks"
    folder.mkdir()
    jurisdiction = folder / str(source["jurisdiction"]).lower()
    jurisdiction.mkdir()
    _ = (jurisdiction / "annex_rules.json").write_text(json.dumps(source))
    roots = registry.extra_roots()
    registry.set_extra_roots((*roots, folder))
    try:
        report = frozen_report_input(
            registry.get("test_annex_rules", "rules.1"), current_annex_repository
        )
        clean = validate_report(report.package, annexes=report.annexes)
        assert [entry for entry in clean if entry["severity"] == "ERROR"] == [], clean
        yield report
    finally:
        registry.set_extra_roots(roots)


@pytest.mark.parametrize(
    ("block_type", "rule"),
    [
        ("capital_position", "icaap.as_of"),
        ("capital_reconciliation", "icaap.pillar2_reconciliation"),
    ],
)
def test_a_report_with_no_capital_figures_at_all_is_an_error(
    frozen_report: FrozenReportInput, block_type: str, rule: str
) -> None:
    """The absence of a figure cannot be quieter than a wrong one (audit F2)."""
    frozen_report.assessment["blocks"] = [
        entry for entry in frozen_report.blocks if entry.get("block_type") != block_type
    ]
    findings = validate_report(frozen_report.package, annexes=frozen_report.annexes)
    assert any(entry["rule"] == rule and entry["severity"] == "ERROR" for entry in findings), (
        findings
    )


def test_an_unanswered_checklist_item_is_an_error(frozen_report: FrozenReportInput) -> None:
    frozen_report.sections[0]["requirements"] = [
        {"item_id": "x", "status": "open", "reason": None, "citations": []}
    ]
    findings = validate_report(frozen_report.package, annexes=frozen_report.annexes)
    assert any(
        entry["rule"] == "icaap.requirements" and entry["severity"] == "ERROR" for entry in findings
    )


def test_a_missing_freeze_document_is_an_error(frozen_report: FrozenReportInput) -> None:
    frozen_report.assessment["attachments"] = []
    findings = validate_report(frozen_report.package, annexes=frozen_report.annexes)
    assert any(entry["rule"] == "icaap.freeze_attachments" for entry in findings)


def test_a_package_that_did_not_come_from_a_freeze_says_so(
    frozen_report: FrozenReportInput,
) -> None:
    frozen_report.package.snapshot = {"metadata": {}}
    findings = validate_report(frozen_report.package, annexes=frozen_report.annexes)
    assert findings[0]["severity"] == "ERROR"
    assert "frozen" in findings[0]["detail"]


def test_a_pending_governed_figure_is_reported_but_does_not_block(
    frozen_report: FrozenReportInput,
) -> None:
    """D-024 §5: a value awaiting stakeholder confirmation is labelled, not hidden."""
    findings = validate_report(frozen_report.package, annexes=frozen_report.annexes)
    pending = [entry for entry in findings if entry["rule"] == "icaap.parameters_pending"]
    assert pending
    assert all(entry["severity"] == "WARNING" for entry in pending)


@pytest.mark.parametrize("current", ["absent", "different_digest"])
def test_the_complete_rules_check_current_annex_answers(
    frozen_report: FrozenReportInput,
    current_annex_repository: type[InMemoryCurrentAnnexRepository],
    current: str,
) -> None:
    records = (
        []
        if current == "absent"
        else [
            (frozen_report.annex_key, replace(frozen_report.annex_version, content_digest="c" * 64))
        ]
    )
    findings = validate_report(frozen_report.package, annexes=current_annex_repository(records))
    errors = [entry for entry in findings if entry["severity"] == "ERROR"]
    assert [entry["rule"] for entry in errors] == ["icaap.annex_current"]
    assert ("no current version" if current == "absent" else "different content") in errors[0][
        "detail"
    ]


def _attempt_attribute_write(target: object, attribute: str) -> None:
    setattr(target, attribute, "changed")


def test_resolved_annex_inputs_are_copied_immutable_and_isolated(
    frozen_report: FrozenReportInput,
    current_annex_repository: type[InMemoryCurrentAnnexRepository],
) -> None:
    records = [(frozen_report.annex_key, frozen_report.annex_version)]
    repository = current_annex_repository(records)
    records.clear()
    current = repository.current(frozen_report.annex_key)
    assert current is not None
    with pytest.raises(FrozenInstanceError):
        _attempt_attribute_write(current, "content_digest")
    clean = validate_report(frozen_report.package, annexes=repository)
    assert [entry for entry in clean if entry["severity"] == "ERROR"] == []
    other = validate_report(frozen_report.package, annexes=current_annex_repository([]))
    assert any(entry["rule"] == "icaap.annex_current" for entry in other)


def test_duplicate_resolved_current_annexes_are_refused(
    frozen_report: FrozenReportInput,
    current_annex_repository: type[InMemoryCurrentAnnexRepository],
) -> None:
    record = (frozen_report.annex_key, frozen_report.annex_version)
    with pytest.raises(ValueError, match="Duplicate resolved current annex"):
        current_annex_repository([record, record])
