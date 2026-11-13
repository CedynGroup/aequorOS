"""Enforce per-file, per-rule Ruff counts with a shrink-only ratchet.

Unlike the type-check baseline, this ratchet is strict in both directions: a
count below the baseline also fails (stale) until ``update`` lowers it, so a
reduction is recorded in the same change that made it. Growth always fails.

Which codes live where is decided once, here and in ``CODEBASE_CONVENTIONS.md``
§1, "Lint baselines":

* ``DTZ`` is selected in the main Ruff run, so tests are held at zero directly;
  every non-test tree is per-file-ignored there (``"!tests/**" = ["DTZ"]``) and
  counted here instead, so app-side violations are tracked, never silently
  ignored.
* ``PT011``, ``PT012``, ``PT017`` and ``RUF043`` are ratchet-only: they are not
  in the main ``select`` at all (so they cannot double-fail) and are counted
  here over ``tests``, which is the only tree where they fire in practice.

Contributor commands are owned by ``CODEBASE_CONVENTIONS.md`` §1.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = BACKEND / "scripts" / "lint_baseline.json"

#: Rule -> error count, per file, paths relative to the backend root.
type Counts = dict[str, dict[str, int]]


class _Location(TypedDict):
    row: int


class _RawViolation(TypedDict):
    filename: str
    code: str
    location: _Location
    message: str


_REPORT = TypeAdapter(list[_RawViolation])
_COUNTS = TypeAdapter(dict[str, dict[str, int]])


@dataclass(frozen=True)
class Scope:
    """One ratchet invocation: a code set over a path set, relative to ``BACKEND``."""

    label: str
    paths: tuple[str, ...]
    codes: tuple[str, ...]


#: The ratcheted code sets. The pytest codes fire only on test code; ``DTZ`` is
#: counted over the whole backend, which also re-covers ``tests`` at its zero.
SCOPES: tuple[Scope, ...] = (
    Scope(
        label="pytest patterns in tests",
        paths=("tests",),
        codes=("PT011", "PT012", "PT017", "RUF043"),
    ),
    Scope(label="naive clocks anywhere", paths=(".",), codes=("DTZ",)),
)


@dataclass(frozen=True)
class Violation:
    """One Ruff finding, charged to the file's baseline name."""

    path: str
    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location} - {self.code}: {self.message}"


@dataclass(frozen=True)
class Drift:
    """A file and code whose count differs from the baseline."""

    path: str
    code: str
    found: int
    allowed: int

    def __str__(self) -> str:
        return f"{self.path} {self.code}: {self.found} (baseline {self.allowed})"


@dataclass(frozen=True)
class Comparison:
    """How the current counts stand against the baseline."""

    grown: tuple[Drift, ...]
    stale: tuple[Drift, ...]

    @property
    def passes(self) -> bool:
        """Whether the gate accepts the change: the counts match the baseline exactly.

        Growth fails outright. A count below the baseline also fails until
        ``update`` lowers it, so the baseline only shrinks in lockstep with the
        violations it tracks.
        """
        return not (self.grown or self.stale)


def violation(path: str, code: str, row: int, message: str) -> Violation:
    return Violation(path=path, code=code, location=f"{path}:{row}", message=message)


def run_ruff(scope: Scope, root: Path = BACKEND) -> list[Violation]:
    """Run Ruff for one scope, ignoring the project configuration.

    ``--isolated`` is the point: the main configuration per-file-ignores the
    ratcheted codes outside ``tests``, so the ratchet must count them without
    it. The pytest codes fire on ``pytest.raises``/``pytest.warns`` call shapes
    anywhere, but the ratchet deliberately scopes them to ``tests``, where the
    flake patterns live.
    """
    command = [
        "ruff",
        "check",
        "--isolated",
        "--no-cache",
        "--output-format",
        "json",
        "--select",
        ",".join(scope.codes),
        *scope.paths,
    ]
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        sys.exit(f"ruff failed (exit {result.returncode}):\n{result.stderr}{result.stdout}")
    return parse_report(result.stdout, root)


def parse_report(output: str, root: Path) -> list[Violation]:
    """The violations in Ruff's ``--output-format json`` report."""
    violations: list[Violation] = []
    for raw in _REPORT.validate_json(output):
        path = Path(raw["filename"])
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        violations.append(
            violation(relative, raw["code"], raw["location"]["row"], raw["message"].splitlines()[0])
        )
    return violations


def count(violations: Iterable[Violation]) -> Counts:
    tally = Counter((item.path, item.code) for item in violations)
    counts: Counts = {}
    for (path, code), found in tally.items():
        counts.setdefault(path, {})[code] = found
    return counts


def compare(current: Counts, baseline: Counts) -> Comparison:
    grown: list[Drift] = []
    stale: list[Drift] = []
    for path in sorted(current.keys() | baseline.keys()):
        found, allowed = current.get(path, {}), baseline.get(path, {})
        for code in sorted(found.keys() | allowed.keys()):
            drift = Drift(path, code, found.get(code, 0), allowed.get(code, 0))
            if drift.found > drift.allowed:
                grown.append(drift)
            elif drift.found < drift.allowed:
                stale.append(drift)
    return Comparison(grown=tuple(grown), stale=tuple(stale))


def load_baseline(baseline: Path = BASELINE) -> Counts:
    if not baseline.exists():
        # First seeding: no baseline yet, so every current count is growth.
        return {}
    return _COUNTS.validate_json(baseline.read_bytes())


def render(counts: Counts) -> str:
    """``counts`` in the baseline's one canonical form."""
    ordered = {path: dict(sorted(counts[path].items())) for path in sorted(counts)}
    return json.dumps(ordered, indent=1) + "\n"


def write_baseline(counts: Counts, baseline: Path = BASELINE) -> None:
    baseline.write_text(render(counts), encoding="utf-8")


def report(comparison: Comparison) -> list[str]:
    """The failure message for ``comparison``, empty when the gate passes."""
    lines: list[str] = []
    if comparison.grown:
        lines.append(
            "New lint violations. A file without a baseline entry holds zero; an "
            "existing one may not add violations (CODEBASE_CONVENTIONS.md §1, "
            "Lint baselines):"
        )
        lines.extend(f"  {drift}" for drift in comparison.grown)
    if comparison.stale:
        lines.append(
            "Counts below the baseline. The ratchet is strict in both directions; "
            "lower it in the same change with `uv run python scripts/lint_baseline.py update`:"
        )
        lines.extend(f"  {drift}" for drift in comparison.stale)
    return lines


def current_counts() -> tuple[list[Violation], Counts]:
    violations: list[Violation] = []
    counts: Counts = {}
    for scope in SCOPES:
        scope_violations = run_ruff(scope)
        violations.extend(scope_violations)
        for path, codes in count(scope_violations).items():
            counts.setdefault(path, {}).update(codes)
    return violations, counts


def check() -> int:
    violations, current = current_counts()
    comparison = compare(current, load_baseline())
    if not comparison.passes:
        print("\n".join(report(comparison)))
        return 1
    print(f"Lint baseline clean: {len(violations)} ratcheted violations.")
    return 0


def update(current: Counts | None = None, baseline: Path = BASELINE) -> int:
    """Record the current counts; refuses to raise the baseline.

    ``current`` is injectable for tests; production always re-measures.
    """
    if current is None:
        current = current_counts()[1]
    comparison = compare(current, load_baseline(baseline))
    if comparison.grown:
        print("\n".join(["The baseline only shrinks; fix these first.", *report(comparison)]))
        return 1
    write_baseline(current, baseline)
    print(f"Recorded {len(comparison.stale)} lowered counts in {baseline.name}.")
    return 0


class _Arguments(argparse.Namespace):
    command: str = "check"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("command", choices=("check", "update"))
    arguments = parser.parse_args(argv, namespace=_Arguments())
    return check() if arguments.command == "check" else update()


if __name__ == "__main__":
    sys.exit(main())
