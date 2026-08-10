"""The nightly scheduled market-desk capture job (founder's directive).

Scrape BoG/GFIM/GSS after Ghana publication hours every day and have new
rates staged, computed, and PENDING REVIEW, so the desk publishes them for
the next business day with one maker-checker action:

1. **Capture** — for every due source in ``SOURCE_REGISTRY`` (per-source
   cadence honored: daily every night; weekly/monthly/per-event only when the
   last parsed capture has aged out — plus a Friday ``auction_pass`` re-pull
   of the auction sources, because the weekly GoG/BoG T-bill tenders clear on
   Fridays), fetch via the proven capture client, persist the raw bytes as a
   ``DeskSourceCapture``, and ingest to observations. A source that fails is
   recorded as a ``failed`` capture and NEVER aborts the other sources — the
   morning desk sees exactly which series need the manual fallback (spec §3).
2. **Stage** — when (and only when) an APPROVED methodology is effective for
   the COB date, open the day's draft determination, run the calculation
   pipeline, and submit it for review. The morning queue then shows it
   PENDING REVIEW with QA results attached.

Governance boundaries, deliberately hard:

- No approved methodology → the job stops after observations. A robot cannot
  bypass Track-2 approval (spec §5) by minting determinations against a draft.
- The job NEVER publishes. Approval and publication are the human
  maker-checker actions, always.

HAZARD (ships-together invariant): the ``desk_capture`` job type lives in
THREE places that must change together — ``job_queue.JOB_TYPES``,
``worker.HANDLERS``, and ``scheduler.any_scheduling_enabled`` (via
``DESK_CAPTURE_ENABLED``). Missing the handler strands jobs "queued" forever;
missing the flag strands the feature with no tick chain. The parity test in
``tests/services/test_desk_capture_job.py`` enforces the first two.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.models import DeskSourceCapture, Job
from app.services import job_queue
from app.services.market_desk import calculation, determinations, register
from app.services.market_desk.sources import SOURCE_REGISTRY, ingest_capture
from app.services.market_desk.sources.fetch import (
    DeskSession,
    FetchError,
    client_kwargs,
    fetch_source,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.services.market_desk.sources import SourceSpec
    from app.services.market_desk.sources.fetch import RawFetch

DESK_CAPTURE = "desk_capture"

#: The robot's provenance identity on captures and drafted determinations —
#: a system identity (the ``register._BOOTSTRAP_PROPOSER`` idiom), so any
#: human desk checker satisfies maker-checker on what the job prepared.
CAPTURE_IDENTITY = "desk-capture-job@aequoros.system"

_FRIDAY = 4  # date.weekday() value

#: Days since the last PARSED capture after which a non-daily source is due
#: again. Weekly uses 6 (not 7) so nightly-run jitter can never drift a
#: weekly source past its slot; ``per_event`` (the MPR table) gets the cheap
#: weekly re-check — MPC decisions land on ~8-week announced dates, but the
#: pull is one paged JSON table. Failed captures do not count: a source that
#: failed tonight stays due tomorrow.
_DUE_AFTER_DAYS: dict[str, int] = {"weekly": 6, "monthly": 28, "per_event": 6}

#: Sources re-pulled on the Friday ``auction_pass`` regardless of staleness:
#: the weekly GoG/BoG T-bill auctions clear on Fridays, and both the result
#: notices and the tender-rate tables carry that day's prints.
AUCTION_PASS_SOURCE_KEYS: tuple[str, ...] = (
    "bog_tbill_rates",
    "bog_bill_rates",
    "bog_gog_auction_pdf",
    "bog_bog_auction_pdf",
)

#: ``RawFetch.meta['kind']`` values that carry parseable data. Fetch runs
#: also return lineage-only artifacts (tender pages, FileBird listings);
#: fetches with NO kind at all are the wpDataTables JSON pages — every one
#: is data.
_DATA_KINDS = frozenset({"result_pdf", "publication_pdf", "report_file", "pxweb_data"})

#: Monthly BoG publications whose URL embeds the edition month; the nightly
#: job tries the current month's edition first (it appears mid-month) and
#: falls back to the previous month.
_MONTHLY_EDITION_SOURCES = ("bog_apr_pdf", "bog_sefd_pdf")

#: Raw payloads at or under this size ride inline on the capture row
#: (base64); larger ones keep only the SHA-256 (the model's storage_path
#: seam is a later phase). The harvested BoG/GFIM artifacts are well under it.
_INLINE_PAYLOAD_MAX_BYTES = 2_000_000


def enqueue_due_desk_capture(
    session: Session, organization_id: str, *, now: datetime | None = None
) -> Job | None:
    """Enqueue tonight's capture job — at most ONE per calendar day, globally.

    The desk is platform-global (desk tables are not tenant-scoped), so the
    daily-uniqueness check deliberately ignores organization: whichever org's
    hourly tick first crosses ``DESK_CAPTURE_HOUR_UTC`` carries the job and
    every later tick — same org or not — finds the day's coalesce key taken.
    A completed job keeps its key, so this existence check (not enqueue
    coalescing, which only merges still-queued jobs) is what holds the daily
    cadence — the ``reporting_deadline_scan`` idiom.
    """
    settings = get_settings().desk
    if not settings.desk_capture_enabled:
        return None
    now = now or utc_now()
    if now.hour < settings.desk_capture_hour_utc:
        return None
    day = now.date()
    coalesce_key = f"desk_capture:{day.isoformat()}"
    existing = session.scalar(
        select(Job.id)
        .where(Job.job_type == DESK_CAPTURE, Job.coalesce_key == coalesce_key)
        .limit(1)
    )
    if existing is not None:
        return None
    payload: dict[str, Any] = {"cob_date": day.isoformat()}
    if day.weekday() == _FRIDAY:
        payload["auction_pass"] = True
    return job_queue.enqueue(
        session,
        organization_id,
        DESK_CAPTURE,
        payload=payload,
        coalesce_key=coalesce_key,
    )


def run_desk_capture(session: Session, job: Job) -> None:
    """Worker handler: capture every due source, then stage the day's
    determination for the morning review queue. Never publishes."""
    settings = get_settings().desk
    if not settings.desk_capture_enabled:
        # Kill-switch honored at run time too: a job queued before the flag
        # was flipped off must not scrape (the SMTP-mirror re-check idiom).
        job.progress = {"skipped": "desk_capture_disabled"}
        return
    raw_cob = job.payload.get("cob_date")
    cob = date.fromisoformat(str(raw_cob)) if raw_cob else utc_now().date()
    auction_pass = bool(job.payload.get("auction_pass"))
    allowlist = settings.capture_source_allowlist

    sources_summary: dict[str, dict[str, Any]] = {}
    for source_key, spec in SOURCE_REGISTRY.items():
        if allowlist is not None and source_key not in allowlist:
            sources_summary[source_key] = {"status": "skipped_not_allowed"}
            continue
        due, due_reason = _is_due(session, spec, cob, auction_pass=auction_pass)
        if not due:
            sources_summary[source_key] = {"status": "skipped_not_due", "reason": due_reason}
            continue
        sources_summary[source_key] = _capture_source(session, spec, cob, due_reason)
        # Commit per source: a crash (or an operator watching the console)
        # sees each source land as it finishes, and a later source's failure
        # can never roll back an earlier source's captures.
        session.commit()

    determination_summary = _stage_determination(session, cob)
    session.commit()

    job.progress = {
        "cob_date": cob.isoformat(),
        "auction_pass": auction_pass,
        "sources": sources_summary,
        "determination": determination_summary,
    }


# ---------------------------------------------------------------------------
# Capture stage
# ---------------------------------------------------------------------------


def _is_due(db: Session, spec: SourceSpec, cob: date, *, auction_pass: bool) -> tuple[bool, str]:
    """Cadence-aware due-ness from the latest PARSED capture per source.

    Daily sources run every night. The Friday ``auction_pass`` forces the
    auction sources due. Everything else is due when never captured or when
    the last parsed capture has aged past its cadence window — failed
    captures deliberately don't count, so a failing source retries nightly.
    """
    if spec.cadence == "daily":
        return True, "daily"
    if auction_pass and spec.source_key in AUCTION_PASS_SOURCE_KEYS:
        return True, "auction_pass"
    last_parsed = db.scalar(
        select(func.max(DeskSourceCapture.captured_at)).where(
            DeskSourceCapture.source_key == spec.source_key,
            DeskSourceCapture.status == "parsed",
        )
    )
    if last_parsed is None:
        return True, "never_captured"
    age_days = (cob - last_parsed.date()).days
    limit = _DUE_AFTER_DAYS.get(spec.cadence, 6)
    if age_days >= limit:
        return True, f"stale_{age_days}d"
    return False, f"fresh_{age_days}d"


def _capture_source(db: Session, spec: SourceSpec, cob: date, due_reason: str) -> dict[str, Any]:
    """Fetch, persist, and ingest one source. Per-source failure is recorded
    on a ``failed`` DeskSourceCapture row and NEVER propagates — one broken
    site must not cost the desk the rest of the night's data."""
    try:
        fetches = _fetch(spec, cob)
        data_fetches = _data_fetches(fetches)
        if not data_fetches:
            raise FetchError("fetch returned no data artifact")
    except Exception as exc:  # noqa: BLE001 - isolation is the contract here
        _record_failed_fetch(db, spec, cob, exc)
        return {
            "status": "failed",
            "due": due_reason,
            "error": f"{type(exc).__name__}: {exc}",
        }
    observations = 0
    warnings = 0
    parse_errors: list[str] = []
    for raw_fetch in data_fetches:
        capture = _persist_capture(db, spec, cob, raw_fetch)
        result = ingest_capture(db, capture, raw_fetch.content)
        observations += len(result.observations)
        warnings += len(result.warnings)
        parse_errors.extend(result.errors)
    summary: dict[str, Any] = {
        "status": "captured",
        "due": due_reason,
        "captures": len(data_fetches),
        "observations": observations,
        "warnings": warnings,
    }
    if parse_errors:
        summary["parse_errors"] = parse_errors
    return summary


def _host_for(source_key: str) -> str:
    if source_key.startswith("bog_"):
        return "www.bog.gov.gh"
    if source_key.startswith("gfim_"):
        return "gfim.com.gh"
    return "statsbank.statsghana.gov.gh"


def _fetch(spec: SourceSpec, cob: date) -> list[RawFetch]:
    """Run the proven capture mechanics for one source (real network).

    Tests monkeypatch module-level ``fetch_source`` (or inject an
    ``httpx.MockTransport`` client); the per-host client honors
    ``client_kwargs`` — BoG's broken TLS chain and browser-UA requirement
    live there, never here.
    """
    with httpx.Client(**client_kwargs(_host_for(spec.source_key))) as client:
        desk_session = DeskSession(client)
        if spec.source_key in _MONTHLY_EDITION_SOURCES:
            try:
                return fetch_source(spec.source_key, desk_session, period=cob)
            except Exception:  # noqa: BLE001 - edition-not-yet-published fallback
                return fetch_source(spec.source_key, desk_session, period=_previous_month(cob))
        if spec.source_key.startswith("gfim_"):
            return fetch_source(spec.source_key, desk_session, year=str(cob.year))
        return fetch_source(spec.source_key, desk_session)


def _previous_month(day: date) -> date:
    return day.replace(day=1) - timedelta(days=1)


def _data_fetches(fetches: list[RawFetch]) -> list[RawFetch]:
    """The parseable artifacts of a fetch run.

    Kinded fetches keep only the data kinds (tender pages and FileBird
    listings are lineage, not data); kind-less fetches are wpDataTables JSON
    pages — every page is data and each ingests independently (observation
    writes supersede by series/date).
    """
    kinded = [fetch for fetch in fetches if fetch.meta.get("kind") in _DATA_KINDS]
    if kinded:
        return kinded
    return [fetch for fetch in fetches if "kind" not in fetch.meta]


def _persist_capture(
    db: Session, spec: SourceSpec, cob: date, raw_fetch: RawFetch
) -> DeskSourceCapture:
    """Persist one raw artifact as the lineage root of its observations."""
    if len(raw_fetch.content) <= _INLINE_PAYLOAD_MAX_BYTES:
        payload: dict[str, Any] = {
            "content_base64": base64.b64encode(raw_fetch.content).decode("ascii"),
            "meta": raw_fetch.meta,
        }
    else:
        payload = {
            "meta": raw_fetch.meta,
            "content_omitted": "exceeds inline cap; sha256 retained",
        }
    capture = DeskSourceCapture(
        id=new_uuid7(),
        source_key=spec.source_key,
        as_of_date=cob,
        source_url=raw_fetch.url[:500],
        content_sha256=raw_fetch.sha256,
        payload=payload,
        parser_version="pending",  # stamped by ingest_capture
        status="captured",
        created_by=CAPTURE_IDENTITY,
    )
    db.add(capture)
    db.flush()
    return capture


def _record_failed_fetch(
    db: Session, spec: SourceSpec, cob: date, exc: Exception
) -> DeskSourceCapture:
    """A fetch that produced no bytes still leaves a lineage row: a ``failed``
    capture carrying the error, so the morning desk sees exactly which source
    needs the manual-entry fallback (spec §3). ``_is_due`` ignores failed
    rows, so the source stays due tomorrow night."""
    message = f"{type(exc).__name__}: {exc}"
    capture = DeskSourceCapture(
        id=new_uuid7(),
        source_key=spec.source_key,
        as_of_date=cob,
        source_url=None,
        content_sha256=hashlib.sha256(b"").hexdigest(),
        payload={"fetch_error": message},
        parser_version="pending",
        status="failed",
        parse_error=f"fetch failed: {message}",
        created_by=CAPTURE_IDENTITY,
    )
    db.add(capture)
    db.flush()
    return capture


# ---------------------------------------------------------------------------
# Determination stage
# ---------------------------------------------------------------------------


def _stage_determination(db: Session, cob: date) -> dict[str, Any]:
    """Draft, compute, and submit the day's determination for human review.

    Governance boundaries, deliberately hard:

    - No APPROVED methodology effective for the COB → observations only
      (recorded as skipped). Track-2 approval cannot be bypassed by a robot.
    - Any existing determination for the COB (whatever its status) is
      respected: a re-run never duplicates the draft, and a human rejection
      is never steamrolled by tonight's job.
    - Success ends at ``pending_review`` — with QA results attached for the
      morning checker. The job NEVER publishes.
    """
    methodology = register.get_active(db, register.DEFAULT_METHODOLOGY_CODE, cob)
    if methodology is None:
        return {"status": "skipped", "reason": "no_approved_methodology"}
    existing = determinations.list_determinations(db, cob_date=cob)
    if existing:
        return {
            "status": "skipped",
            "reason": "determination_exists",
            "existing": [
                {"determination_id": str(row.id), "status": row.status} for row in existing
            ],
        }
    try:
        draft = determinations.create_draft(db, cob_date=cob, prepared_by=CAPTURE_IDENTITY)
        calculation.compute_determination(db, draft, methodology=methodology)
        determinations.submit_for_review(db, draft.id)
    except (HTTPException, calculation.CalculationError) as exc:
        # Structural refusals (no observations yet, undeclared series, ...)
        # are a nightly fact of life, not a job failure: the captures are
        # already committed, and retrying without new data cannot help.
        db.rollback()
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return {"status": "failed", "reason": str(detail)}
    return {
        "status": "pending_review",
        "determination_id": str(draft.id),
        "input_digest": draft.input_digest,
        "qa_passed": draft.derived_values.get("qa_passed"),
    }
