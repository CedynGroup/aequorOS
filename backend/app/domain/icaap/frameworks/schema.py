"""The ICAAP framework document schema (pure domain).

A framework is the regulator's ICAAP instrument expressed as data: its printed
section headings, the checklist of what each section must say, the risk
categories, the attachments, the review stages and the filing deadline. The
platform holds no country in code — swapping jurisdiction means adding a JSON
file under ``frameworks/<jurisdiction>/``, never a branch.

Two rules shape everything below.

**Nothing is invented.** Every requirement item carries at least one citation to
a declared document, the text is a paraphrase (never a quotation, never longer
than 280 characters), and a section whose primary text has not been read is
marked ``pending_primary_text`` so readiness can refuse a real filing until it
is. ``SOURCES.md`` beside each framework is the manifest a test holds the
citations against.

**No regulatory number is in the data** (founder directive D-024). The filing
deadline, the disclosure period, the materiality thresholds and any figure a
requirement mentions are *parameter references* resolved at render time through
the governed control plane, so staff change them in the console without a code
or data change. That is why ``months_after_fye_param`` is a code and not a 3.

The parser is deliberately strict and reports a JSON path with every refusal: a
typo must fail loudly at load, not silently drop a requirement from a
regulator's checklist.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from app.domain.icaap.blocks import BLOCK_TYPES, P1_BLOCK_TYPES
from app.domain.icaap.methods import METHOD_KEYS, NOT_CAPITALISED

SCHEMA_ID = "aequoros-icaap-framework-v1"

FrameworkStatus = Literal["exposure_draft", "final"]
SourceStatus = Literal["sourced", "pending_primary_text"]
AttachmentGate = Literal["freeze", "submission", "optional", "per_block"]
P29Class = Literal["pillar1", "pillar1_not_fully_captured", "outside_pillar1", "external"]
KeyRelation = Literal["equivalent", "partial", "split", "merge", "unmapped"]
StageDecision = Literal["prepare", "review", "approve", "attest"]
TitleStatus = Literal["verbatim", "partial", "platform"]

_FRAMEWORK_STATUSES = frozenset({"exposure_draft", "final"})
_SOURCE_STATUSES = frozenset({"sourced", "pending_primary_text"})
_ATTACHMENT_GATES = frozenset({"freeze", "submission", "optional", "per_block"})
_P29_CLASSES = frozenset({"pillar1", "pillar1_not_fully_captured", "outside_pillar1", "external"})
_KEY_RELATIONS = frozenset({"equivalent", "partial", "split", "merge", "unmapped"})
_STAGE_DECISIONS = frozenset({"prepare", "review", "approve", "attest"})
_TITLE_STATUSES = frozenset({"verbatim", "partial", "platform"})
_WORKFLOW_TOKENS = frozenset({"stage_decisions", "basis_companion_cycle", "board_signature"})
_CONDITIONS = frozenset({"has_subsidiaries", "group_member", "foreign_bank_subsidiary"})
_CYCLE_KINDS = frozenset({"annual", "material_change", "regulator_request", "rehearsal"})

_SECTION_KEY = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
_ITEM_ID = re.compile(r"^[a-z0-9_]{2,40}$")
_PARAM_CODE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
_PARAM_PLACEHOLDER = re.compile(r"\{param:([a-z][a-z0-9_]{2,60})\}")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_DAY = re.compile(r"^\d{2}-\d{2}$")
_ITEM_TEXT_MAX = 280

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "code",
        "jurisdiction",
        "regulator",
        "title",
        "short_title",
        "version",
        "status",
        "effective_from",
        "effective_from_citations",
        "first_as_of_date",
        "first_as_of_basis",
        "first_as_of_note",
        "institution_classes",
        "filing_return_code",
        "supersedes",
        "section_key_map",
        "cross_framework_section_map",
        "documents",
        "related_instruments",
        "deadline",
        "document_citations",
        "sections",
        "table5_rows",
        "table5_citation",
        "risk_categories",
        "attachments",
        "stages",
        "stages_source_status",
        "material_change_triggers",
        "materiality_matrix",
        "disclosure",
        "filing",
        "notes",
    }
)

#: The signature roles a filing block may address. ``board`` is built but off
#: by default (D-043); wording for it still ships so a bank that enables the
#: slot gets an attestation sentence rather than a blank line.
_FILING_ROLES = frozenset({"preparer", "approver", "board"})
_FILING_PURPOSES = frozenset({"annual", "update", "disclosure"})


class FrameworkSchemaError(ValueError):
    """A framework document the parser refuses, with the JSON path."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _fail(path: str, message: str) -> FrameworkSchemaError:
    return FrameworkSchemaError(path, message)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    doc: str
    ref: str

    @property
    def cite_id(self) -> str:
        return f"{self.doc}:{self.ref}"


@dataclass(frozen=True)
class DocumentRef:
    id: str
    title: str
    short_label: str
    issuer: str
    issued: str
    status: FrameworkStatus
    url: str | None


@dataclass(frozen=True)
class RelatedInstrument:
    id: str
    title: str
    issued: str | None
    status: FrameworkStatus
    named_by: tuple[Citation, ...]


@dataclass(frozen=True)
class RequirementItem:
    id: str
    text: str
    citations: tuple[Citation, ...]
    evidence: tuple[str, ...]
    waivable: bool
    applies_when: str | None
    #: Governed parameter codes the text quotes as ``{param:<code>}`` (D-024 A4).
    param_refs: tuple[str, ...] = ()

    @property
    def placeholders(self) -> tuple[str, ...]:
        return tuple(_PARAM_PLACEHOLDER.findall(self.text))


@dataclass(frozen=True)
class SectionDef:
    key: str
    letter: str
    order: int
    title: str
    citation: Citation
    source_status: SourceStatus
    guidance: str
    data_blocks: tuple[str, ...]
    ai_draftable: bool
    public_disclosure: bool
    requirements: tuple[RequirementItem, ...]


@dataclass(frozen=True)
class Table5Row:
    key: str
    label: str
    order: int


@dataclass(frozen=True)
class MethodMandate:
    """A method that becomes mandatory from a governed as-of date (A6)."""

    method: str
    mandatory_from_param: str
    replaces: str | None


@dataclass(frozen=True)
class Pillar2Component:
    key: str
    table5_row: str | None
    p29_class: P29Class
    p29_basis: Literal["sourced", "inferred"]
    allowed_methods: tuple[str, ...]
    method_mandates: tuple[MethodMandate, ...] = ()


@dataclass(frozen=True)
class RiskCategoryDef:
    key: str
    number: int
    title: str
    citation: Citation
    title_status: TitleStatus
    pillar1_coverage: Literal["full", "partial", "none"]
    custom: bool
    components: tuple[Pillar2Component, ...]
    sub_requirements: tuple[RequirementItem, ...]


@dataclass(frozen=True)
class AttachmentRequirement:
    kind: str
    title: str
    citations: tuple[Citation, ...]
    gate: AttachmentGate
    min_count: int
    max_count: int | None
    relaxable_by_signing_policy: bool
    media_types: tuple[str, ...]
    applies_when: str | None
    section_keys: tuple[str, ...]


@dataclass(frozen=True)
class StageTemplate:
    seq: int
    key: str
    title: str
    decision: StageDecision
    default_officer_titles: tuple[str, ...]
    freeze_on_approve: bool
    citations: tuple[Citation, ...]


def _add_months(anchor: date, months: int) -> date:
    """Add months with a month-end clamp: 31 Dec + 3 months is 31 March."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass(frozen=True)
class DeadlineSpec:
    """When the report is due, in terms of a governed number of months (A1)."""

    as_of: Literal["fy_end"]
    fy_end_month_day: str
    months_after_fye_param: str
    citations: tuple[Citation, ...]

    def fy_end(self, fiscal_year: int) -> date:
        month, day = (int(part) for part in self.fy_end_month_day.split("-"))
        return date(fiscal_year, month, min(day, calendar.monthrange(fiscal_year, month)[1]))

    def due_date(self, as_of: date, months_after_fy_end: int) -> date:
        """The filing date for ``as_of``, given the resolved governed months.

        The caller resolves ``months_after_fye_param`` through the parameter
        control plane and passes the value, so changing the deadline in the
        console takes effect without a new framework version.
        """
        if months_after_fy_end < 0:
            msg = "months after year-end cannot be negative"
            raise ValueError(msg)
        return _add_months(as_of, months_after_fy_end)


@dataclass(frozen=True)
class MaterialChangeTrigger:
    code: str
    label: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class MaterialityLevel:
    score: int
    key: str
    label: str


@dataclass(frozen=True)
class MaterialityMatrix:
    """The scoring scale. Every threshold is a parameter reference (A8)."""

    source_status: Literal["platform_default"]
    likelihood_levels: tuple[MaterialityLevel, ...]
    impact_levels: tuple[MaterialityLevel, ...]
    rating_bands_param: str
    material_min_score_param: str
    material_min_impact_param: str
    note: str | None = None


@dataclass(frozen=True)
class DisclosureSpec:
    citations: tuple[Citation, ...]
    source_status: Literal["sourced", "interpretation_required"]
    channel: str
    submit_months_after_fye_param: str | None
    trigger: Literal["annual", "annual_and_each_update"]
    content_selection: Literal["bank_selected", "prescribed"]
    never_public_block_types: tuple[str, ...]
    requires_disclosure_approval: bool
    note: str | None = None


@dataclass(frozen=True)
class FilingAnnex:
    """A separate return that is filed INSIDE this one, not beside it."""

    return_code: str
    required: bool
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class FilingAttestation:
    """The wording each signer signs under, frozen into the filed document.

    Platform-default wording by default: the attestation sentence a bank's
    officers put their name to is the platform's, and only a framework under a
    jurisdiction directory may name a regulator in it. ``source_status`` says
    which it is, and the filing PDF prints that provenance.
    """

    source_status: Literal["platform_default", "sourced"]
    #: role -> the line printed above the signature block.
    lines: tuple[tuple[str, str], ...]
    #: role -> the statement the signer attests to.
    statements: tuple[tuple[str, str], ...]

    def line_for(self, role: str) -> str | None:
        return dict(self.lines).get(role)

    def statement_for(self, role: str) -> str | None:
        return dict(self.statements).get(role)

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(role for role, _ in self.lines)


@dataclass(frozen=True)
class StressValidationSpec:
    """Which governed parameters the annex's stress rules are measured against."""

    horizon_param: str | None
    severe_min_param: str | None


@dataclass(frozen=True)
class FilingSpec:
    """How this framework's assessment becomes a filing (P3).

    Optional, and absent for a framework published for reference only: a
    jurisdiction whose return family does not exist yet declares no filing
    block, and freeze refuses with ``filing_not_available_for_framework``
    rather than inventing a return code.
    """

    return_codes: tuple[tuple[str, str], ...]
    annexes: tuple[FilingAnnex, ...]
    attestation: FilingAttestation
    stress_validation: StressValidationSpec | None
    exposure_draft_line: str | None = None

    def return_code_for(self, purpose: str) -> str | None:
        return dict(self.return_codes).get(purpose)

    @property
    def required_annexes(self) -> tuple[FilingAnnex, ...]:
        return tuple(annex for annex in self.annexes if annex.required)


@dataclass(frozen=True)
class SectionKeyMapping:
    from_key: str
    to_key: str | None
    relation: KeyRelation


@dataclass(frozen=True)
class CrossFrameworkMap:
    """Equivalence to another jurisdiction's framework (A5).

    Version lineage lives in ``section_key_map`` and drives ``plan_rebase``.
    This is a different relation — a group filing in two countries wants to see
    which section answers which — and is deliberately not accepted by rebase.
    """

    framework: str
    direction: Literal["to", "from"]
    mappings: tuple[SectionKeyMapping, ...]


@dataclass(frozen=True)
class FrameworkNote:
    code: str
    text: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Framework:
    code: str
    jurisdiction: str
    regulator: str
    title: str
    short_title: str
    version: str
    status: FrameworkStatus
    effective_from: date
    first_as_of_date: date
    first_as_of_basis: Literal["sourced", "inferred"]
    first_as_of_note: str | None
    institution_classes: tuple[str, ...]
    filing_return_code: str | None
    supersedes: tuple[str, str] | None
    section_key_map: tuple[SectionKeyMapping, ...]
    cross_framework_section_map: tuple[CrossFrameworkMap, ...]
    documents: tuple[DocumentRef, ...]
    related_instruments: tuple[RelatedInstrument, ...]
    deadline: DeadlineSpec
    sections: tuple[SectionDef, ...]
    table5_rows: tuple[Table5Row, ...]
    table5_citation: Citation | None
    risk_categories: tuple[RiskCategoryDef, ...]
    attachments: tuple[AttachmentRequirement, ...]
    stages: tuple[StageTemplate, ...]
    stages_source_status: str
    material_change_triggers: tuple[MaterialChangeTrigger, ...]
    materiality: MaterialityMatrix
    disclosure: DisclosureSpec | None
    notes: tuple[FrameworkNote, ...]
    digest: str
    #: P3. ``None`` for a framework published for reference only.
    filing: FilingSpec | None = None
    #: ``effective_from_citations`` + ``document_citations``: cited by the
    #: framework as a whole rather than by any one section.
    standalone_citations: tuple[Citation, ...] = ()

    def section(self, key: str) -> SectionDef:
        for section in self.sections:
            if section.key == key:
                return section
        msg = f"{self.code} {self.version} has no section {key!r}"
        raise KeyError(msg)

    def has_section(self, key: str) -> bool:
        return any(section.key == key for section in self.sections)

    def item(self, item_id: str) -> RequirementItem:
        for item in self.all_items():
            if item.id == item_id:
                return item
        msg = f"{self.code} {self.version} has no requirement {item_id!r}"
        raise KeyError(msg)

    def all_items(self) -> tuple[RequirementItem, ...]:
        return (
            *(item for section in self.sections for item in section.requirements),
            *(item for category in self.risk_categories for item in category.sub_requirements),
        )

    def attachment(self, kind: str) -> AttachmentRequirement:
        for attachment in self.attachments:
            if attachment.kind == kind:
                return attachment
        msg = f"{self.code} {self.version} has no attachment {kind!r}"
        raise KeyError(msg)

    def all_citations(self) -> frozenset[Citation]:
        found: set[Citation] = set()
        if self.table5_citation is not None:
            found.add(self.table5_citation)
        for section in self.sections:
            found.add(section.citation)
        for category in self.risk_categories:
            found.add(category.citation)
        for item in self.all_items():
            found.update(item.citations)
        for attachment in self.attachments:
            found.update(attachment.citations)
        for stage in self.stages:
            found.update(stage.citations)
        for trigger in self.material_change_triggers:
            found.update(trigger.citations)
        for note in self.notes:
            found.update(note.citations)
        for instrument in self.related_instruments:
            found.update(instrument.named_by)
        found.update(self.deadline.citations)
        if self.disclosure is not None:
            found.update(self.disclosure.citations)
        if self.filing is not None:
            for annex in self.filing.annexes:
                found.update(annex.citations)
        found.update(self.standalone_citations)
        return frozenset(found)

    def param_refs(self) -> frozenset[str]:
        """Every governed parameter code this framework depends on (D-024)."""
        codes: set[str] = {
            self.deadline.months_after_fye_param,
            self.materiality.rating_bands_param,
            self.materiality.material_min_score_param,
            self.materiality.material_min_impact_param,
        }
        if self.disclosure is not None and self.disclosure.submit_months_after_fye_param:
            codes.add(self.disclosure.submit_months_after_fye_param)
        if self.filing is not None and self.filing.stress_validation is not None:
            codes.update(
                code
                for code in (
                    self.filing.stress_validation.horizon_param,
                    self.filing.stress_validation.severe_min_param,
                )
                if code
            )
        for item in self.all_items():
            codes.update(item.param_refs)
        for category in self.risk_categories:
            for component in category.components:
                for mandate in component.method_mandates:
                    codes.add(mandate.mandatory_from_param)
        return frozenset(codes)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _at(path: str, key: str) -> str:
    """``sections[0].key`` — and a bare ``key`` at the top level, not ``.key``."""
    return f"{path}.{key}" if path else key


def _require(obj: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(obj, dict):
        raise _fail(path, "expected an object")
    return obj


def _string(obj: Mapping[str, Any], key: str, path: str, *, allow_empty: bool = False) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise _fail(_at(path, key), "expected a non-empty string")
    return value


def _optional_string(obj: Mapping[str, Any], key: str, path: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(_at(path, key), "expected a string or null")
    return value


def _bool(obj: Mapping[str, Any], key: str, path: str) -> bool:
    value = obj.get(key)
    if not isinstance(value, bool):
        raise _fail(_at(path, key), "expected true or false")
    return value


def _int(obj: Mapping[str, Any], key: str, path: str) -> int:
    value = obj.get(key)
    if type(value) is not int:
        raise _fail(_at(path, key), "expected an integer")
    return value


def _list(obj: Mapping[str, Any], key: str, path: str) -> Sequence[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        raise _fail(_at(path, key), "expected a list")
    return value


def _date(obj: Mapping[str, Any], key: str, path: str) -> date:
    value = _string(obj, key, path)
    if not _ISO_DATE.match(value):
        raise _fail(_at(path, key), "expected an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _fail(_at(path, key), f"is not a real date: {exc}") from exc


def _literal(obj: Mapping[str, Any], key: str, path: str, allowed: frozenset[str]) -> str:
    value = _string(obj, key, path)
    if value not in allowed:
        raise _fail(_at(path, key), f"must be one of {sorted(allowed)}")
    return value


def _param_code(obj: Mapping[str, Any], key: str, path: str) -> str:
    value = _string(obj, key, path)
    if not _PARAM_CODE.match(value):
        raise _fail(_at(path, key), "is not a governed parameter code")
    return value


def _citation(raw: Any, path: str, documents: frozenset[str]) -> Citation:
    obj = _require(raw, path)
    unknown = set(obj) - {"doc", "ref"}
    if unknown:
        raise _fail(path, f"unknown citation key(s) {sorted(unknown)}")
    doc = _string(obj, "doc", path)
    ref = _string(obj, "ref", path)
    if doc not in documents:
        raise _fail(f"{path}.doc", f"{doc!r} is not a declared document")
    return Citation(doc=doc, ref=ref)


def _citations(
    obj: Mapping[str, Any], key: str, path: str, documents: frozenset[str], *, minimum: int = 0
) -> tuple[Citation, ...]:
    raw = _list(obj, key, path)
    citations = tuple(
        _citation(entry, f"{path}.{key}[{index}]", documents) for index, entry in enumerate(raw)
    )
    if len(citations) < minimum:
        raise _fail(_at(path, key), f"needs at least {minimum} citation(s)")
    return citations


def _condition(value: str | None, path: str) -> str | None:
    if value is None:
        return None
    if value in _CONDITIONS:
        return value
    if value.startswith("cycle_kind:") and value.split(":", 1)[1] in _CYCLE_KINDS:
        return value
    raise _fail(path, f"{value!r} is not a known applicability condition")


def _evidence(raw: Sequence[Any], path: str, attachment_kinds: frozenset[str]) -> tuple[str, ...]:
    tokens: list[str] = []
    for index, entry in enumerate(raw):
        where = f"{path}[{index}]"
        if not isinstance(entry, str):
            raise _fail(where, "expected a string")
        if entry == "narrative":
            tokens.append(entry)
        elif entry.startswith("block:"):
            block_type = entry.split(":", 1)[1]
            if block_type not in BLOCK_TYPES:
                raise _fail(where, f"{block_type!r} is not a declared block type")
            tokens.append(entry)
        elif entry.startswith("attachment:"):
            kind = entry.split(":", 1)[1]
            if kind not in attachment_kinds:
                raise _fail(where, f"{kind!r} is not an attachment of this framework")
            tokens.append(entry)
        elif entry.startswith("workflow:"):
            step = entry.split(":", 1)[1]
            if step not in _WORKFLOW_TOKENS:
                raise _fail(where, f"{step!r} is not a known workflow step")
            tokens.append(entry)
        else:
            raise _fail(where, f"{entry!r} is not a known evidence token")
    return tuple(tokens)


def _requirement(
    raw: Any, path: str, documents: frozenset[str], attachment_kinds: frozenset[str]
) -> RequirementItem:
    obj = _require(raw, path)
    unknown = set(obj) - {
        "id",
        "text",
        "citations",
        "evidence",
        "waivable",
        "applies_when",
        "param_refs",
    }
    if unknown:
        raise _fail(path, f"unknown requirement key(s) {sorted(unknown)}")
    item_id = _string(obj, "id", path)
    if not _ITEM_ID.match(item_id):
        raise _fail(f"{path}.id", "must match ^[a-z0-9_]{2,40}$")
    text = _string(obj, "text", path)
    if len(text) > _ITEM_TEXT_MAX:
        raise _fail(f"{path}.text", f"is longer than {_ITEM_TEXT_MAX} characters")
    if '"' in text:
        raise _fail(f"{path}.text", "must paraphrase, never quote (no double quotes)")
    param_refs: list[str] = []
    for index, entry in enumerate(obj.get("param_refs", []) or []):
        where = f"{path}.param_refs[{index}]"
        if not isinstance(entry, str) or not _PARAM_CODE.match(entry):
            raise _fail(where, "is not a governed parameter code")
        param_refs.append(entry)
    placeholders = set(_PARAM_PLACEHOLDER.findall(text))
    missing = placeholders - set(param_refs)
    if missing:
        raise _fail(
            f"{path}.param_refs",
            f"text references unlisted parameter(s) {sorted(missing)}",
        )
    return RequirementItem(
        id=item_id,
        text=text,
        citations=_citations(obj, "citations", path, documents, minimum=1),
        evidence=_evidence(_list(obj, "evidence", path), f"{path}.evidence", attachment_kinds),
        waivable=_bool(obj, "waivable", path),
        applies_when=_condition(
            _optional_string(obj, "applies_when", path), f"{path}.applies_when"
        ),
        param_refs=tuple(param_refs),
    )


def _section(
    raw: Any, index: int, documents: frozenset[str], attachment_kinds: frozenset[str]
) -> SectionDef:
    path = f"sections[{index}]"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "key",
        "letter",
        "order",
        "title",
        "citation",
        "source_status",
        "guidance",
        "data_blocks",
        "ai_draftable",
        "public_disclosure",
        "requirements",
    }
    if unknown:
        raise _fail(path, f"unknown section key(s) {sorted(unknown)}")
    key = _string(obj, "key", path)
    if not _SECTION_KEY.match(key):
        raise _fail(f"{path}.key", "must match ^[a-z][a-z0-9_]{1,59}$")
    source_status = _literal(obj, "source_status", path, _SOURCE_STATUSES)
    guidance = _string(obj, "guidance", path)
    pending_word = "pending" in guidance.casefold()
    if source_status == "sourced" and pending_word:
        raise _fail(f"{path}.guidance", "a sourced section must not describe itself as pending")
    if source_status == "pending_primary_text" and not pending_word:
        raise _fail(f"{path}.guidance", "a pending section must say so in its guidance")
    data_blocks = tuple(str(entry) for entry in _list(obj, "data_blocks", path))
    for position, block_type in enumerate(data_blocks):
        if block_type not in P1_BLOCK_TYPES:
            raise _fail(
                f"{path}.data_blocks[{position}]",
                f"{block_type!r} is not a block a cycle can hold yet",
            )
    return SectionDef(
        key=key,
        letter=_string(obj, "letter", path),
        order=_int(obj, "order", path),
        title=_string(obj, "title", path),
        citation=_citation(obj.get("citation"), f"{path}.citation", documents),
        source_status=source_status,  # pyright: ignore[reportArgumentType]
        guidance=guidance,
        data_blocks=data_blocks,
        ai_draftable=_bool(obj, "ai_draftable", path),
        public_disclosure=_bool(obj, "public_disclosure", path),
        requirements=tuple(
            _requirement(entry, f"{path}.requirements[{position}]", documents, attachment_kinds)
            for position, entry in enumerate(_list(obj, "requirements", path))
        ),
    )


def _method_mandate(raw: Any, path: str) -> MethodMandate:
    obj = _require(raw, path)
    unknown = set(obj) - {"method", "mandatory_from_param", "replaces"}
    if unknown:
        raise _fail(path, f"unknown method-mandate key(s) {sorted(unknown)}")
    method = _string(obj, "method", path)
    if method not in METHOD_KEYS:
        raise _fail(f"{path}.method", f"{method!r} is not a known Pillar 2 method")
    replaces = _optional_string(obj, "replaces", path)
    if replaces is not None and replaces not in METHOD_KEYS:
        raise _fail(f"{path}.replaces", f"{replaces!r} is not a known Pillar 2 method")
    return MethodMandate(
        method=method,
        mandatory_from_param=_param_code(obj, "mandatory_from_param", path),
        replaces=replaces,
    )


def _component(raw: Any, path: str, table5_keys: frozenset[str]) -> Pillar2Component:
    obj = _require(raw, path)
    unknown = set(obj) - {
        "key",
        "table5_row",
        "p29_class",
        "p29_basis",
        "allowed_methods",
        "method_mandates",
    }
    if unknown:
        raise _fail(path, f"unknown component key(s) {sorted(unknown)}")
    methods = tuple(str(entry) for entry in _list(obj, "allowed_methods", path))
    if not methods:
        raise _fail(f"{path}.allowed_methods", "needs at least one method")
    for position, method in enumerate(methods):
        if method not in METHOD_KEYS:
            raise _fail(
                f"{path}.allowed_methods[{position}]",
                f"{method!r} is not a known Pillar 2 method",
            )
    table5_row = _optional_string(obj, "table5_row", path)
    if table5_row is None:
        # A2: with no Table 5 the whole column is absent; otherwise only a risk
        # that carries no capital line may leave it unset.
        if table5_keys and methods != (NOT_CAPITALISED,):
            raise _fail(
                f"{path}.table5_row",
                "only a component whose sole method is not_capitalised may omit its Table 5 row",
            )
    else:
        if not table5_keys:
            raise _fail(f"{path}.table5_row", "this framework declares no Table 5 rows")
        if table5_row not in table5_keys:
            raise _fail(f"{path}.table5_row", f"{table5_row!r} is not a declared Table 5 row")
    return Pillar2Component(
        key=_string(obj, "key", path),
        table5_row=table5_row,
        p29_class=_literal(obj, "p29_class", path, _P29_CLASSES),  # pyright: ignore[reportArgumentType]
        p29_basis=_literal(obj, "p29_basis", path, frozenset({"sourced", "inferred"})),  # pyright: ignore[reportArgumentType]
        allowed_methods=methods,
        method_mandates=tuple(
            _method_mandate(entry, f"{path}.method_mandates[{position}]")
            for position, entry in enumerate(obj.get("method_mandates", []) or [])
        ),
    )


def _risk_category(  # noqa: PLR0913 - the parse context is explicit
    raw: Any,
    index: int,
    documents: frozenset[str],
    attachment_kinds: frozenset[str],
    table5_keys: frozenset[str],
) -> RiskCategoryDef:
    path = f"risk_categories[{index}]"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "key",
        "number",
        "title",
        "citation",
        "title_status",
        "pillar1_coverage",
        "custom",
        "components",
        "sub_requirements",
    }
    if unknown:
        raise _fail(path, f"unknown risk-category key(s) {sorted(unknown)}")
    custom = _bool(obj, "custom", path)
    title_status = _literal(obj, "title_status", path, _TITLE_STATUSES)
    if title_status == "platform" and not custom:
        raise _fail(
            f"{path}.title_status",
            "only a bank-defined (custom) category may carry a platform title",
        )
    components = tuple(
        _component(entry, f"{path}.components[{position}]", table5_keys)
        for position, entry in enumerate(_list(obj, "components", path))
    )
    if not components:
        raise _fail(f"{path}.components", "needs at least one component")
    return RiskCategoryDef(
        key=_string(obj, "key", path),
        number=_int(obj, "number", path),
        title=_string(obj, "title", path),
        citation=_citation(obj.get("citation"), f"{path}.citation", documents),
        title_status=title_status,  # pyright: ignore[reportArgumentType]
        pillar1_coverage=_literal(  # pyright: ignore[reportArgumentType]
            obj, "pillar1_coverage", path, frozenset({"full", "partial", "none"})
        ),
        custom=custom,
        components=components,
        sub_requirements=tuple(
            _requirement(entry, f"{path}.sub_requirements[{position}]", documents, attachment_kinds)
            for position, entry in enumerate(_list(obj, "sub_requirements", path))
        ),
    )


def _attachment(raw: Any, index: int, documents: frozenset[str]) -> AttachmentRequirement:
    path = f"attachments[{index}]"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "kind",
        "title",
        "citations",
        "gate",
        "min_count",
        "max_count",
        "relaxable_by_signing_policy",
        "media_types",
        "applies_when",
        "section_keys",
    }
    if unknown:
        raise _fail(path, f"unknown attachment key(s) {sorted(unknown)}")
    citations = _citations(obj, "citations", path, documents)
    gate = _literal(obj, "gate", path, _ATTACHMENT_GATES)
    min_count = _int(obj, "min_count", path)
    if min_count < 0:
        raise _fail(f"{path}.min_count", "cannot be negative")
    relaxable = _bool(obj, "relaxable_by_signing_policy", path)
    if gate == "submission" and relaxable and citations:
        raise _fail(
            f"{path}.relaxable_by_signing_policy",
            "a document the regulator requires at submission is never relaxable",
        )
    max_count = obj.get("max_count")
    if max_count is not None and type(max_count) is not int:
        raise _fail(f"{path}.max_count", "expected an integer or null")
    return AttachmentRequirement(
        kind=_string(obj, "kind", path),
        title=_string(obj, "title", path),
        citations=citations,
        gate=gate,  # pyright: ignore[reportArgumentType]
        min_count=min_count,
        max_count=max_count,
        relaxable_by_signing_policy=relaxable,
        media_types=tuple(str(entry) for entry in _list(obj, "media_types", path)),
        applies_when=_condition(
            _optional_string(obj, "applies_when", path), f"{path}.applies_when"
        ),
        section_keys=tuple(str(entry) for entry in _list(obj, "section_keys", path)),
    )


def _stage(raw: Any, index: int, documents: frozenset[str]) -> StageTemplate:
    path = f"stages[{index}]"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "seq",
        "key",
        "title",
        "decision",
        "default_officer_titles",
        "freeze_on_approve",
        "citations",
    }
    if unknown:
        raise _fail(path, f"unknown stage key(s) {sorted(unknown)}")
    return StageTemplate(
        seq=_int(obj, "seq", path),
        key=_string(obj, "key", path),
        title=_string(obj, "title", path),
        decision=_literal(obj, "decision", path, _STAGE_DECISIONS),  # pyright: ignore[reportArgumentType]
        default_officer_titles=tuple(
            str(entry) for entry in _list(obj, "default_officer_titles", path)
        ),
        freeze_on_approve=_bool(obj, "freeze_on_approve", path),
        citations=_citations(obj, "citations", path, documents),
    )


def _deadline(raw: Any, documents: frozenset[str]) -> DeadlineSpec:
    path = "deadline"
    obj = _require(raw, path)
    unknown = set(obj) - {"as_of", "fy_end_month_day", "months_after_fye_param", "citations"}
    if unknown:
        raise _fail(path, f"unknown deadline key(s) {sorted(unknown)}")
    month_day = _string(obj, "fy_end_month_day", path)
    if not _MONTH_DAY.match(month_day):
        raise _fail(f"{path}.fy_end_month_day", "expected MM-DD")
    return DeadlineSpec(
        as_of=_literal(obj, "as_of", path, frozenset({"fy_end"})),  # pyright: ignore[reportArgumentType]
        fy_end_month_day=month_day,
        months_after_fye_param=_param_code(obj, "months_after_fye_param", path),
        citations=_citations(obj, "citations", path, documents),
    )


def _filing_role_map(raw: Any, path: str) -> tuple[tuple[str, str], ...]:
    obj = _require(raw, path)
    unknown = set(obj) - _FILING_ROLES
    if unknown:
        raise _fail(path, f"unknown signature role(s) {sorted(unknown)}")
    if not obj:
        raise _fail(path, "needs wording for at least one signature role")
    return tuple(sorted((str(role), _string(obj, str(role), path)) for role in obj))


def _filing(raw: Any, documents: frozenset[str]) -> FilingSpec | None:
    """The filing block: which return, which annexes, and what the signers sign.

    Absent means "this framework is published for reference only". That is a
    real state — Nigeria and Kenya ship as data long before a CBN or CBK return
    family exists — and it must read as a refusal at freeze rather than as a
    guessed return code.
    """
    if raw is None:
        return None
    path = "filing"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "return_codes",
        "annexes",
        "attestation",
        "stress_validation",
        "exposure_draft_line",
    }
    if unknown:
        raise _fail(path, f"unknown filing key(s) {sorted(unknown)}")

    codes_obj = _require(obj.get("return_codes"), f"{path}.return_codes")
    unknown_purposes = set(codes_obj) - _FILING_PURPOSES
    if unknown_purposes:
        raise _fail(f"{path}.return_codes", f"unknown purpose(s) {sorted(unknown_purposes)}")
    if "annual" not in codes_obj:
        raise _fail(f"{path}.return_codes", "an annual return code is required")
    return_codes = tuple(
        sorted(
            (str(key), _string(codes_obj, str(key), f"{path}.return_codes"))
            for key in codes_obj
        )
    )

    annexes: list[FilingAnnex] = []
    for index, entry in enumerate(_list(obj, "annexes", path)):
        where = f"{path}.annexes[{index}]"
        annex = _require(entry, where)
        extra = set(annex) - {"return_code", "required", "citations"}
        if extra:
            raise _fail(where, f"unknown annex key(s) {sorted(extra)}")
        annexes.append(
            FilingAnnex(
                return_code=_string(annex, "return_code", where),
                required=_bool(annex, "required", where),
                citations=_citations(annex, "citations", where, documents),
            )
        )
    codes = [annex.return_code for annex in annexes]
    repeated = {code for code in codes if codes.count(code) > 1}
    if repeated:
        raise _fail(f"{path}.annexes", f"duplicate annex return code(s) {sorted(repeated)}")

    attestation_obj = _require(obj.get("attestation"), f"{path}.attestation")
    extra = set(attestation_obj) - {"source_status", "lines", "statements"}
    if extra:
        raise _fail(f"{path}.attestation", f"unknown key(s) {sorted(extra)}")
    lines = _filing_role_map(attestation_obj.get("lines"), f"{path}.attestation.lines")
    statements = _filing_role_map(
        attestation_obj.get("statements"), f"{path}.attestation.statements"
    )
    if {role for role, _ in lines} != {role for role, _ in statements}:
        raise _fail(
            f"{path}.attestation",
            "every role with a signature line needs the statement it attests to",
        )

    stress_raw = obj.get("stress_validation")
    stress: StressValidationSpec | None = None
    if stress_raw is not None:
        where = f"{path}.stress_validation"
        stress_obj = _require(stress_raw, where)
        extra = set(stress_obj) - {"horizon_param", "severe_min_param"}
        if extra:
            raise _fail(where, f"unknown key(s) {sorted(extra)}")
        stress = StressValidationSpec(
            horizon_param=(
                _param_code(stress_obj, "horizon_param", where)
                if "horizon_param" in stress_obj
                else None
            ),
            severe_min_param=(
                _param_code(stress_obj, "severe_min_param", where)
                if "severe_min_param" in stress_obj
                else None
            ),
        )
    return FilingSpec(
        return_codes=return_codes,
        annexes=tuple(annexes),
        attestation=FilingAttestation(
            source_status=_literal(  # pyright: ignore[reportArgumentType]
                attestation_obj,
                "source_status",
                f"{path}.attestation",
                frozenset({"platform_default", "sourced"}),
            ),
            lines=lines,
            statements=statements,
        ),
        stress_validation=stress,
        exposure_draft_line=_optional_string(obj, "exposure_draft_line", path),
    )


def _materiality(raw: Any) -> MaterialityMatrix:
    path = "materiality_matrix"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "source_status",
        "note",
        "likelihood_levels",
        "impact_levels",
        "rating_bands_param",
        "material_if",
    }
    if unknown:
        raise _fail(path, f"unknown materiality key(s) {sorted(unknown)}")

    def levels(key: str) -> tuple[MaterialityLevel, ...]:
        out: list[MaterialityLevel] = []
        for index, entry in enumerate(_list(obj, key, path)):
            where = f"{path}.{key}[{index}]"
            level = _require(entry, where)
            extra = set(level) - {"score", "key", "label"}
            if extra:
                raise _fail(where, f"unknown level key(s) {sorted(extra)}")
            out.append(
                MaterialityLevel(
                    score=_int(level, "score", where),
                    key=_string(level, "key", where),
                    label=_string(level, "label", where),
                )
            )
        if not out:
            raise _fail(_at(path, key), "needs at least one level")
        return tuple(out)

    material_if = _require(obj.get("material_if"), f"{path}.material_if")
    extra = set(material_if) - {"min_score_param", "or_min_impact_param"}
    if extra:
        raise _fail(f"{path}.material_if", f"unknown key(s) {sorted(extra)}")
    return MaterialityMatrix(
        source_status=_literal(  # pyright: ignore[reportArgumentType]
            obj, "source_status", path, frozenset({"platform_default"})
        ),
        likelihood_levels=levels("likelihood_levels"),
        impact_levels=levels("impact_levels"),
        rating_bands_param=_param_code(obj, "rating_bands_param", path),
        material_min_score_param=_param_code(material_if, "min_score_param", f"{path}.material_if"),
        material_min_impact_param=_param_code(
            material_if, "or_min_impact_param", f"{path}.material_if"
        ),
        note=_optional_string(obj, "note", path),
    )


def _disclosure(raw: Any, documents: frozenset[str]) -> DisclosureSpec | None:
    if raw is None:
        return None  # A3: a regulator with no publication requirement
    path = "disclosure"
    obj = _require(raw, path)
    unknown = set(obj) - {
        "citations",
        "source_status",
        "channel",
        "submit_months_after_fye_param",
        "trigger",
        "content_selection",
        "never_public_block_types",
        "requires_disclosure_approval",
        "note",
    }
    if unknown:
        raise _fail(path, f"unknown disclosure key(s) {sorted(unknown)}")
    never_public = tuple(str(entry) for entry in _list(obj, "never_public_block_types", path))
    for index, block_type in enumerate(never_public):
        if block_type not in BLOCK_TYPES:
            raise _fail(
                f"{path}.never_public_block_types[{index}]",
                f"{block_type!r} is not a declared block type",
            )
    submit_param = obj.get("submit_months_after_fye_param")
    return DisclosureSpec(
        citations=_citations(obj, "citations", path, documents),
        source_status=_literal(  # pyright: ignore[reportArgumentType]
            obj, "source_status", path, frozenset({"sourced", "interpretation_required"})
        ),
        channel=_string(obj, "channel", path),
        submit_months_after_fye_param=(
            None
            if submit_param is None
            else _param_code(obj, "submit_months_after_fye_param", path)
        ),
        trigger=_literal(  # pyright: ignore[reportArgumentType]
            obj, "trigger", path, frozenset({"annual", "annual_and_each_update"})
        ),
        content_selection=_literal(  # pyright: ignore[reportArgumentType]
            obj, "content_selection", path, frozenset({"bank_selected", "prescribed"})
        ),
        never_public_block_types=never_public,
        requires_disclosure_approval=_bool(obj, "requires_disclosure_approval", path),
        note=_optional_string(obj, "note", path),
    )


def _mapping(raw: Any, path: str) -> SectionKeyMapping:
    obj = _require(raw, path)
    unknown = set(obj) - {"from", "to", "relation"}
    if unknown:
        raise _fail(path, f"unknown mapping key(s) {sorted(unknown)}")
    relation = _literal(obj, "relation", path, _KEY_RELATIONS)
    to_key = _optional_string(obj, "to", path)
    if relation == "unmapped" and to_key is not None:
        raise _fail(f"{path}.to", "an unmapped section has no target")
    if relation != "unmapped" and to_key is None:
        raise _fail(f"{path}.to", "only an unmapped section may have a null target")
    return SectionKeyMapping(
        from_key=_string(obj, "from", path),
        to_key=to_key,
        relation=relation,  # pyright: ignore[reportArgumentType]
    )


def _cross_map(raw: Any, index: int) -> CrossFrameworkMap:
    path = f"cross_framework_section_map[{index}]"
    obj = _require(raw, path)
    unknown = set(obj) - {"framework", "direction", "map"}
    if unknown:
        raise _fail(path, f"unknown cross-map key(s) {sorted(unknown)}")
    return CrossFrameworkMap(
        framework=_string(obj, "framework", path),
        direction=_literal(obj, "direction", path, frozenset({"to", "from"})),  # pyright: ignore[reportArgumentType]
        mappings=tuple(
            _mapping(entry, f"{path}.map[{position}]")
            for position, entry in enumerate(_list(obj, "map", path))
        ),
    )


def _check_sections(sections: Sequence[SectionDef]) -> None:
    if not sections:
        raise _fail("sections", "a framework needs at least one section")
    seen: set[str] = set()
    for index, section in enumerate(sections):
        path = f"sections[{index}]"
        expected_letter = _letter_for(index)
        if section.letter != expected_letter:
            raise _fail(f"{path}.letter", f"expected {expected_letter!r}, got {section.letter!r}")
        if section.order != index + 1:
            raise _fail(f"{path}.order", f"expected {index + 1}, got {section.order}")
        if section.key in seen:
            raise _fail(f"{path}.key", f"duplicate section key {section.key!r}")
        seen.add(section.key)


def _letter_for(index: int) -> str:
    """a, b, ... z, aa, ab, ... — enough for any published ICAAP instrument."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters


def _check_item_ids(framework_items: Sequence[tuple[str, RequirementItem]]) -> None:
    seen: dict[str, str] = {}
    for path, item in framework_items:
        if item.id in seen:
            raise _fail(path, f"duplicate requirement id {item.id!r} (also at {seen[item.id]})")
        seen[item.id] = path


def parse_framework(obj: object, *, digest: str) -> Framework:  # noqa: PLR0912, PLR0915
    """Parse and validate one framework document.

    Every refusal names a JSON path, because the alternative — a framework that
    loads with a section quietly missing — is a checklist a bank would file
    against.
    """
    root = _require(obj, "")
    unknown = set(root) - _TOP_LEVEL_KEYS
    if unknown:
        raise _fail("", f"unknown top-level key(s) {sorted(unknown)}")
    if root.get("schema") != SCHEMA_ID:
        raise _fail("schema", f"expected {SCHEMA_ID!r}")

    documents = tuple(
        DocumentRef(
            id=_string(_require(entry, f"documents[{index}]"), "id", f"documents[{index}]"),
            title=_string(_require(entry, f"documents[{index}]"), "title", f"documents[{index}]"),
            short_label=_string(
                _require(entry, f"documents[{index}]"), "short_label", f"documents[{index}]"
            ),
            issuer=_string(_require(entry, f"documents[{index}]"), "issuer", f"documents[{index}]"),
            issued=_string(_require(entry, f"documents[{index}]"), "issued", f"documents[{index}]"),
            status=_literal(  # pyright: ignore[reportArgumentType]
                _require(entry, f"documents[{index}]"),
                "status",
                f"documents[{index}]",
                _FRAMEWORK_STATUSES,
            ),
            url=_optional_string(
                _require(entry, f"documents[{index}]"), "url", f"documents[{index}]"
            ),
        )
        for index, entry in enumerate(_list(root, "documents", ""))
    )
    if not documents:
        raise _fail("documents", "a framework must declare the documents it cites")
    document_ids = frozenset(document.id for document in documents)

    attachments = tuple(
        _attachment(entry, index, document_ids)
        for index, entry in enumerate(_list(root, "attachments", ""))
    )
    kinds = [attachment.kind for attachment in attachments]
    duplicates = {kind for kind in kinds if kinds.count(kind) > 1}
    if duplicates:
        raise _fail("attachments", f"duplicate attachment kind(s) {sorted(duplicates)}")
    attachment_kinds = frozenset(kinds)

    table5_rows = tuple(
        Table5Row(
            key=_string(_require(entry, f"table5_rows[{index}]"), "key", f"table5_rows[{index}]"),
            label=_string(
                _require(entry, f"table5_rows[{index}]"), "label", f"table5_rows[{index}]"
            ),
            order=_int(_require(entry, f"table5_rows[{index}]"), "order", f"table5_rows[{index}]"),
        )
        for index, entry in enumerate(_list(root, "table5_rows", ""))
    )
    table5_keys = frozenset(row.key for row in table5_rows)
    raw_table5_citation = root.get("table5_citation")
    if table5_rows and raw_table5_citation is None:
        raise _fail("table5_citation", "a declared Table 5 needs its citation")
    if not table5_rows and raw_table5_citation is not None:
        raise _fail("table5_citation", "no Table 5 rows are declared")
    table5_citation = (
        None
        if raw_table5_citation is None
        else _citation(raw_table5_citation, "table5_citation", document_ids)
    )

    sections = tuple(
        _section(entry, index, document_ids, attachment_kinds)
        for index, entry in enumerate(_list(root, "sections", ""))
    )
    _check_sections(sections)
    section_keys = frozenset(section.key for section in sections)

    risk_categories = tuple(
        _risk_category(entry, index, document_ids, attachment_kinds, table5_keys)
        for index, entry in enumerate(_list(root, "risk_categories", ""))
    )
    for index, category in enumerate(risk_categories):
        if category.number != index + 1:
            raise _fail(f"risk_categories[{index}].number", f"expected {index + 1}")
    mapped_rows = {
        component.table5_row
        for category in risk_categories
        for component in category.components
        if component.table5_row is not None
    }
    unmapped = table5_keys - mapped_rows
    if unmapped:
        raise _fail("table5_rows", f"no risk component maps to {sorted(unmapped)}")

    _check_item_ids(
        [
            *(
                (f"sections[{s}].requirements[{i}]", item)
                for s, section in enumerate(sections)
                for i, item in enumerate(section.requirements)
            ),
            *(
                (f"risk_categories[{c}].sub_requirements[{i}]", item)
                for c, category in enumerate(risk_categories)
                for i, item in enumerate(category.sub_requirements)
            ),
        ]
    )

    for index, attachment in enumerate(attachments):
        for position, key in enumerate(attachment.section_keys):
            if key not in section_keys:
                raise _fail(
                    f"attachments[{index}].section_keys[{position}]",
                    f"{key!r} is not a section of this framework",
                )

    stages = tuple(
        _stage(entry, index, document_ids) for index, entry in enumerate(_list(root, "stages", ""))
    )
    for index, stage in enumerate(stages):
        if stage.seq != index + 1:
            raise _fail(f"stages[{index}].seq", f"expected {index + 1}")
    freezing = [stage.key for stage in stages if stage.freeze_on_approve]
    if len(freezing) > 1:
        raise _fail("stages", f"only one stage may freeze the cycle, got {freezing}")

    supersedes_raw = root.get("supersedes")
    supersedes: tuple[str, str] | None = None
    if supersedes_raw is not None:
        superseded = _require(supersedes_raw, "supersedes")
        supersedes = (
            _string(superseded, "code", "supersedes"),
            _string(superseded, "version", "supersedes"),
        )
    section_key_map = tuple(
        _mapping(entry, f"section_key_map[{index}]")
        for index, entry in enumerate(_list(root, "section_key_map", ""))
    )
    if supersedes is None and section_key_map:
        raise _fail("section_key_map", "a framework that supersedes nothing maps nothing")
    for index, mapping in enumerate(section_key_map):
        if mapping.to_key is not None and mapping.to_key not in section_keys:
            raise _fail(f"section_key_map[{index}].to", f"{mapping.to_key!r} is not a section here")
    froms = [mapping.from_key for mapping in section_key_map]
    repeated = {key for key in froms if froms.count(key) > 1}
    if repeated:
        raise _fail(
            "section_key_map", f"source section(s) mapped more than once: {sorted(repeated)}"
        )

    cross_map = tuple(
        _cross_map(entry, index)
        for index, entry in enumerate(_list(root, "cross_framework_section_map", ""))
    )

    filing_return_code = _optional_string(root, "filing_return_code", "")
    effective_citations = _citations(root, "effective_from_citations", "", document_ids, minimum=1)
    document_citations = _citations(root, "document_citations", "", document_ids)

    framework = Framework(
        code=_string(root, "code", ""),
        jurisdiction=_string(root, "jurisdiction", ""),
        regulator=_string(root, "regulator", ""),
        title=_string(root, "title", ""),
        short_title=_string(root, "short_title", ""),
        version=_string(root, "version", ""),
        status=_literal(root, "status", "", _FRAMEWORK_STATUSES),  # pyright: ignore[reportArgumentType]
        effective_from=_date(root, "effective_from", ""),
        first_as_of_date=_date(root, "first_as_of_date", ""),
        first_as_of_basis=_literal(  # pyright: ignore[reportArgumentType]
            root, "first_as_of_basis", "", frozenset({"sourced", "inferred"})
        ),
        first_as_of_note=_optional_string(root, "first_as_of_note", ""),
        institution_classes=tuple(str(e) for e in _list(root, "institution_classes", "")),
        filing_return_code=filing_return_code,
        supersedes=supersedes,
        section_key_map=section_key_map,
        cross_framework_section_map=cross_map,
        documents=documents,
        related_instruments=tuple(
            RelatedInstrument(
                id=_string(
                    _require(entry, f"related_instruments[{index}]"),
                    "id",
                    f"related_instruments[{index}]",
                ),
                title=_string(
                    _require(entry, f"related_instruments[{index}]"),
                    "title",
                    f"related_instruments[{index}]",
                ),
                issued=_optional_string(
                    _require(entry, f"related_instruments[{index}]"),
                    "issued",
                    f"related_instruments[{index}]",
                ),
                status=_literal(  # pyright: ignore[reportArgumentType]
                    _require(entry, f"related_instruments[{index}]"),
                    "status",
                    f"related_instruments[{index}]",
                    _FRAMEWORK_STATUSES,
                ),
                named_by=_citations(
                    _require(entry, f"related_instruments[{index}]"),
                    "named_by",
                    f"related_instruments[{index}]",
                    document_ids,
                ),
            )
            for index, entry in enumerate(_list(root, "related_instruments", ""))
        ),
        deadline=_deadline(root.get("deadline"), document_ids),
        sections=sections,
        table5_rows=table5_rows,
        table5_citation=table5_citation,
        risk_categories=risk_categories,
        attachments=attachments,
        stages=stages,
        stages_source_status=_string(root, "stages_source_status", ""),
        material_change_triggers=tuple(
            MaterialChangeTrigger(
                code=_string(
                    _require(entry, f"material_change_triggers[{index}]"),
                    "code",
                    f"material_change_triggers[{index}]",
                ),
                label=_string(
                    _require(entry, f"material_change_triggers[{index}]"),
                    "label",
                    f"material_change_triggers[{index}]",
                ),
                citations=_citations(
                    _require(entry, f"material_change_triggers[{index}]"),
                    "citations",
                    f"material_change_triggers[{index}]",
                    document_ids,
                    minimum=1,
                ),
            )
            for index, entry in enumerate(_list(root, "material_change_triggers", ""))
        ),
        materiality=_materiality(root.get("materiality_matrix")),
        disclosure=_disclosure(root.get("disclosure"), document_ids),
        filing=_filing(root.get("filing"), document_ids),
        notes=tuple(
            FrameworkNote(
                code=_string(_require(entry, f"notes[{index}]"), "code", f"notes[{index}]"),
                text=_string(_require(entry, f"notes[{index}]"), "text", f"notes[{index}]"),
                citations=_citations(
                    _require(entry, f"notes[{index}]"), "citations", f"notes[{index}]", document_ids
                ),
            )
            for index, entry in enumerate(_list(root, "notes", ""))
        ),
        digest=digest,
    )
    object.__setattr__(
        framework, "standalone_citations", (*effective_citations, *document_citations)
    )
    if not framework.institution_classes:
        raise _fail("institution_classes", "a framework must name the institutions it applies to")
    return framework


__all__ = [
    "SCHEMA_ID",
    "AttachmentGate",
    "AttachmentRequirement",
    "Citation",
    "CrossFrameworkMap",
    "DeadlineSpec",
    "DisclosureSpec",
    "DocumentRef",
    "FilingAnnex",
    "FilingAttestation",
    "FilingSpec",
    "Framework",
    "FrameworkNote",
    "FrameworkSchemaError",
    "FrameworkStatus",
    "KeyRelation",
    "MaterialChangeTrigger",
    "MaterialityLevel",
    "MaterialityMatrix",
    "MethodMandate",
    "P29Class",
    "Pillar2Component",
    "RelatedInstrument",
    "RequirementItem",
    "RiskCategoryDef",
    "SectionDef",
    "SectionKeyMapping",
    "SourceStatus",
    "StageDecision",
    "StageTemplate",
    "StressValidationSpec",
    "Table5Row",
    "TitleStatus",
    "parse_framework",
]
