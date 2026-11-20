#!/usr/bin/env python3
"""Write the non-blocking quarantine job's results into the run summary.

The quarantine owner is deliberately not a gate: a quarantined test may fail
and the job stays green (continue-on-error), so this step exists only to make
those results visible. It reads the JUnit XML the quarantined pytest run wrote
(`PYTEST_ADDOPTS=--junitxml=...`) and prints a Markdown table meant to be
appended to `$GITHUB_STEP_SUMMARY`. It never fails the run: the quarantined
pytest step already had its chance to fail, and a broken report is reported as
a visible error while the step still exits 0.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(report: Path) -> str:
    """Render the quarantine JUnit report as a Markdown summary."""
    if not report.is_file():
        return f"Quarantine report `{report}` was not written; nothing to report.\n"
    cases = [
        (case.get("classname", ""), case.get("name", ""), case)
        for case in ET.parse(report).iter("testcase")
    ]
    if not cases:
        return "No quarantined tests ran; the quarantine is empty.\n"

    def outcome(case: ET.Element) -> str:
        for tag, label in (("failure", "failed"), ("error", "errored"), ("skipped", "skipped")):
            if case.find(tag) is not None:
                return label
        return "passed"

    lines = ["## Quarantined tests (non-blocking)", ""]
    counts: dict[str, int] = {}
    for _classname, _name, case in cases:
        counts[outcome(case)] = counts.get(outcome(case), 0) + 1
    order = ("passed", "failed", "errored", "skipped")
    total = ", ".join(f"{counts[name]} {name}" for name in order if counts.get(name))
    lines.append(f"{len(cases)} quarantined test(s) ran: {total}.")
    lines.append("")
    lines.append("| Test | Outcome |")
    lines.append("| --- | --- |")
    for classname, name, case in cases:
        # Parametrized names can carry '|' or newlines; escape them so one row
        # cannot swallow the rest of the table.
        label = (f"{classname}::{name}" if classname else name).replace("|", "\\|")
        label = label.replace("\r", " ").replace("\n", " ")
        lines.append(f"| `{label}` | {outcome(case)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    arguments = parser.parse_args()
    if not arguments.report.is_file():
        # A missing report is not an empty quarantine: pytest may have died
        # before it could write one. Say so loudly, but keep the step green.
        print(
            f"::warning::Quarantine report {arguments.report} was not written; "
            "the quarantined pytest step may have failed before it could report.",
            file=sys.stderr,
        )
    try:
        print(summarize(arguments.report), end="")
    except (ET.ParseError, OSError) as error:
        print(
            f"::error::Quarantine report {arguments.report} is not readable: {error}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
