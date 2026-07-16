"""CSV rendering of a resolved return (docs/regulatory_reporting.md §5).

Artifact shape (kind stays ``csv`` in ``regulatory_package_artifacts``):

- **Multi-section templates** (every current BoG template) produce ONE ``.zip``
  artifact containing ``00_metadata.csv``, one numbered CSV per section
  (``01_<section>.csv`` …), and ``99_provenance.csv``. The object path ends in
  ``.zip`` so a download is honest about its container.
- **Single-section templates** produce one plain ``.csv`` with the metadata
  block, the section table, and the provenance rows separated by blank lines.

Data cells are machine-readable (plain decimal strings, minus signs, no
grouping) — the GHS '000 scaling still applies and is declared in the
metadata rows; display conventions (parentheses, Yes/No) live in the xlsx and
pdf artifacts. Zip entries carry fixed timestamps so byte output is
deterministic and re-exports keep a stable checksum.
"""

from __future__ import annotations

import csv
import io
import zipfile

from app.services.regulatory_reporting.templates import (
    RenderedReturn,
    RenderedSection,
    machine_cell,
)

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _metadata_rows(rendered: RenderedReturn) -> list[list[str]]:
    rows = [["field", "value"]]
    rows.extend([label, value] for label, value in rendered.metadata_pairs)
    rows.extend(["attestation", line] for line in rendered.attestation_lines)
    rows.extend(["template_note", note] for note in rendered.template.notes)
    return rows


def _section_rows(section: RenderedSection) -> list[list[str]]:
    rows = [
        ["#section", section.layout.section_code],
        ["#layout", section.layout.layout_id],
        ["#fidelity", section.layout.fidelity],
        ["#source_citation", section.layout.source_citation],
        *[["#note", note] for note in section.layout.notes],
        [column.header for column in section.layout.columns],
    ]
    for rendered_row in section.rows:
        rows.append([machine_cell(cell) for cell in rendered_row.cells])
    if section.total_row is not None:
        rows.append([machine_cell(cell) for cell in section.total_row.cells])
    return rows


def _provenance_rows(rendered: RenderedReturn) -> list[list[str]]:
    rows = [["#provenance", line] for line in rendered.provenance_lines]
    rows.append(["module", "run_id", "input_hash", "engine_version"])
    rows.extend(list(entry) for entry in rendered.provenance_runs)
    return rows


def render_csv(rendered: RenderedReturn) -> tuple[bytes, str]:
    """Render the return; returns ``(payload, extension)`` where extension is
    ``csv`` for a single-section template and ``zip`` otherwise."""
    if len(rendered.sections) == 1:
        rows = _metadata_rows(rendered)
        rows.append([])
        rows.extend(_section_rows(rendered.sections[0]))
        rows.append([])
        rows.extend(_provenance_rows(rendered))
        return _csv_bytes(rows), "csv"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        entries: list[tuple[str, bytes]] = [
            ("00_metadata.csv", _csv_bytes(_metadata_rows(rendered)))
        ]
        for index, section in enumerate(rendered.sections, start=1):
            entries.append(
                (
                    f"{index:02d}_{section.layout.section_code}.csv",
                    _csv_bytes(_section_rows(section)),
                )
            )
        entries.append(("99_provenance.csv", _csv_bytes(_provenance_rows(rendered))))
        for name, payload in entries:
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return buffer.getvalue(), "zip"


__all__ = ["render_csv"]
