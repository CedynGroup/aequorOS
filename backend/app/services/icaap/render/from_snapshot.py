"""The frozen package snapshot, read back as a renderable ICAAP document.

Both filing artifacts — the signed PDF and the Word working copy — are built
from **this one function** and from nothing else. Neither renderer opens a
session, reads a cycle, resolves a framework or recomputes a figure: if a number
is not in the snapshot, it does not reach the page. That is not tidiness. The
snapshot is the sealed evidence an officer certifies and a regulator receives;
a renderer that could reach past it could print a figure the signature does not
cover, and the two artifacts could disagree with each other.

The snapshot this module reads is the one ``services/icaap/snapshot.py`` freezes
(P3-DESIGN §3.7). The shape is a contract between two modules, so it is written
out here rather than inferred:

``snapshot`` (the generic envelope every return shares)
    ``return_code``, ``reporting_date``, ``institution {name, short_name,
    currency, jurisdiction_code}``, ``reporting_period``, ``sections[]``,
    ``totals[]``, ``metadata {generated_at, icaap {…}}``.

``metadata.icaap``
    ``cycle {id, kind, kind_label, fiscal_year, as_of_date, basis, basis_label,
    round, title}``; ``framework {code, title, version, status, digest,
    effective_from}``; ``sections[] {key, letter, order, title, citation_label,
    version_no, doc, doc_sha256, ai_assisted_paragraphs, requirements[]}``;
    ``blocks[] {block_id, block_type, title, status, pin_reason, source_label,
    source_as_of, payload, facts, provenance[]}``; ``attachments[]``;
    ``attachment_requirements[]``; ``stages[]``; ``annexes[]``;
    ``parameters[]``; ``filing {attestation_lines, statements,
    exposure_draft_line, signing_order?}``; ``review_digest``.

**Every read here is tolerant and honest.** A key the freeze did not write
becomes an absence the page states ("Not available", "Not recorded"), never a
substituted value and never an exception that would make a sealed package
unexportable months after it was filed. The one thing that is NOT tolerated is a
snapshot with no ICAAP metadata at all: that is a package of the wrong family
reaching an ICAAP renderer, and rendering something plausible from it would be
worse than refusing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from app.domain.icaap.document import (
    AttachmentLine,
    BlockRender,
    ChecklistLine,
    IcaapDocument,
    RenderBlock,
    SectionRender,
    TableColumn,
    TableRow,
    TableSpec,
)
from app.services.icaap.render import format as fmt

#: What ``metadata.icaap`` is keyed under in the frozen envelope.
METADATA_KEY = "icaap"

#: Printed where the snapshot recorded nothing at all for a scalar fact. Distinct
#: from ``NOT_AVAILABLE``, which is about a FIGURE the bank has not measured;
#: this is about a field the freeze did not write.
NOT_RECORDED = "Not recorded"

#: The prefix ``snapshot.py`` gives an annex section copied in from the sealed
#: annex package (P3-DESIGN §3.7). ``annex_ICAAP-STRESS-APPENDIX2__governance``.
ANNEX_SECTION_PREFIX = "annex_"

#: The envelope section carrying the capital headline.
HEADLINE_SECTION_CODE = "icaap_headline"


class SnapshotPackage(Protocol):
    """The package columns the provenance page prints.

    A protocol, not the ORM class: this module is a renderer input builder and
    has no business importing the model layer, and the tests then need no
    database row to exercise a page.
    """

    @property
    def id(self) -> Any: ...
    @property
    def version(self) -> int: ...
    @property
    def return_code(self) -> str: ...
    @property
    def content_digest(self) -> str | None: ...
    @property
    def snapshot_sha256(self) -> str | None: ...
    @property
    def source_runs(self) -> Sequence[Any]: ...
    @property
    def generated_at(self) -> datetime: ...


class SnapshotError(ValueError):
    """The snapshot handed in is not an ICAAP filing snapshot."""


# --------------------------------------------------------------------------
# What the filing prints that an IcaapDocument has no field for
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SignatureSlot:
    """One officer's block on the attestation page, as the snapshot froze it."""

    role: str
    #: "Approved for submission on behalf of Senior Management: " — the line
    #: printed above the block. Framework text, frozen; never written in Python.
    line: str
    #: The paragraph that officer's signature is attached to.
    statement: str


@dataclass(frozen=True)
class SnapshotTable:
    """A generic envelope section (headline, annex) as a printable table."""

    code: str
    title: str
    spec: TableSpec
    optional: bool = False


@dataclass(frozen=True)
class AttachmentEntry:
    """One row of the freeze-time attachment manifest."""

    kind: str
    kind_title: str
    title: str
    sha256: str
    byte_size: int | None
    media_type: str
    gate: str


@dataclass(frozen=True)
class ParameterEntry:
    """One governed regulatory value the report relied on, with its provenance."""

    code: str
    value: str
    unit: str
    confirmation_status: str
    citation: str


@dataclass(frozen=True)
class SourceRun:
    module: str
    run_id: str
    input_hash: str
    engine_version: str


@dataclass(frozen=True)
class AnnexEntry:
    return_code: str
    title: str
    package_id: str
    version: str
    content_digest: str


@dataclass(frozen=True)
class StageEntry:
    """One recorded review decision, by name and title rather than by user id."""

    seq: int
    title: str
    decision: str
    decided_by_name: str
    officer_title: str
    decided_at: str


@dataclass(frozen=True)
class FilingProvenance:
    """Everything the filing PDF's provenance page and annex print.

    Separate from :class:`IcaapDocument` on purpose: that model is shared with
    P1's draft exports, and widening it would put filing-only fields on a draft
    that has no package, no source runs and no sealed digest.
    """

    return_code: str
    package_id: str
    package_version: int
    content_digest: str
    snapshot_sha256: str
    review_digest: str
    reporting_date: str
    institution_register_digest: str
    exposure_draft_line: str
    ai_assisted_paragraphs: int
    signature_slots: tuple[SignatureSlot, ...] = ()
    headline: SnapshotTable | None = None
    annex_tables: tuple[SnapshotTable, ...] = ()
    annexes: tuple[AnnexEntry, ...] = ()
    attachments: tuple[AttachmentEntry, ...] = ()
    parameters: tuple[ParameterEntry, ...] = ()
    source_runs: tuple[SourceRun, ...] = ()
    stages: tuple[StageEntry, ...] = ()


@dataclass(frozen=True)
class IcaapFiling:
    """The pair every ICAAP artifact renders from."""

    document: IcaapDocument
    provenance: FilingProvenance
    #: The roles that carry a signing block on the attestation page, in ceremony
    #: order. Supplied by the exporter from the policy in force, because the
    #: fields ``pdf_signing`` will create must land on the rules this page draws.
    signing_order: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Small tolerant readers
# --------------------------------------------------------------------------
def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    text = str(value)
    return text if text.strip() else default


def _integer(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return default


def _optional_date(value: Any) -> date | None:
    """A date the snapshot recorded, or ``None`` when it recorded none.

    Distinct from :func:`_as_date`: a block's ``source_as_of`` that cannot be
    parsed must become "no as-of stated", never a substituted epoch date beside
    a figure.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _as_date(value: Any, *, default: date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return default
    return default


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
def _checklist(entry: Mapping[str, Any]) -> tuple[ChecklistLine, ...]:
    lines: list[ChecklistLine] = []
    for raw in _sequence(entry.get("requirements")):
        item = _mapping(raw)
        if not item:
            continue
        reason = item.get("reason")
        lines.append(
            ChecklistLine(
                item_id=_text(item.get("item_id")),
                text=_text(item.get("text")),
                citation_label=_citation_label(item),
                status=_text(item.get("status"), default="open"),
                # REG-ICAAP-008: the reason a requirement was set aside travels
                # with the filed document, not only with the screen.
                reason=None if reason is None else _text(reason) or None,
            )
        )
    return tuple(lines)


def _citation_label(entry: Mapping[str, Any]) -> str:
    """The reference a reader can look up, however the freeze recorded it.

    ``citation_label`` is what the snapshot builder should write — the label is
    resolved through the framework's own document short labels, and no regulator
    name may be written in Python (jurisdiction neutrality). The other shapes are
    accepted so a snapshot frozen before that settled still renders a reference
    rather than a blank cell.
    """
    label = entry.get("citation_label")
    if label:
        return _text(label)
    citation = entry.get("citation")
    if isinstance(citation, str):
        return _text(citation)
    mapping = _mapping(citation)
    if not mapping:
        return ""
    resolved = mapping.get("label")
    if resolved:
        return _text(resolved)
    doc = _text(mapping.get("doc"))
    ref = _text(mapping.get("ref"))
    if doc and ref:
        return f"{doc} ¶{ref}"
    return doc or ref


def _section_renders(entries: Sequence[Any]) -> tuple[SectionRender, ...]:
    from app.domain.icaap import prosemirror  # noqa: PLC0415 - pure domain, imported lazily

    renders: list[SectionRender] = []
    for raw in sorted(entries, key=_section_order):
        entry = _mapping(raw)
        if not entry:
            continue
        doc = _mapping(entry.get("doc"))
        blocks: tuple[RenderBlock, ...] = prosemirror.to_render_blocks(doc) if doc else ()
        version_no = entry.get("version_no")
        renders.append(
            SectionRender(
                key=_text(entry.get("key")),
                letter=_text(entry.get("letter")),
                title=_text(entry.get("title")),
                citation_label=_citation_label(entry),
                source_status=_text(entry.get("source_status"), default="sourced"),
                content_label=(
                    fmt.content_label(committed_version=_integer(version_no, default=0) or None)
                    if version_no is not None
                    else ""
                ),
                blocks=blocks,
                checklist=_checklist(entry),
                # The filed document never substitutes prose of its own for an
                # empty section; it says the section carries no text.
                empty_note=None if blocks else "This section carries no text in the filed report.",
            )
        )
    return tuple(renders)


def _section_order(raw: Any) -> tuple[int, str]:
    entry = _mapping(raw)
    return (_integer(entry.get("order"), default=0), _text(entry.get("key")))


# --------------------------------------------------------------------------
# Blocks and facts
# --------------------------------------------------------------------------
def _block_provenance(entry: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    recorded = entry.get("provenance")
    if isinstance(recorded, list):
        pairs: list[tuple[str, str]] = []
        for item in recorded:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((_text(item[0]), _text(item[1])))
            elif isinstance(item, Mapping):
                pairs.append((_text(item.get("label")), _text(item.get("value"))))
        return tuple(pairs)
    lines: list[tuple[str, str]] = []
    source_key = entry.get("source_key")
    if source_key:
        lines.append(("Source", _text(source_key)))
    for key in ("run_id", "input_hash", "engine_version", "package_id", "signoff_id", "plan_id"):
        value = entry.get(key)
        if value:
            lines.append((fmt.humanize(key), _text(value)))
    return tuple(lines)


def _block_status(entry: Mapping[str, Any]) -> str:
    """What the note beside this table says, derived from the frozen binding.

    The freeze refuses a stale block outright, so a block in a filed report is
    either current, deliberately pinned (with the reason that was given), or —
    only on a report that was allowed to freeze without it — unbound. The status
    is derived rather than stored because a stored one could disagree with the
    payload sitting next to it.
    """
    recorded = entry.get("status")
    if recorded:
        return str(recorded)
    if entry.get("pin_reason"):
        return "pinned"
    return "fresh" if isinstance(entry.get("payload"), Mapping) else "unbound"


def _block_renders(entries: Sequence[Any]) -> tuple[dict[str, BlockRender], dict[str, Any]]:
    renders: dict[str, BlockRender] = {}
    facts: dict[str, Any] = {}
    for raw in entries:
        entry = _mapping(raw)
        block_id = _text(entry.get("block_id"))
        if not block_id:
            continue
        payload = entry.get("payload")
        pin_reason = entry.get("pin_reason")
        renders[block_id] = fmt.block_render(
            block_id=block_id,
            payload=payload if isinstance(payload, Mapping) else None,
            status=_block_status(entry),
            pin_reason=None if pin_reason is None else _text(pin_reason) or None,
            fallback_title=_text(
                entry.get("title"),
                default=fmt.humanize(_text(entry.get("block_type"), default="figures")),
            ),
            source_as_of=_optional_date(entry.get("source_as_of")),
            provenance=_block_provenance(entry),
        )
        facts[block_id] = _mapping(entry.get("facts"))
    return renders, facts


# --------------------------------------------------------------------------
# Generic envelope sections (headline, annexes)
# --------------------------------------------------------------------------
_SNAPSHOT_TABLE_COLUMNS = (
    TableColumn(key="description", label="Item", kind="text"),
    TableColumn(key="value", label="Value", kind="text"),
    TableColumn(key="unit", label="Unit", kind="text"),
)


def _snapshot_table(section: Mapping[str, Any]) -> SnapshotTable:
    """One envelope section as a printable table.

    Values are printed exactly as the snapshot froze them. Nothing is reformatted
    against a unit the renderer guessed: the generator that wrote the row already
    decided how the figure reads, and re-deriving it here is how two artifacts of
    one package come to show different numbers.
    """
    rows: list[TableRow] = []
    for raw in _sequence(section.get("rows")):
        row = _mapping(raw)
        if not row:
            continue
        rows.append(
            TableRow(
                cells={
                    "description": _text(row.get("description"), default=_text(row.get("code"))),
                    "value": _text(row.get("value")),
                    "unit": _text(row.get("unit")),
                }
            )
        )
    total = _mapping(section.get("total"))
    if total:
        rows.append(
            TableRow(
                cells={
                    "description": _text(total.get("description"), default="Total"),
                    "value": _text(total.get("value")),
                    "unit": _text(total.get("unit")),
                },
                emphasis="total",
            )
        )
    code = _text(section.get("code"))
    title = _text(section.get("title"), default=fmt.humanize(code))
    return SnapshotTable(
        code=code,
        title=title,
        spec=TableSpec(key=code, title="", columns=_SNAPSHOT_TABLE_COLUMNS, rows=tuple(rows)),
        optional=bool(section.get("optional")),
    )


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------
def _attachments(
    entries: Sequence[Any], requirements: Sequence[Any]
) -> tuple[AttachmentEntry, ...]:
    titles = {
        _text(_mapping(raw).get("kind")): _text(_mapping(raw).get("title"))
        for raw in requirements
        if _mapping(raw)
    }
    manifest: list[AttachmentEntry] = []
    for raw in entries:
        entry = _mapping(raw)
        kind = _text(entry.get("kind"))
        if not kind:
            continue
        byte_size = entry.get("byte_size")
        manifest.append(
            AttachmentEntry(
                kind=kind,
                kind_title=_text(
                    entry.get("kind_title"), default=titles.get(kind) or fmt.humanize(kind)
                ),
                title=_text(entry.get("title")),
                sha256=_text(entry.get("sha256")),
                byte_size=None if byte_size is None else _integer(byte_size, default=0),
                media_type=_text(entry.get("media_type")),
                gate=_text(entry.get("gate")),
            )
        )
    return tuple(manifest)


def _attachment_lines(
    manifest: Sequence[AttachmentEntry], *, recorded_on: date
) -> tuple[AttachmentLine, ...]:
    """The manifest in the shared document model, for the DOCX's own table.

    ``recorded_on`` is the date the MANIFEST was sealed (the package's
    ``generated_at``), not the date each document was uploaded — the freeze
    manifest does not carry per-attachment upload dates, and inventing one would
    put a fabricated date beside a checksum. The filing PDF prints the manifest
    from :class:`AttachmentEntry` directly, with no date column at all.
    """
    return tuple(
        AttachmentLine(
            kind_title=entry.kind_title,
            title=entry.title,
            sha256_prefix=entry.sha256[:16],
            uploaded_on=recorded_on,
        )
        for entry in manifest
    )


def _parameters(entries: Sequence[Any]) -> tuple[ParameterEntry, ...]:
    resolved: list[ParameterEntry] = []
    for raw in entries:
        entry = _mapping(raw)
        code = _text(entry.get("code"), default=_text(entry.get("param_code")))
        if not code:
            continue
        value_json = entry.get("value_json")
        resolved.append(
            ParameterEntry(
                code=code,
                # A parameter the control plane had no approved row for is
                # printed as unresolved. It is never printed as a number, and
                # never omitted: the report relied on its absence.
                value=(
                    _text(entry.get("value"))
                    or _json_value(value_json)
                    or ("Not resolved" if entry.get("resolved") is False else NOT_RECORDED)
                ),
                unit=_text(entry.get("unit")),
                confirmation_status=_text(entry.get("confirmation_status")),
                citation=_text(entry.get("citation"), default=_text(entry.get("source_citation"))),
            )
        )
    return tuple(resolved)


def _json_value(value: Any) -> str:
    """A ``value_json`` parameter (a date, a small object) as one printable line."""
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {value[key]}" for key in sorted(value))
    return str(value)


def _source_runs(entries: Sequence[Any]) -> tuple[SourceRun, ...]:
    runs: list[SourceRun] = []
    for raw in entries:
        entry = _mapping(raw)
        if not entry:
            continue
        runs.append(
            SourceRun(
                module=_text(entry.get("module"), default=NOT_RECORDED),
                run_id=_text(entry.get("run_id"), default=NOT_RECORDED),
                input_hash=_text(entry.get("input_hash"), default=NOT_RECORDED),
                engine_version=_text(entry.get("engine_version"), default=NOT_RECORDED),
            )
        )
    return tuple(runs)


def _annexes(entries: Sequence[Any]) -> tuple[AnnexEntry, ...]:
    resolved: list[AnnexEntry] = []
    for raw in entries:
        entry = _mapping(raw)
        code = _text(entry.get("return_code"))
        if not code:
            continue
        resolved.append(
            AnnexEntry(
                return_code=code,
                title=_text(entry.get("title"), default=code),
                package_id=_text(entry.get("package_id"), default=NOT_RECORDED),
                version=_text(entry.get("version"), default=NOT_RECORDED),
                content_digest=_text(entry.get("content_digest"), default=NOT_RECORDED),
            )
        )
    return tuple(resolved)


def _stages(entries: Sequence[Any]) -> tuple[StageEntry, ...]:
    """One row per recorded DECISION, flattened out of the stage chain.

    The snapshot nests decisions under their stage (a stage can be decided more
    than once across rounds). The filed page prints the decisions, because a
    stage with no decision is not evidence of anything.
    """
    resolved: list[StageEntry] = []
    for raw in entries:
        stage = _mapping(raw)
        if not stage:
            continue
        seq = _integer(stage.get("seq"), default=0)
        title = _text(stage.get("title"), default=_text(stage.get("stage_key")))
        for decision_raw in _sequence(stage.get("decisions")):
            decision = _mapping(decision_raw)
            if not decision:
                continue
            resolved.append(
                StageEntry(
                    seq=seq,
                    title=title,
                    decision=fmt.humanize(_text(decision.get("decision"))),
                    # m13: a filed document names people, not user ids.
                    decided_by_name=_text(
                        decision.get("decided_by_name"), default=NOT_RECORDED
                    ),
                    officer_title=_text(decision.get("officer_title")),
                    decided_at=_text(decision.get("decided_at")),
                )
            )
    return tuple(resolved)


def _signature_slots(
    filing: Mapping[str, Any], signing_order: Sequence[str]
) -> tuple[SignatureSlot, ...]:
    lines = _mapping(filing.get("attestation_lines"))
    statements = _mapping(filing.get("statements"))
    return tuple(
        SignatureSlot(
            role=role,
            line=_text(lines.get(role)),
            statement=_text(statements.get(role)),
        )
        for role in signing_order
    )


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------
def document_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    package: SnapshotPackage,
    watermark: str | None = None,
) -> IcaapDocument:
    """The renderable document for one frozen ICAAP package (P3-DESIGN §7.1)."""
    return filing_from_snapshot(snapshot, package=package, watermark=watermark).document


def filing_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    package: SnapshotPackage,
    signing_order: Sequence[str] = (),
    watermark: str | None = None,
) -> IcaapFiling:
    """The document AND the filing provenance, from the sealed snapshot alone.

    ``signing_order`` names the officers whose blocks the attestation page rules.
    It is the ONE input that does not come from the snapshot, and it must not:
    the blocks this page draws have to match the AcroForm fields
    ``pdf_signing.prepare_signature_fields`` is about to create for the ceremony
    in force, and that ceremony is the policy's answer at signing time, not the
    freeze's. An empty order draws no blocks, which is what an unsigned reading
    copy of the report should show.
    """
    icaap = _mapping(_mapping(snapshot.get("metadata")).get(METADATA_KEY))
    if not icaap:
        raise SnapshotError(
            "This package snapshot carries no ICAAP metadata, so it is not an ICAAP "
            "filing snapshot. Rendering the ICAAP report from it would print a "
            "document assembled from a different return's figures."
        )

    institution = _mapping(snapshot.get("institution"))
    cycle = _mapping(icaap.get("cycle"))
    framework = _mapping(icaap.get("framework"))
    filing = _mapping(icaap.get("filing"))

    reporting_date = _text(snapshot.get("reporting_date"))
    as_of = _as_date(
        cycle.get("as_of_date"),
        default=_as_date(reporting_date, default=package.generated_at.date()),
    )
    basis = _text(cycle.get("basis"), default="solo")
    cycle_kind = _text(cycle.get("kind"), default="annual")
    currency = _text(institution.get("currency"))

    sections = _section_renders(_sequence(icaap.get("sections")))
    block_renders, block_facts = _block_renders(_sequence(icaap.get("blocks")))
    manifest = _attachments(
        _sequence(icaap.get("attachments")), _sequence(icaap.get("attachment_requirements"))
    )

    envelope_sections = [_mapping(raw) for raw in _sequence(snapshot.get("sections"))]
    headline = next(
        (
            _snapshot_table(section)
            for section in envelope_sections
            if _text(section.get("code")) == HEADLINE_SECTION_CODE
        ),
        None,
    )
    annex_tables = tuple(
        _snapshot_table(section)
        for section in envelope_sections
        if _text(section.get("code")).startswith(ANNEX_SECTION_PREFIX)
    )

    document = IcaapDocument(
        institution_name=_text(institution.get("name"), default=NOT_RECORDED),
        institution_short_name=_text(
            institution.get("short_name"), default=_text(institution.get("name"))
        ),
        cycle_title=_text(cycle.get("title"), default=_text(snapshot.get("return_code"))),
        fiscal_year=_integer(cycle.get("fiscal_year"), default=as_of.year),
        as_of=as_of,
        basis=basis,
        basis_label=_text(cycle.get("basis_label"), default=fmt.basis_label(basis)),
        cycle_kind=cycle_kind,
        cycle_kind_label=_text(
            cycle.get("kind_label"), default=fmt.cycle_kind_label(cycle_kind)
        ),
        framework_code=_text(framework.get("code"), default=NOT_RECORDED),
        framework_title=_text(framework.get("title"), default=NOT_RECORDED),
        framework_version=_text(framework.get("version")),
        framework_status=_text(framework.get("status")),
        framework_digest=_text(framework.get("digest"), default=NOT_RECORDED),
        # Frozen at the freeze; never resolved live (jurisdiction neutrality —
        # no regulator's name is written in Python).
        regulator_short=_text(icaap.get("regulator_short")),
        currency=currency,
        generated_at=package.generated_at,
        sections=sections,
        blocks=block_renders,
        fact_text=fmt.fact_text_map(block_facts, fallback_currency=currency or None),
        attachments=_attachment_lines(manifest, recorded_on=package.generated_at.date()),
        # A filed report states no readiness: readiness is what had to be cleared
        # before the freeze, and the freeze happened.
        readiness_summary=(),
        watermark=watermark,
        is_rehearsal=cycle_kind == "rehearsal",
    )

    provenance = FilingProvenance(
        return_code=_text(snapshot.get("return_code"), default=package.return_code),
        package_id=str(package.id),
        package_version=package.version,
        content_digest=_text(package.content_digest, default=NOT_RECORDED),
        snapshot_sha256=_text(package.snapshot_sha256, default=NOT_RECORDED),
        review_digest=_text(icaap.get("review_digest"), default=NOT_RECORDED),
        reporting_date=reporting_date,
        institution_register_digest=_text(
            _mapping(icaap.get("institution_profile")).get("register_state_digest"),
            default=NOT_RECORDED,
        ),
        exposure_draft_line=_text(filing.get("exposure_draft_line")),
        ai_assisted_paragraphs=sum(
            _integer(_mapping(raw).get("ai_assisted_paragraphs"), default=0)
            for raw in _sequence(icaap.get("sections"))
        ),
        signature_slots=_signature_slots(filing, signing_order),
        headline=headline,
        annex_tables=annex_tables,
        annexes=_annexes(_sequence(icaap.get("annexes"))),
        attachments=manifest,
        parameters=_parameters(_sequence(icaap.get("parameters"))),
        source_runs=_source_runs(list(package.source_runs)),
        stages=_stages(_sequence(icaap.get("stages"))),
    )

    return IcaapFiling(
        document=document,
        provenance=provenance,
        signing_order=tuple(signing_order),
    )


__all__ = [
    "ANNEX_SECTION_PREFIX",
    "HEADLINE_SECTION_CODE",
    "METADATA_KEY",
    "NOT_RECORDED",
    "AnnexEntry",
    "AttachmentEntry",
    "FilingProvenance",
    "IcaapFiling",
    "ParameterEntry",
    "SignatureSlot",
    "SnapshotError",
    "SnapshotPackage",
    "SnapshotTable",
    "SourceRun",
    "StageEntry",
    "document_from_snapshot",
    "filing_from_snapshot",
]
