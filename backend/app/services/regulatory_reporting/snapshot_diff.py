"""What changed between two versions of a return, line item by line item.

The question a supervisor asks about a superseded filing is "what moved, and by
how much" — and a superseded package can always answer it, because every
version keeps its immutable ``snapshot`` whether or not it was ever exported.
This is therefore a diff over snapshots, not over rendered files.

``regulatory-package-v1`` snapshots are structured (sections → rows → values),
so this is a real line-item diff keyed on the regulator's own row codes, not a
text diff of two renders. The server computes it because the comparison an
examiner is shown must be the one the platform stands behind — a client-side
diff would be a second, unaudited implementation of the same claim.

**Volatile metadata is out of scope by construction.** Only ``sections`` and
``totals`` are compared, so ``metadata.generated_at`` can never present itself
as a change — the same exclusion ``digests.content_digest`` makes, for the same
reason: a regeneration of identical figures must read as identical figures.

**A section present in only one version reports every one of its line items.**
A heading that appears or disappears between filings is the largest kind of
figures change there is, and an examiner needs the rows that entered or left
the return, not a note that a heading moved. The section is flagged
``added``/``removed`` and each of its rows carries the same flag, so the UI has
one uniform row-level rendering for all three cases.
"""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from app.models import RegulatoryPackage
from app.schemas.regulatory_reporting import (
    PackageComparisonRead,
    PackageComparisonSideRead,
    SnapshotLineDiffRead,
    SnapshotSectionDiffRead,
)

#: The snapshot's top-level ``totals`` are the stable headline figures (the
#: prior-period movement check compares them across reporting dates), so they
#: are diffed as a synthetic section rather than dropped. ``origin`` keeps them
#: distinguishable from a real section that happened to be coded "totals".
_TOTALS_CODE = "totals"
_TOTALS_TITLE = "Headline totals"


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _movement(base: str | None, target: str | None) -> tuple[str | None, str | None]:
    """Absolute and percentage movement, or (None, None) for non-numeric cells.

    Returns blanks rather than zeros where a figure is textual (a label, a
    date, a Yes/No): a fabricated 0.00 delta would read as "no movement".
    """
    base_value = _decimal(base)
    target_value = _decimal(target)
    if base_value is None or target_value is None:
        return None, None
    delta = target_value - base_value
    try:
        pct = (delta / base_value * 100).quantize(Decimal("0.01"))
    except (DivisionByZero, InvalidOperation):
        return str(delta), None
    return str(delta), str(pct)


def _cell(row: dict[str, Any]) -> str | None:
    value = row.get("value")
    return None if value is None else str(value)


def _keyed_lines(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A section's rows plus its total, keyed for matching across versions.

    Keyed on the regulator's row code, which is what makes this a line-item
    diff rather than a positional one: inserting a row must not report every
    row beneath it as changed. Repeated codes are disambiguated by occurrence
    so a duplicate can never silently swallow its twin's movement.
    """
    lines: dict[str, dict[str, Any]] = {}
    seen: dict[str, int] = {}
    entries = [(row, False) for row in section.get("rows") or []]
    total = section.get("total")
    if isinstance(total, dict):
        entries.append((total, True))
    for row, is_total in entries:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code", ""))
        ordinal = seen.get(code, 0)
        seen[code] = ordinal + 1
        key = code if ordinal == 0 else f"{code}#{ordinal}"
        lines[key] = {**row, "_code": code, "_is_total": is_total}
    return lines


def _line(
    key_row: dict[str, Any],
    change: str,
    base_value: str | None,
    target_value: str | None,
) -> SnapshotLineDiffRead:
    delta, delta_pct = _movement(base_value, target_value)
    return SnapshotLineDiffRead(
        code=str(key_row["_code"]),
        description=str(key_row.get("description") or ""),
        change=change,  # type: ignore[arg-type]
        base_value=base_value,
        target_value=target_value,
        delta=delta,
        delta_pct=delta_pct,
        is_total=bool(key_row["_is_total"]),
    )


def _diff_section(
    base: dict[str, Any] | None, target: dict[str, Any] | None
) -> SnapshotSectionDiffRead | None:
    """One section's line-item diff, or None when every figure matches."""
    present = target if target is not None else base
    if present is None:
        return None
    if base is None:
        change = "added"
    elif target is None:
        change = "removed"
    else:
        change = "changed"
    base_lines = _keyed_lines(base) if base is not None else {}
    target_lines = _keyed_lines(target) if target is not None else {}
    # Target order first so the diff reads in the order of the return as it now
    # stands; keys the target dropped follow, in the order the base had them.
    keys = list(target_lines) + [key for key in base_lines if key not in target_lines]

    lines: list[SnapshotLineDiffRead] = []
    unchanged = 0
    for key in keys:
        base_row = base_lines.get(key)
        target_row = target_lines.get(key)
        base_value = _cell(base_row) if base_row is not None else None
        target_value = _cell(target_row) if target_row is not None else None
        if base_row is None:
            lines.append(_line(target_row or {}, "added", None, target_value))
        elif target_row is None:
            lines.append(_line(base_row, "removed", base_value, None))
        elif base_value != target_value:
            lines.append(_line(target_row, "changed", base_value, target_value))
        else:
            unchanged += 1
    if not lines:
        return None
    return SnapshotSectionDiffRead(
        code=str(present.get("code") or ""),
        title=str(present.get("title") or ""),
        origin=str(present.get("_origin") or "section"),  # type: ignore[arg-type]
        change=change,  # type: ignore[arg-type]
        lines=lines,
        unchanged_line_count=unchanged,
    )


def _comparable_sections(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Every diffable block of a snapshot: its sections, then its headline totals."""
    sections = [
        {**section, "_origin": "section"}
        for section in snapshot.get("sections") or []
        if isinstance(section, dict)
    ]
    totals = snapshot.get("totals") or []
    if totals:
        sections.append(
            {
                "code": _TOTALS_CODE,
                "title": _TOTALS_TITLE,
                "rows": totals,
                "total": None,
                "_origin": "totals",
            }
        )
    return sections


def _section_key(section: dict[str, Any]) -> tuple[str, str]:
    return str(section.get("_origin") or "section"), str(section.get("code") or "")


def diff_snapshots(
    base_snapshot: dict[str, Any], target_snapshot: dict[str, Any]
) -> tuple[list[SnapshotSectionDiffRead], int]:
    """Section-by-section, line-by-line diff. Returns (differing, unchanged count)."""
    base_sections = {_section_key(item): item for item in _comparable_sections(base_snapshot)}
    target_sections = {_section_key(item): item for item in _comparable_sections(target_snapshot)}
    keys = list(target_sections) + [key for key in base_sections if key not in target_sections]

    diffs: list[SnapshotSectionDiffRead] = []
    unchanged = 0
    for key in keys:
        section_diff = _diff_section(base_sections.get(key), target_sections.get(key))
        if section_diff is None:
            unchanged += 1
        else:
            diffs.append(section_diff)
    return diffs, unchanged


def _side(package: RegulatoryPackage) -> PackageComparisonSideRead:
    return PackageComparisonSideRead(
        package_id=package.id,
        version=package.version,
        status=package.status,  # type: ignore[arg-type]
        reporting_date=package.reporting_date,
        basis=package.basis,  # type: ignore[arg-type]
        generated_at=package.generated_at,
        snapshot_sha256=package.snapshot_sha256,
        content_digest=package.content_digest,
    )


def build_comparison(base: RegulatoryPackage, target: RegulatoryPackage) -> PackageComparisonRead:
    """Assemble the wire comparison from two ``RegulatoryPackage`` rows."""
    sections, unchanged_sections = diff_snapshots(base.snapshot or {}, target.snapshot or {})
    counts = {"added": 0, "removed": 0, "changed": 0}
    for section in sections:
        for line in section.lines:
            counts[line.change] += 1
    return PackageComparisonRead(
        base=_side(base),
        target=_side(target),
        identical=not sections,
        changed_count=counts["changed"],
        added_count=counts["added"],
        removed_count=counts["removed"],
        sections=sections,
        unchanged_section_count=unchanged_sections,
    )


__all__ = ["build_comparison", "diff_snapshots"]
