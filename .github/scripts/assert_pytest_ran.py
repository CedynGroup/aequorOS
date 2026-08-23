#!/usr/bin/env python3
"""Fail a CI step that reported success having executed nothing.

pytest exits 0 when every test it collected SKIPPED itself, which is how a gate
comes to be green and empty at the same time. It is the D-32 failure in its
commonest form and this repository has shipped it more than once:

  * the storage contract suite ran `27 passed, 18 skipped` in every job for
    months because no job configured S3_*; the eighteen that mattered — SSE-KMS
    at rest, the hash-chained access log, per-institution provisioning — had
    never executed anywhere;
  * the Postgres concurrency tests self-skip without a Postgres URL, and CI gave
    that URL to one task only, so they ran in no environment at all.

Nothing in either output said so. This reads pytest's own JUnit XML — written
via PYTEST_ADDOPTS, so no task command has to change — and asserts the step did
work.

  --min-executed N   at least N tests actually ran (total minus skipped)
  --max-skipped N    at most N tests skipped; use where the environment is
                     supposed to satisfy the whole suite
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--min-executed", type=int, default=1)
    parser.add_argument("--max-skipped", type=int, default=None)
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"::error::{args.report} was not written — pytest did not run.")
        return 1

    root = ET.parse(args.report).getroot()
    suites = root.iter("testsuite") if root.tag == "testsuites" else [root]

    total = skipped = errors = failures = 0
    for suite in suites:
        total += int(suite.get("tests", 0))
        skipped += int(suite.get("skipped", 0))
        errors += int(suite.get("errors", 0))
        failures += int(suite.get("failures", 0))
    executed = total - skipped

    print(
        f"{args.report.name}: {total} collected, {executed} executed, "
        f"{skipped} skipped, {failures} failed, {errors} errored."
    )

    problems: list[str] = []
    if errors:
        problems.append(f"{errors} collection/setup error(s) — the run is not trustworthy")
    if executed < args.min_executed:
        problems.append(
            f"only {executed} test(s) executed, expected at least {args.min_executed}. "
            "A green step that ran nothing is the failure this check exists for"
        )
    if args.max_skipped is not None and skipped > args.max_skipped:
        problems.append(
            f"{skipped} test(s) skipped, at most {args.max_skipped} expected. "
            "The environment this job stands up is supposed to satisfy them; a "
            "skip here means the job is green without exercising what it names"
        )

    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
