"""Unit tests for the non-blocking quarantine job's summary renderer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".github/scripts/quarantine_summary.py"
_spec = importlib.util.spec_from_file_location("quarantine_summary_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
quarantine_summary = importlib.util.module_from_spec(_spec)
sys.modules["quarantine_summary_under_test"] = quarantine_summary
_ = _spec.loader.exec_module(quarantine_summary)
summarize = quarantine_summary.summarize

JUNIT = """<testsuite tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="tests.flake.test_one" name="test_passes"/>
  <testcase classname="tests.flake.test_one" name="test_fails">
    <failure message="assert 1 == 2">trace</failure>
  </testcase>
  <testcase classname="tests.flake.test_two" name="test_skips">
    <skipped message="service unavailable"/>
  </testcase>
</testsuite>
"""


def test_summarize_lists_every_quarantined_outcome(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    _ = report.write_text(JUNIT)
    summary = summarize(report)
    assert "3 quarantined test(s) ran: 1 passed, 1 failed, 1 skipped." in summary
    assert "| `tests.flake.test_one::test_passes` | passed |" in summary
    assert "| `tests.flake.test_one::test_fails` | failed |" in summary
    assert "| `tests.flake.test_two::test_skips` | skipped |" in summary
    assert summary.startswith("## Quarantined tests (non-blocking)")


def test_summarize_reports_an_empty_quarantine(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    _ = report.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"/>')
    assert summarize(report) == "No quarantined tests ran; the quarantine is empty.\n"


def test_summarize_escapes_table_breaking_parametrized_names(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    _ = report.write_text(
        '<testsuite tests="1">'
        '<testcase classname="tests.f" name="test_x[a|b&#10;c]"/>'
        "</testsuite>"
    )
    summary = summarize(report)
    assert "| `tests.f::test_x[a\\|b c]` | passed |" in summary
    # The | and newline inside the name produced no extra rows or line breaks.
    assert summary.count("| passed |") == 1
    assert len(summary.splitlines()) == 7


def test_summarize_names_a_missing_report(tmp_path: Path) -> None:
    missing = tmp_path / "absent.xml"
    assert summarize(missing) == (
        f"Quarantine report `{missing}` was not written; nothing to report.\n"
    )


def test_main_prints_the_summary_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report.xml"
    _ = report.write_text('<testsuite tests="1"><testcase classname="a" name="b"/></testsuite>')
    _ = monkeypatch.setattr("sys.argv", ["quarantine_summary.py", str(report)])
    assert quarantine_summary.main() == 0
    assert "| `a::b` | passed |" in capsys.readouterr().out
