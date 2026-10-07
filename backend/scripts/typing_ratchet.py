"""Hold the backend to basedpyright strict, ratcheting the legacy errors down.

``pyproject.toml`` runs basedpyright in strict mode with ``reportAny`` and the
``reportUnknown*`` family on. Most of the tree predates that, so this gate runs the
checker once and compares each module's errors, counted per rule, with
``typing_baseline.json``:

* A module with no baseline entry is strict: any error fails. That covers every new
  module, every module that already passed when the baseline was recorded, and
  ``STRICT_MODULES``, which may never take an entry.
* A legacy module fails when a rule's count grows past its baseline.
* A count that falls below its baseline also fails until ``update`` records it, so
  the baseline only shrinks and every fix is kept by the change that makes it.

Run from ``backend/``::

    uv run python scripts/typing_ratchet.py check   # the gate: mise run risk-service:typecheck
    uv run python scripts/typing_ratchet.py update  # record fixed errors; never adds one

``update`` refuses to run while any count is above its baseline. Baseline keys are
current dotted module names; moved modules must pass strict checking at their new
location before ``update`` can remove their old entries.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from pydantic import TypeAdapter

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = BACKEND / "scripts" / "typing_baseline.json"

#: Strict from the start: the feature packages and kernel seams the feature-layout work
#: created. The baseline may never cover them, even by a hand edit.
STRICT_MODULES: tuple[str, ...] = (
    "app.core.tenancy",
    "app.db.base",
    "app.forecasting",
    "app.identity",
    "app.live",
    "app.models.bank",
    "app.models.job",
    "app.models.parameter_register",
)

#: The rule name charged to a diagnostic basedpyright raises without one, such as a
#: syntax error. The baseline never holds it.
UNRULED = "unruled"

#: Rule -> error count, per module.
type Counts = dict[str, dict[str, int]]


class _Position(TypedDict):
    line: int
    character: int


class _Range(TypedDict):
    start: _Position


class _RawDiagnostic(TypedDict):
    file: str
    severity: str
    message: str
    range: NotRequired[_Range]
    rule: NotRequired[str]


class _Report(TypedDict):
    generalDiagnostics: list[_RawDiagnostic]


_REPORT = TypeAdapter(_Report)
_COUNTS = TypeAdapter(dict[str, dict[str, int]])


@dataclass(frozen=True)
class Diagnostic:
    """One basedpyright error, charged to its module's current name."""

    module: str
    rule: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location} - {self.rule}: {self.message}"


@dataclass(frozen=True)
class Drift:
    """A module and rule whose error count differs from the baseline."""

    module: str
    rule: str
    found: int
    allowed: int

    def __str__(self) -> str:
        return f"{self.module} {self.rule}: {self.found} (baseline {self.allowed})"


@dataclass(frozen=True)
class Comparison:
    """How the current errors stand against the baseline."""

    grown: tuple[Drift, ...]
    shrunk: tuple[Drift, ...]
    strict_entries: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (self.grown or self.shrunk or self.strict_entries)


def module_of(path: Path) -> str:
    """Dotted module name of a backend file, naming a package by its directory."""
    relative = path.resolve().relative_to(BACKEND).with_suffix("")
    return ".".join(relative.parts).removesuffix(".__init__")


def is_strict_module(module: str) -> bool:
    return any(module == strict or module.startswith(f"{strict}.") for strict in STRICT_MODULES)


def run_basedpyright() -> list[Diagnostic]:
    """Type-check the backend and return its errors and warnings."""
    result = subprocess.run(
        ["basedpyright", "--outputjson"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        sys.exit(f"basedpyright failed (exit {result.returncode}):\n{result.stderr}{result.stdout}")
    return parse_report(result.stdout)


def parse_report(output: str) -> list[Diagnostic]:
    """The errors and warnings in basedpyright's ``--outputjson`` report."""
    return [
        _diagnostic(raw)
        for raw in _REPORT.validate_json(output)["generalDiagnostics"]
        if raw["severity"] in ("error", "warning")
    ]


def _diagnostic(raw: _RawDiagnostic) -> Diagnostic:
    path = Path(raw["file"])
    location = path.relative_to(BACKEND).as_posix() if path.is_relative_to(BACKEND) else str(path)
    if "range" in raw:
        start = raw["range"]["start"]
        location = f"{location}:{start['line'] + 1}:{start['character'] + 1}"
    return Diagnostic(
        module=module_of(path),
        rule=raw.get("rule", UNRULED),
        location=location,
        message=raw["message"].splitlines()[0],
    )


def count(diagnostics: Iterable[Diagnostic]) -> Counts:
    tally = Counter((diagnostic.module, diagnostic.rule) for diagnostic in diagnostics)
    counts: Counts = {}
    for (module, rule), errors in tally.items():
        counts.setdefault(module, {})[rule] = errors
    return counts


def compare(current: Counts, baseline: Counts) -> Comparison:
    grown: list[Drift] = []
    shrunk: list[Drift] = []
    for module in sorted(current.keys() | baseline.keys()):
        found, allowed = current.get(module, {}), baseline.get(module, {})
        for rule in sorted(found.keys() | allowed.keys()):
            drift = Drift(module, rule, found.get(rule, 0), allowed.get(rule, 0))
            if drift.found > drift.allowed:
                grown.append(drift)
            elif drift.found < drift.allowed:
                shrunk.append(drift)
    return Comparison(
        grown=tuple(grown),
        shrunk=tuple(shrunk),
        strict_entries=tuple(module for module in sorted(baseline) if is_strict_module(module)),
    )


def load_baseline() -> Counts:
    return _COUNTS.validate_json(BASELINE.read_bytes())


def render(counts: Counts) -> str:
    """``counts`` in the baseline's one canonical form."""
    ordered = {module: dict(sorted(counts[module].items())) for module in sorted(counts)}
    return json.dumps(ordered, indent=1) + "\n"


def write_baseline(counts: Counts) -> None:
    BASELINE.write_text(render(counts), encoding="utf-8")


def report(diagnostics: Sequence[Diagnostic], comparison: Comparison) -> list[str]:
    """The failure message for ``comparison``, empty when it is clean."""
    lines: list[str] = []
    if comparison.strict_entries:
        lines.append(
            f"{BASELINE.name} covers strict modules; delete their entries and fix the errors:"
        )
        lines.extend(f"  {module}" for module in comparison.strict_entries)
    if comparison.grown:
        found: dict[tuple[str, str], list[Diagnostic]] = {}
        for diagnostic in diagnostics:
            found.setdefault((diagnostic.module, diagnostic.rule), []).append(diagnostic)
        lines.append(
            "New type errors. A module without a baseline entry is strict; a legacy "
            "module may not add errors (CODEBASE_CONVENTIONS.md §1, Typing ratchet):"
        )
        for drift in comparison.grown:
            lines.append(f"  {drift}")
            lines.extend(f"    {diagnostic}" for diagnostic in found[drift.module, drift.rule])
    if comparison.shrunk:
        lines.append(
            "These errors are fixed; record them with "
            "`uv run python scripts/typing_ratchet.py update` so the baseline keeps the gain:"
        )
        lines.extend(f"  {drift}" for drift in comparison.shrunk)
    return lines


def check() -> int:
    diagnostics = run_basedpyright()
    comparison = compare(count(diagnostics), load_baseline())
    if comparison.clean:
        print(f"Typing ratchet clean: {len(diagnostics)} baselined legacy errors.")
        return 0
    print("\n".join(report(diagnostics, comparison)))
    return 1


def update() -> int:
    diagnostics = run_basedpyright()
    current = count(diagnostics)
    comparison = compare(current, load_baseline())
    if comparison.grown or comparison.strict_entries:
        failed = Comparison(comparison.grown, (), comparison.strict_entries)
        print(
            "\n".join(["The baseline only shrinks; fix these first.", *report(diagnostics, failed)])
        )
        return 1
    write_baseline(current)
    print(f"Recorded {len(comparison.shrunk)} lowered counts in {BASELINE.name}.")
    return 0


class _Arguments(argparse.Namespace):
    command: str = "check"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("command", choices=("check", "update"))
    arguments = parser.parse_args(argv, namespace=_Arguments())
    return check() if arguments.command == "check" else update()


if __name__ == "__main__":
    sys.exit(main())
