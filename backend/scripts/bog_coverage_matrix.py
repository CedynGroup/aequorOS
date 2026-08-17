# ruff: noqa: E501 — report generator: long table rows are intentional
"""Emit the Form × (map · calc · export · test · governance) coverage matrix.

    DATABASE_URL="" uv run python scripts/bog_coverage_matrix.py > ../docs/bog_returns/99_coverage_matrix.md

Everything here is derived from code, so the matrix cannot claim more than the
build actually does:

- map     — a line map exists and binds every leaf input cell (unmapped == 0);
- calc    — the share of bound leaf cells fed from platform data (``mapped``)
            vs ``input_required``/``coa-mapping`` (bank must supply);
- export  — the official layout is committed and the form is registered with
            the template-faithful ``bog_form`` generator;
- test    — a per-form test module exists under tests/services/bog_forms/ (the
            framework gate covers every form structurally regardless);
- governance — the form is a registered return (immutable package lifecycle).
"""

from __future__ import annotations

from pathlib import Path

from app.services.regulatory_reporting.bog_forms.catalog import all_form_specs
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.registry import REGISTRY

TESTS = Path(__file__).resolve().parents[1] / "tests" / "services" / "bog_forms"


def _test_files_for(code: str) -> list[str]:
    """Test modules covering ``code``: ``test_bsd6.py``, multi-form modules like
    ``test_bsd10_11_16_17.py`` (numbers after the first are bare), and family
    modules ``test_bsd3.py`` / ``test_bsd5.py`` / ``test_bsd7.py`` /
    ``test_bsd15.py`` covering the A/B variants; ``test_bsd2_annexes.py`` → BSD2."""
    stem = code.lower()  # e.g. bsd5a
    number = stem[3:].rstrip("ab")  # "5"
    hits: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        parts = path.stem.split("_")[1:]  # ["bsd10", "11", "16", "17"] / ["bsd2", "annexes"]
        covered = {parts[0]} if parts else set()
        prefix = parts[0][:3] if parts else "bsd"
        for extra in parts[1:]:
            if extra.isdigit():
                covered.add(f"{prefix}{extra}")
        if stem in covered or f"bsd{number}" in covered:
            hits.append(path.name)
    return hits


def main() -> None:
    print("# BoG prudential returns — coverage matrix (generated)\n")
    print(
        "| Form | Sheets | Leaf input cells | mapped | input_required | coa-mapping | unmapped |"
        " map | calc | export | test | governance |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for spec in all_form_specs():
        layout = load_layout(spec.code)
        total_inputs = sum(len(s.input_cells) for s in layout.sheets)
        bound = {
            (sheet.name, ref)
            for sheet in spec.sheets
            for line in sheet.lines
            for ref in line.cells.values()
        }
        mapped = sum(
            1 for sheet in spec.sheets for line in sheet.lines if line.source for _ in line.cells
        )
        coa = sum(
            1
            for sheet in spec.sheets
            for line in sheet.lines
            if line.source is None and "chart-of-accounts" in line.notes
            for _ in line.cells
        )
        input_required = sum(
            1
            for sheet in spec.sheets
            for line in sheet.lines
            if line.source is None and "chart-of-accounts" not in line.notes
            for _ in line.cells
        )
        captured = {(s.name, c.ref) for s in layout.sheets for c in s.input_cells}
        unmapped = len(captured - bound)  # captured inputs nobody bound
        blank_grid_bound = len(bound - captured)  # blank-grid data cells bound explicitly
        registered = spec.code in REGISTRY and REGISTRY[spec.code].generator == "bog_form"
        tests = _test_files_for(spec.code)
        total_inputs = (
            total_inputs + blank_grid_bound
        )  # official data cells = captured + blank-grid bound
        calc_pct = (100 * mapped // total_inputs) if total_inputs else 100
        print(
            f"| {spec.code} | {len(layout.sheets)} | {total_inputs} | {mapped} | {input_required} | {coa} | {unmapped} |"
            f" {'✓' if unmapped == 0 else '◐'} | {calc_pct}% platform-fed |"
            f" {'✓' if registered else '✗'} | {'✓ ' + ', '.join(tests) if tests else '◐ framework gate only'} |"
            f" {'✓' if registered else '✗'} |"
        )
    print(
        "\nLegend: map ✓ = every official leaf input cell is bound (source or explicit input_required); "
        "◐ = some official cells still unmapped. calc = % of bound cells fed from platform data today; the "
        "remainder is data the bank must supply (see each form's line-map doc, 'Residual unmapped lines'). "
        "The framework gate (tests/services/test_bog_forms_framework.py) generates + exports EVERY form "
        "template-faithfully and evaluates 100% of the templates' formulas regardless of map status."
    )


if __name__ == "__main__":
    main()
