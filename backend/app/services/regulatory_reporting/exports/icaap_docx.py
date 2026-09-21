"""The ICAAP Word working copy (`docx_working`) — never filed, never signed.

A bank's reviewers redline in Word, so the filing needs an editable twin. What
makes that safe rather than dangerous is that the twin says what it is and
cannot be mistaken for the filing:

* **Every page carries the notice.** ``WORKING COPY — not the filed document``
  is the Word section header, so it is on every page of every printout, and it
  is repeated on the cover and on the provenance page.
* **It is excluded from the filing set**
  (``workflow.UNFILABLE_WORKING_ARTIFACT_KINDS``: a working kind reaches a
  regulator only by opting in, and the Word draft does not), so a submission
  never transmits it and a submission event never lists it.
* **It is never signed.** ``artifact_signing`` signs the ``pdf`` kind only, and
  a Word file has no AcroForm signature field to sign into.
* **It carries the same figures as the PDF**, because both are rendered from the
  one frozen snapshot through ``render/from_snapshot.py``. A reviewer's redline
  is therefore against the numbers that were actually frozen.

**Determinism.** python-docx stamps every zip entry with the wall clock at save,
which would make two exports of one sealed package differ in bytes and break the
export checksum's only useful property. P1's renderer already solves that — the
container is re-written through the exporters' shared ``normalize_zip`` with
fixed entry timestamps, and the core properties are pinned to the document's
``generated_at``, which for a filing is the package's own generation time and
never ``now()``. This module adds nothing that reintroduces a clock. (Byte
identity is claimed within one machine and one zlib; cross-platform identity is
not — P1-DESIGN §4.3.)
"""

from __future__ import annotations

from typing import Any, Final

from app.domain.icaap.document import WORKING_COPY_NOTICE
from app.services.icaap.render import format as fmt

# P1 owns the Word renderer and P3's filing copy is explicitly one of the two
# documents its module docstring says it serves. Subclassing its renderer — even
# though the class is underscored — is what keeps the prose, the tables and the
# list markers identical between a draft and a working copy of the same cycle;
# re-implementing them here is how the two would drift. ``test_icaap_docx.py``
# pins the attribute, so a rename in P1 fails a test rather than an export.
from app.services.icaap.render.docx import _DocxRenderer
from app.services.icaap.render.from_snapshot import IcaapFiling, SnapshotTable

#: What the artifact kind is called everywhere: the DB CHECK, the ORM tuple, the
#: export route's Literal, the filing-set exclusion and the generated client.
ARTIFACT_KIND: Final = "docx_working"

#: The file extension and the ``Content-Type`` the exporter stores it under.
EXTENSION: Final = "docx"
MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: Appended to the return code for the object path, so a working copy can never
#: overwrite the filing at ``{code}.pdf``. Mirrors ``xlsx_working``'s stem.
FILE_STEM_SUFFIX: Final = ".working"


class _IcaapWorkingCopyRenderer(_DocxRenderer):
    """P1's Word renderer, with the filing's own headline, annex and provenance."""

    def __init__(self, filing: IcaapFiling) -> None:
        super().__init__(filing.document)
        self._filing = filing

    # -- filing-only parts -------------------------------------------------
    def _snapshot_table(self, table: SnapshotTable, *, level: str = "h2") -> None:
        self._heading(table.title, level)
        self._table(table.spec)

    def _headline(self) -> None:
        headline = self._filing.provenance.headline
        if headline is not None:
            self._snapshot_table(headline, level="h1")

    def _attestation(self) -> None:
        slots = self._filing.provenance.signature_slots
        if not slots:
            return
        self._heading("Attestation", "h1")
        self._note(
            "The filed instrument is the signed PDF. This copy reproduces the "
            "attestation wording; it carries no signature and cannot be signed."
        )
        for slot in slots:
            if slot.line:
                self._heading(slot.line.rstrip(), "h3")
            if slot.statement:
                self._paragraph(slot.statement)

    def _annexes(self) -> None:
        provenance = self._filing.provenance
        if not provenance.annex_tables and not provenance.annexes:
            return
        self._heading("Annex A", "h1")
        for entry in provenance.annexes:
            name = (
                entry.return_code
                if entry.title == entry.return_code
                else f"{entry.title} ({entry.return_code})"
            )
            self._note(
                f"{name} · package {entry.package_id} version {entry.version} "
                f"· content digest {entry.content_digest}"
            )
        for table in provenance.annex_tables:
            self._snapshot_table(table)

    def _attachments(self) -> None:
        """The freeze-time manifest, with the columns the manifest actually has.

        Overrides P1's draft table, whose "Uploaded" column has no counterpart
        in a frozen manifest — a date beside a checksum that was not that
        document's own date is a fabricated fact, even on a working copy.
        """
        manifest = self._filing.provenance.attachments
        self._heading("Documents filed with this report", "h1")
        if not manifest:
            self._paragraph("No documents accompany this report.")
            return
        table = self._out.add_table(rows=1, cols=5, style="Table Grid")
        headers = ("Document", "Title", "Required at", "Size (bytes)", "SHA-256")
        for cell, header in zip(table.rows[0].cells, headers, strict=True):
            cell.paragraphs[0].add_run(header).bold = True
        for entry in manifest:
            cells = table.add_row().cells
            cells[0].paragraphs[0].add_run(entry.kind_title)
            cells[1].paragraphs[0].add_run(entry.title)
            cells[2].paragraphs[0].add_run(fmt.humanize(entry.gate))
            cells[3].paragraphs[0].add_run("" if entry.byte_size is None else str(entry.byte_size))
            cells[4].paragraphs[0].add_run(entry.sha256)

    def _provenance(self) -> None:
        provenance = self._filing.provenance
        doc = self._doc
        self._heading("Provenance", "h1")
        self._paragraph(WORKING_COPY_NOTICE).runs[0].bold = True
        if doc.is_exposure_draft and provenance.exposure_draft_line:
            self._note(provenance.exposure_draft_line)
        for line in (
            f"Return: {provenance.return_code}",
            f"Package: {provenance.package_id} (version {provenance.package_version})",
            f"Reporting date: {provenance.reporting_date}",
            f"Content digest: {provenance.content_digest}",
            f"Snapshot seal: {provenance.snapshot_sha256}",
            f"Review digest: {provenance.review_digest}",
            f"Framework: {doc.framework_title} {doc.framework_version} ({doc.framework_code})",
            f"Framework digest: {doc.framework_digest}",
            f"Generated at: {doc.generated_at.isoformat(timespec='seconds')}",
        ):
            self._paragraph(line)
        if provenance.ai_assisted_paragraphs:
            self._paragraph(
                f"{provenance.ai_assisted_paragraphs} paragraph(s) were drafted with AI "
                "assistance and reviewed by a named officer before the report was frozen."
            )
        else:
            self._paragraph("No paragraph in this report was drafted with AI assistance.")
        self._parameters()
        self._source_runs()
        self._review_record()
        self._figure_sources()

    def _parameters(self) -> None:
        parameters = self._filing.provenance.parameters
        self._heading("Regulatory values applied", "h2")
        if not parameters:
            self._paragraph("This report applies no governed regulatory value.")
            return
        table = self._out.add_table(rows=1, cols=5, style="Table Grid")
        for cell, header in zip(
            table.rows[0].cells, ("Parameter", "Value", "Unit", "Status", "Citation"), strict=True
        ):
            cell.paragraphs[0].add_run(header).bold = True
        for entry in parameters:
            cells = table.add_row().cells
            cells[0].paragraphs[0].add_run(entry.code)
            cells[1].paragraphs[0].add_run(entry.value)
            cells[2].paragraphs[0].add_run(entry.unit)
            cells[3].paragraphs[0].add_run(fmt.humanize(entry.confirmation_status))
            cells[4].paragraphs[0].add_run(entry.citation)

    def _source_runs(self) -> None:
        runs = self._filing.provenance.source_runs
        self._heading("Calculation runs", "h2")
        if not runs:
            self._paragraph("No calculation run is bound to this report.")
            return
        table = self._out.add_table(rows=1, cols=4, style="Table Grid")
        for cell, header in zip(
            table.rows[0].cells, ("Module", "Run", "Input hash", "Engine"), strict=True
        ):
            cell.paragraphs[0].add_run(header).bold = True
        for run in runs:
            cells = table.add_row().cells
            cells[0].paragraphs[0].add_run(run.module)
            cells[1].paragraphs[0].add_run(run.run_id)
            cells[2].paragraphs[0].add_run(run.input_hash)
            cells[3].paragraphs[0].add_run(run.engine_version)

    def _review_record(self) -> None:
        stages = self._filing.provenance.stages
        self._heading("Review and approval", "h2")
        if not stages:
            self._paragraph("No review decision is recorded in this snapshot.")
            return
        table = self._out.add_table(rows=1, cols=5, style="Table Grid")
        for cell, header in zip(
            table.rows[0].cells,
            ("Stage", "Decision", "By", "Designation", "When"),
            strict=True,
        ):
            cell.paragraphs[0].add_run(header).bold = True
        for stage in stages:
            cells = table.add_row().cells
            cells[0].paragraphs[0].add_run(stage.title)
            cells[1].paragraphs[0].add_run(stage.decision)
            # m13: a filed document names people, not user ids.
            cells[2].paragraphs[0].add_run(stage.decided_by_name)
            cells[3].paragraphs[0].add_run(stage.officer_title)
            cells[4].paragraphs[0].add_run(stage.decided_at)

    def _figure_sources(self) -> None:
        self._heading("Figure sources", "h2")
        sourced = [
            block for block in self._doc.blocks.values() if block.provenance or block.source_label
        ]
        if not sourced:
            self._paragraph("No figure table is bound in this report.")
            return
        for block in sourced:
            self._paragraph(block.title).runs[0].bold = True
            detail = [f"Status: {fmt.block_status_label(block.status)}"]
            if block.source_label:
                detail.append(block.source_label)
            if block.as_of is not None:
                detail.append(f"As at {fmt.format_date(block.as_of)}")
            detail.extend(f"{label}: {value}" for label, value in block.provenance)
            self._paragraph(" · ".join(detail))

    # -- assembly ---------------------------------------------------------
    def build(self) -> bytes:
        self._cover()
        self._contents()
        self._attestation()
        self._headline()
        for section in self._doc.sections:
            self._section(section)
        self._checklist()
        self._annexes()
        self._attachments()
        self._provenance()
        self._page_header()
        self._core_properties()
        return self._save()

    def _save(self) -> bytes:
        import io  # noqa: PLC0415 - kept beside its single use

        from app.services.regulatory_reporting.exports.xlsx import (  # noqa: PLC0415
            normalize_zip,
        )

        buffer = io.BytesIO()
        self._out.save(buffer)
        # The one wall clock in an OOXML package, pinned to the package's own
        # generation time so two exports of one sealed package are byte-equal.
        return normalize_zip(
            buffer.getvalue(), self._doc.generated_at.replace(tzinfo=None, microsecond=0)
        )

    def _core_properties(self) -> None:
        super()._core_properties()
        props: Any = self._out.core_properties
        props.title = f"{self._filing.provenance.return_code} — {self._doc.cycle_title}"
        props.comments = WORKING_COPY_NOTICE


def render_working_docx(filing: IcaapFiling) -> bytes:
    """One frozen ICAAP package as the Word working copy. Same input, same bytes."""
    return _IcaapWorkingCopyRenderer(filing).build()


def file_stem(return_code: str) -> str:
    """``ICAAP-REPORT.working`` — never the filing's own object path."""
    return f"{return_code}{FILE_STEM_SUFFIX}"


__all__ = [
    "ARTIFACT_KIND",
    "EXTENSION",
    "FILE_STEM_SUFFIX",
    "MEDIA_TYPE",
    "file_stem",
    "render_working_docx",
]
