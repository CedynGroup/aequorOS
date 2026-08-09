"""Source-layer core: specs, parse results, validation, and capture ingestion.

The desk's Tier-1 Ghana sources (spec §3) are HTML/PDF/XLSX publications with
no APIs; every parser here was built against a REAL captured document in
``tests/fixtures/market_desk/`` and encodes that harvest's quirks catalog
(fixtures README "Top things the ingestion adapters must handle").

Design rules:

- Parsers are pure: ``bytes -> ParseResult``. No network, no DB.
- Validation never silently drops data (exact duplicates excepted, and those
  are counted in warnings): out-of-range and conflicting values are kept and
  quality-flagged so the desk analyst — not the parser — decides.
- ``ingest_capture`` is the single write path from a capture to observations,
  reusing the append-only supersession idiom of ``observations.py`` so a
  re-parse supersedes cleanly instead of colliding with the
  current-generation unique index.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from app.core.ids import new_uuid7
from app.models import DeskObservation

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

    from app.models import DeskSourceCapture


class FetchMethod(enum.StrEnum):
    """How a source's raw bytes come off the wire (spec §3 ingestion-method
    summary: Ghana is HTML-scrape + templated-PDF-parse, no API except GSS)."""

    WDT_AJAX = "wdt_ajax"  # BoG wpDataTables admin-ajax POST protocol
    HTML = "html"  # plain page scrape (auction indexes/tender pages)
    PDF = "pdf"  # templated PDF parse (auction results, APR, SEFD)
    XLSX = "xlsx"  # GFIM daily trading report workbooks
    PXWEB_API = "pxweb_api"  # GSS statsbank PxWeb JSON
    MANUAL = "manual"  # desk operator entry (observations.py)


# Decimal storage scale must fit DeskObservation.value Numeric(28, 10);
# GFIM publishes yields to 15dp so drafts are quantized on write.
_VALUE_QUANT = Decimal("0.0000000001")

# Validation bounds by unit family (spec §4 "range/bounds checks"). Values
# outside the band are KEPT and flagged — the desk decides, not the parser.
RANGE_BOUNDS: dict[str, tuple[Decimal, Decimal]] = {
    "pct": (Decimal("0"), Decimal("60")),  # GHS rates: 0-60% p.a.
    "rate": (Decimal("0.1"), Decimal("1000")),  # FX rates vs GHS
}

# Staleness thresholds by cadence, in days between the capture's as-of and
# the newest observation it yields (fixtures README quirk 5: statsbank is
# ~2 years stale while table 69 is T+0 — staleness must be per-source).
STALENESS_LIMIT_DAYS: dict[str, int] = {
    "daily": 5,
    "weekly": 14,
    "monthly": 75,
    "per_event": 120,
}

QF_SOURCE_CONFLICT = "source_conflict"
QF_OUT_OF_RANGE = "out_of_range"
QF_STALE_SOURCE = "stale_source"
QF_AS_OF_FROM_CAPTURE = "as_of_from_capture"
QF_CROSS_SOURCE_MISMATCH = "cross_source_mismatch"
QF_NO_TRADES = "no_trades"
QF_WATERMARK_CLEANED = "watermark_cleaned"


@dataclass
class ObservationDraft:
    """One cleaned observation as produced by a parser, before DB write."""

    series_code: str
    as_of_date: date
    value: Decimal
    unit: str
    attributes: dict[str, Any] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)

    def flag(self, flag: str) -> None:
        if flag not in self.quality_flags:
            self.quality_flags.append(flag)


@dataclass
class ParseResult:
    """What one parser run produced — observations plus honest telemetry.

    ``errors`` are fatal defects (capture is stamped ``failed`` when there
    are errors and nothing usable came out); ``warnings`` are the dirty-data
    ledger (dropped duplicates, skipped empty dates, unsupported sections).
    """

    observations: list[ObservationDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParseContext:
    """Ambient facts a parser may need beyond the raw bytes.

    ``as_of_date`` backfills documents that carry no date of their own (the
    table-32 FX reference banner is a single text cell with no date column —
    such observations get ``QF_AS_OF_FROM_CAPTURE``).
    """

    as_of_date: date
    source_url: str | None = None


class SourceParser(Protocol):
    def __call__(self, raw: bytes, *, context: ParseContext) -> ParseResult: ...


@dataclass(frozen=True)
class SourceSpec:
    """Registry row for one Tier-1 source: identity, cadence, mechanics.

    ``manual_fallback`` is True for every source by design (spec §3: "source-
    adapter plugins with a manual-entry fallback for every source", because
    document layouts change and BoG blocks automated access) — the UI renders
    entry forms for ``series_codes`` via ``observations.record_manual_observation``.
    ``unsupported`` names what this source publishes that the parser
    deliberately does NOT extract — honesty over coverage.
    """

    source_key: str
    display_name: str
    cadence: str  # 'daily' | 'weekly' | 'monthly' | 'per_event'
    fetch_method: FetchMethod
    parser: SourceParser | None
    parser_version: str
    series_codes: tuple[str, ...]  # codes/patterns this source owns
    manual_fallback: bool = True
    unsupported: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class ReconciliationRule:
    """Same economic series from two sources must agree within tolerance
    (spec §4 cross-source reconciliation: BoG vs GFIM vs GSS)."""

    series_a: str
    series_b: str
    tolerance: Decimal
    description: str = ""


RECONCILIATION_RULES: tuple[ReconciliationRule, ...] = (
    ReconciliationRule(
        series_a="GHS.GRR",
        series_b="GHS.SEFD.GRR",
        tolerance=Decimal("0.5"),
        description="Ghana Reference Rate: GSS statsbank vs BoG SEFD table 3a",
    ),
    ReconciliationRule(
        series_a="GHS.GSS.MPR",
        series_b="GHS.SEFD.MPR",
        tolerance=Decimal("0.5"),
        description="Monthly-average MPR: GSS statsbank vs BoG SEFD table 3a",
    ),
    ReconciliationRule(
        series_a="GHS.IWAL",
        series_b="GHS.SEFD.INTERBANK_WAVG",
        tolerance=Decimal("0.5"),
        description="Interbank weighted average: GSS statsbank vs BoG SEFD",
    ),
    ReconciliationRule(
        series_a="GHS.LENDING.AVG",
        series_b="GHS.SEFD.LENDING_AVG",
        tolerance=Decimal("0.5"),
        description="Average lending rate: GSS statsbank vs BoG SEFD",
    ),
)


def slugify(text: str) -> str:
    """Series-code path token: uppercase alnum runs joined by underscores."""
    out: list[str] = []
    word: list[str] = []
    for ch in text.upper():
        if ch.isalnum():
            word.append(ch)
        elif word:
            out.append("".join(word))
            word = []
    if word:
        out.append("".join(word))
    return "_".join(out)


def quantize_value(value: Decimal) -> Decimal:
    """Fit a draft value into Numeric(28, 10) deterministically."""
    return value.quantize(_VALUE_QUANT, rounding=ROUND_HALF_UP).normalize()


def dedupe_and_resolve_conflicts(
    drafts: list[ObservationDraft], result: ParseResult
) -> list[ObservationDraft]:
    """Apply the README's drop/dedup/conflict policy to a parser's drafts.

    - Exact duplicates (same series, date, value): drop, count in warnings —
      the only silent-ish drop allowed, and it is not silent.
    - Same (series, date) with DIFFERENT values: keep the LAST, flag it
      ``source_conflict`` with every conflicting value in attributes (the
      table-69 2020-02-25 16.14-vs-16.12 case).
    """
    kept: dict[tuple[str, date], ObservationDraft] = {}
    exact_dupes = 0
    for draft in drafts:
        key = (draft.series_code, draft.as_of_date)
        current = kept.get(key)
        if current is None:
            kept[key] = draft
            continue
        if current.value == draft.value:
            exact_dupes += 1
            continue
        conflicts = list(current.attributes.get("conflicting_values", []))
        if not conflicts:
            conflicts.append(str(current.value))
        conflicts.append(str(draft.value))
        draft.attributes["conflicting_values"] = conflicts
        draft.flag(QF_SOURCE_CONFLICT)
        kept[key] = draft
    if exact_dupes:
        result.warnings.append(f"dropped {exact_dupes} exact duplicate row(s)")
    return list(kept.values())


def apply_range_bounds(drafts: list[ObservationDraft], result: ParseResult) -> None:
    flagged = 0
    for draft in drafts:
        bounds = RANGE_BOUNDS.get(draft.unit)
        if bounds is None:
            continue
        low, high = bounds
        if not low <= draft.value <= high:
            draft.flag(QF_OUT_OF_RANGE)
            flagged += 1
    if flagged:
        result.warnings.append(f"{flagged} observation(s) outside {QF_OUT_OF_RANGE} bounds (kept)")


def apply_staleness(
    drafts: list[ObservationDraft], result: ParseResult, *, cadence: str, as_of: date
) -> None:
    """Flag the newest observations when the whole source lags its cadence
    (README quirk 5: never let a stale monthly source look fresh)."""
    if not drafts:
        return
    limit = STALENESS_LIMIT_DAYS.get(cadence)
    if limit is None:
        return
    newest = max(d.as_of_date for d in drafts)
    gap_days = (as_of - newest).days
    if gap_days <= limit:
        return
    for draft in drafts:
        if draft.as_of_date == newest:
            draft.flag(QF_STALE_SOURCE)
            draft.attributes.setdefault("staleness_gap_days", gap_days)
    result.warnings.append(
        f"source is stale: newest observation {newest.isoformat()} is "
        f"{gap_days} days behind capture as-of ({cadence} cadence, limit {limit}d)"
    )


def run_reconciliations(db: Session, series_codes: set[str], result: ParseResult) -> None:
    """Cross-source check for every rule touching the just-written series.

    Compares CURRENT generations on shared as-of dates; a breach flags both
    rows ``cross_source_mismatch`` (accumulating, never dropping).
    """
    for rule in RECONCILIATION_RULES:
        if rule.series_a not in series_codes and rule.series_b not in series_codes:
            continue
        rows_a = _current_rows(db, rule.series_a)
        rows_b = _current_rows(db, rule.series_b)
        by_date_b = {row.as_of_date: row for row in rows_b}
        for row_a in rows_a:
            row_b = by_date_b.get(row_a.as_of_date)
            if row_b is None:
                continue
            if abs(Decimal(row_a.value) - Decimal(row_b.value)) <= rule.tolerance:
                continue
            for row, other in ((row_a, row_b), (row_b, row_a)):
                flags = list(row.quality_flags)
                if QF_CROSS_SOURCE_MISMATCH not in flags:
                    flags.append(QF_CROSS_SOURCE_MISMATCH)
                    row.quality_flags = flags
                attrs = dict(row.attributes)
                attrs["cross_source_mismatch"] = {
                    "against": other.series_code,
                    "other_value": str(other.value),
                    "tolerance": str(rule.tolerance),
                }
                row.attributes = attrs
            result.warnings.append(
                f"cross-source mismatch on {row_a.as_of_date.isoformat()}: "
                f"{rule.series_a}={row_a.value} vs {rule.series_b}={row_b.value} "
                f"(tolerance {rule.tolerance})"
            )


def _current_rows(db: Session, series_code: str) -> list[DeskObservation]:
    return list(
        db.scalars(
            select(DeskObservation).where(
                DeskObservation.series_code == series_code,
                DeskObservation.superseded_by.is_(None),
            )
        )
    )


def _write_observation(
    db: Session, *, capture: DeskSourceCapture, draft: ObservationDraft
) -> DeskObservation:
    """Append-only write with current-generation supersession — the
    ``observations.record_manual_observation`` idiom, with capture lineage."""
    row = DeskObservation(
        id=new_uuid7(),
        capture_id=capture.id,
        series_code=draft.series_code,
        as_of_date=draft.as_of_date,
        value=quantize_value(draft.value),
        unit=draft.unit,
        attributes=draft.attributes,
        quality_flags=list(draft.quality_flags),
        entered_by=None,
    )
    current = list(
        db.scalars(
            select(DeskObservation).where(
                DeskObservation.series_code == draft.series_code,
                DeskObservation.as_of_date == draft.as_of_date,
                DeskObservation.superseded_by.is_(None),
            )
        )
    )
    for old in current:
        old.superseded_by = row.id
    if current:
        db.flush()
    db.add(row)
    db.flush()
    return row


def ingest_capture(db: Session, capture: DeskSourceCapture, raw: bytes) -> ParseResult:
    """Parse one capture's raw bytes and write its observations.

    Runs the registered parser, applies the validation layer (dedup/conflict
    policy, range bounds, staleness, cross-source reconciliation), writes
    observations with supersession, and stamps ``capture.status``:
    ``parsed`` when anything usable came out, ``failed`` on a parser error
    or an empty error-bearing result.
    """
    # Deferred deliberately: the registry module imports every parser,
    # and the parsers import this module.
    from app.services.market_desk.sources import SOURCE_REGISTRY  # noqa: PLC0415

    spec = SOURCE_REGISTRY.get(capture.source_key)
    if spec is None or spec.parser is None:
        message = f"no parser registered for source_key={capture.source_key!r}"
        capture.status = "failed"
        capture.parse_error = message
        db.flush()
        return ParseResult(errors=[message])

    context = ParseContext(as_of_date=capture.as_of_date, source_url=capture.source_url)
    try:
        result = spec.parser(raw, context=context)
    except Exception as exc:  # noqa: BLE001 - a parser crash must stamp the capture
        message = f"{type(exc).__name__}: {exc}"
        capture.status = "failed"
        capture.parse_error = message
        db.flush()
        return ParseResult(errors=[message])

    result.observations = dedupe_and_resolve_conflicts(result.observations, result)
    apply_range_bounds(result.observations, result)
    apply_staleness(
        result.observations, result, cadence=spec.cadence, as_of=capture.as_of_date
    )

    if result.errors and not result.observations:
        capture.status = "failed"
        capture.parse_error = "; ".join(result.errors)
        db.flush()
        return result

    for draft in result.observations:
        _write_observation(db, capture=capture, draft=draft)

    run_reconciliations(db, {d.series_code for d in result.observations}, result)

    capture.parser_version = spec.parser_version
    capture.status = "parsed"
    capture.parse_error = "; ".join(result.errors) if result.errors else None
    db.flush()
    return result
