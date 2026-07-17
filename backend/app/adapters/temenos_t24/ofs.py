"""OFS (Open Financial Service) message codec for Temenos T24.

OFS is T24's canonical machine interface. Two things live here and nothing
T24-application-specific does:

1. **Request envelope** — the documented delimited OFS request string
   ``APPLICATION,VERSION/FUNCTION/PROCESS,USER/PASSWORD,ID,FIELD:MV:SV=VALUE,...``
   used for both transactions and, via ``ENQUIRY.SELECT``, read enquiries.
2. **Response envelope + multivalue flattening** — T24 records carry
   multivalued and subvalued fields delimited by Field/Value/Sub markers
   (ASCII 254 / 253 / 252, also written ``@FM`` / ``@VM`` / ``@SM``). This
   module flattens a raw field string into scalars / lists / lists-of-lists
   so extractors receive plain Python structures and never touch marker bytes.

This module deliberately knows NOTHING about which applications or fields
exist — that is catalog and extractor territory. It only speaks the wire
format. Raw response text is never surfaced to banks; callers wrap failures in
:class:`~app.adapters.temenos_t24.errors.TemenosError`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

# T24 record delimiters. The control-character forms are the on-the-wire truth;
# the mnemonic forms are what appear in text-safe transports and fixtures.
FM = "\x1e"  # normalized field marker used internally (see _normalize_markers)
VM = "\x1d"  # value marker
SM = "\x1c"  # sub-value marker

# Real T24 marker code points, plus the textual mnemonics, all normalized to
# the internal FM/VM/SM above before splitting.
_FM_FORMS = ("\xfe", "@FM", "\x1e")
_VM_FORMS = ("\xfd", "@VM", "\x1d")
_SM_FORMS = ("\xfc", "@SM", "\x1c")

# OFS request function codes (documented T24 verbs).
FUNCTION_INPUT = "I"
FUNCTION_AUTHORISE = "A"
FUNCTION_REVERSE = "R"
FUNCTION_DELETE = "D"
FUNCTION_SEE = "S"
FUNCTION_VALIDATE = "V"

# OFS process codes.
PROCESS = "PROCESS"
VALIDATE = "VALIDATE"


class OfsError(ValueError):
    """The OFS byte stream is malformed at the codec level (never surfaced to
    banks directly — callers translate this into a bank-facing TemenosError)."""


def _normalize_markers(raw: str) -> str:
    """Fold every accepted marker representation onto the internal FM/VM/SM."""
    for form in _FM_FORMS:
        raw = raw.replace(form, FM)
    for form in _VM_FORMS:
        raw = raw.replace(form, VM)
    for form in _SM_FORMS:
        raw = raw.replace(form, SM)
    return raw


def flatten_value(raw: str) -> str | list[str] | list[list[str]]:
    """Flatten one field's raw value by its markers.

    - no markers        -> the scalar string
    - value markers     -> list[str] (one per multivalue)
    - sub-value markers -> list[list[str]] (multivalue -> subvalues)

    A field mixing plain multivalues and subvalued multivalues is normalized to
    ``list[list[str]]`` only when at least one sub marker is present; otherwise
    it stays ``list[str]`` so the common case reads naturally.
    """
    raw = _normalize_markers(raw)
    if VM not in raw and SM not in raw:
        return raw
    if SM not in raw:
        return raw.split(VM)
    return [value.split(SM) for value in raw.split(VM)]


@dataclass(frozen=True)
class OfsRecord:
    """One parsed OFS record: its id plus flattened field values keyed by the
    T24 field name (as emitted by the enquiry / OFS response)."""

    record_id: str
    fields: dict[str, str | list[str] | list[list[str]]]

    def scalar(self, name: str, default: str | None = None) -> str | None:
        """The field as a scalar, taking the first multivalue if present."""
        value = self.fields.get(name, default)
        if value is None:
            return default
        while isinstance(value, list):
            value = value[0] if value else default
        return value  # type: ignore[return-value]


@dataclass(frozen=True)
class OfsResponse:
    """A decoded OFS response envelope.

    ``ok`` reflects the OFS transaction status. On failure ``error_code`` and
    ``error_text`` carry the raw T24 diagnostic for internal logs only — a
    caller must classify them via the error taxonomy before anything reaches a
    bank.
    """

    ok: bool
    records: tuple[OfsRecord, ...]
    error_code: str | None = None
    error_text: str | None = None
    raw_header: str = ""

    @property
    def record(self) -> OfsRecord | None:
        return self.records[0] if self.records else None


def _escape_id(record_id: str) -> str:
    return record_id.replace(",", r"\,")


def build_ofs_message(  # noqa: PLR0913 — OFS envelope legitimately has this many parts
    application: str,
    record_id: str,
    fields: Mapping[str, object] | None = None,
    *,
    version: str = "",
    function: str = FUNCTION_INPUT,
    process: str = PROCESS,
    user: str = "",
    password: str = "",
    gtsmode: str = "",
) -> str:
    """Build a documented delimited OFS request string.

    Field assignments use the ``NAME:MV:SV=VALUE`` form. A plain-string field
    is assigned at ``:1:1``; to place a multivalue/subvalue explicitly, pass a
    key already containing ``:`` (e.g. ``"CURR.NO:2:1"``). Credentials, when
    supplied, are included in the ``USER/PASSWORD`` slot — callers building
    read enquiries typically leave them blank and let the transport attach an
    authenticated session token instead.
    """
    header_fn = f"{version}/{function}/{process}" if function else version
    credential = f"{user}/{password}" if password else user
    parts = [application, header_fn, credential, _escape_id(record_id)]
    for name, value in (fields or {}).items():
        assignment = name if ":" in name else f"{name}:1:1"
        parts.append(f"{assignment}={_ofs_scalar(value)}")
    return ",".join(parts)


def _ofs_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def build_enquiry_message(
    enquiry: str,
    selection: Mapping[str, object] | Sequence[tuple[str, str, object]] | None = None,
    *,
    user: str = "",
    password: str = "",
) -> str:
    """Build an ``ENQUIRY.SELECT`` OFS request.

    ``selection`` is the enquiry's selection criteria. A mapping is treated as
    equality (``FIELD:EQ:VALUE``); pass a sequence of ``(field, operand, value)``
    tuples for other operands (``RG``, ``GT``, ``LT``, ``LK``, ...). Operands are
    the documented T24 enquiry operands; this codec does not invent field names.
    """
    criteria: list[tuple[str, str, object]] = []
    if isinstance(selection, Mapping):
        criteria = [(name, "EQ", value) for name, value in selection.items()]
    elif selection is not None:
        criteria = list(selection)
    credential = f"{user}/{password}" if password else user
    parts = ["ENQUIRY.SELECT", "", credential, enquiry]
    parts.extend(f"{name}:{operand}:{_ofs_scalar(value)}" for name, operand, value in criteria)
    return ",".join(parts)


# In the OFS response envelope the field section separates records with the
# field marker and field assignments with commas; the codec normalizes the
# record separator to FM before splitting.
_STATUS_OK = {"1", "OK"}


@dataclass
class _FieldAccumulator:
    values: dict[str, str] = field(default_factory=dict)


def parse_ofs_response(raw: str) -> OfsResponse:
    """Decode an OFS response envelope into flattened records.

    Expected shape (documented OFS success form)::

        APP,VERSION/FN/PROCESS,USER,ID/1,FIELD:MV:SV=VALUE,FIELD=VALUE,...

    The portion up to the first field marker (or newline) is the header; its
    ``ID/<status>`` token gives success (``1``) or failure. Field assignments
    after it are collapsed by base field name, positioned by their ``:MV:SV``
    coordinates, then flattened via :func:`flatten_value`. Multiple records are
    separated by the field marker at envelope level.

    On any structural problem this raises :class:`OfsError`; on a T24-reported
    failure it returns ``ok=False`` with the raw diagnostic for internal logs.
    """
    if not raw or not raw.strip():
        raise OfsError("empty OFS response")

    normalized = _normalize_markers(raw)
    record_blocks = [block for block in normalized.split(FM) if block.strip()]
    if not record_blocks:
        raise OfsError("OFS response had no record blocks")

    header = record_blocks[0].split(",", 1)[0] if "," in record_blocks[0] else record_blocks[0]
    status, error_text = _extract_status(record_blocks[0])
    if status not in _STATUS_OK:
        return OfsResponse(
            ok=False,
            records=(),
            error_code=status,
            error_text=error_text,
            raw_header=header,
        )

    records = tuple(_parse_record_block(block) for block in record_blocks)
    return OfsResponse(ok=True, records=records, raw_header=header)


def _extract_status(first_block: str) -> tuple[str, str | None]:
    """Pull the ``ID/<status>`` token and any trailing error text from the first
    field of the first record block."""
    head = first_block.split(",", 1)[0]
    if "/" in head:
        status = head.rsplit("/", 1)[1].strip()
        # A negative or non-numeric status carries error text after a comma.
        if status not in _STATUS_OK:
            tail = first_block.split(",", 1)[1] if "," in first_block else ""
            return status, tail or None
        return status, None
    # No explicit status token: treat the presence of field assignments as OK.
    return ("1" if "=" in first_block or ":" in first_block else "OK"), None


def _parse_record_block(block: str) -> OfsRecord:
    """Parse one record block ``ID/status,FIELD:MV:SV=VALUE,...`` into an
    OfsRecord with flattened values."""
    segments = block.split(",")
    header = segments[0]
    record_id = header.split("/", 1)[0].strip()

    # Reassemble positional field assignments into per-field marker strings.
    raw_by_field: dict[str, dict[tuple[int, int], str]] = {}
    for segment in segments[1:]:
        if "=" not in segment:
            continue
        locator, _, value = segment.partition("=")
        name, mv, sv = _split_locator(locator)
        raw_by_field.setdefault(name, {})[(mv, sv)] = value

    fields: dict[str, str | list[str] | list[list[str]]] = {}
    for name, positioned in raw_by_field.items():
        fields[name] = flatten_value(_reassemble(positioned))
    return OfsRecord(record_id=record_id, fields=fields)


def _split_locator(locator: str) -> tuple[str, int, int]:
    """Split ``NAME:MV:SV`` into name and 1-based multivalue/subvalue positions
    (absent positions default to 1)."""
    bits = locator.split(":")
    name = bits[0].strip()
    mv = int(bits[1]) if len(bits) > 1 and bits[1].isdigit() else 1
    sv = int(bits[2]) if len(bits) > 2 and bits[2].isdigit() else 1
    return name, mv, sv


def _reassemble(positioned: dict[tuple[int, int], str]) -> str:
    """Rebuild a marker-delimited field string from positioned assignments."""
    if len(positioned) == 1 and next(iter(positioned)) == (1, 1):
        return next(iter(positioned.values()))
    max_mv = max(mv for mv, _ in positioned)
    multivalues: list[str] = []
    for mv in range(1, max_mv + 1):
        subs = {sv: value for (m, sv), value in positioned.items() if m == mv}
        if not subs:
            multivalues.append("")
            continue
        max_sv = max(subs)
        if max_sv == 1:
            multivalues.append(subs.get(1, ""))
        else:
            multivalues.append(SM.join(subs.get(sv, "") for sv in range(1, max_sv + 1)))
    return VM.join(multivalues)
