"""The nightly scheduled market-desk capture job (founder's directive).

Scrape BoG/GFIM/GSS after Ghana publication hours every day and stage a
**draft** rates package for the Analyst — never auto-submit for review.

1. **Capture** — for every due source in ``SOURCE_REGISTRY`` (per-source
   cadence honored: daily every night; weekly/monthly/per-event only when the
   last parsed capture has aged out — plus a Friday ``auction_pass`` re-pull
   of the auction sources, because the weekly GoG/BoG T-bill tenders clear on
   Fridays), fetch via the proven capture client, persist the raw bytes as a
   ``DeskSourceCapture``, and ingest to observations. A source that fails is
   recorded as a ``failed`` capture and NEVER aborts the other sources — the
   morning desk sees exactly which series need the manual fallback (spec §3).

   The fetch is **watermarked**: the same stored history that decides whether
   a source is due also bounds how far back it is pulled. A nightly run asks
   the publisher for what happened since the newest date the desk already
   holds from that source, and no further — so a night's cost tracks the
   news, not the archive. With no stored observations there is no watermark
   and the walk is deliberately unbounded: a cold start must backfill in
   full, and a bound invented to avoid one is worse than no bound at all.
   Sources marked ``nightly=False`` sit out the ordinary walk entirely (a
   cheaper sibling serves their series) and run on a cold start or when the
   job payload names them in ``backfill_sources``.
2. **Stage** — when (and only when) an APPROVED methodology is effective for
   the COB date, open the day's draft determination and pre-compute proposed
   rates/curves so the Analyst opens a package with numbers ready. Success
   ends at ``draft`` (``draft_ready`` in job progress). The job NEVER
   submits for review, NEVER approves, and NEVER publishes — the Analyst
   must Review → Adjust → Confirm → Submit; the Supervisor then approves.

Governance boundaries, deliberately hard:

- No approved methodology → the job stops after observations. A robot cannot
  bypass Track-2 approval (spec §5) by minting determinations against a draft.
- The job NEVER advances past draft. Maker (Analyst) and checker (Supervisor)
  are human actions, always.

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
from app.models import DeskObservation, DeskSourceCapture, Job
from app.services import job_queue
from app.services.market_desk import calculation, determinations, register, snippets
from app.services.market_desk.sources import SOURCE_REGISTRY, ingest_capture
from app.services.market_desk.sources.fetch import (
    DeskSession,
    FetchError,
    SourceNotYetPublished,
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
    # An operator asks for an archive walk by naming sources here; nothing
    # else re-reads a backfill source, and the allow-list still gates it.
    backfill_sources = frozenset(
        str(key) for key in (job.payload.get("backfill_sources") or ())
    )
    allowlist = settings.capture_source_allowlist

    sources_summary: dict[str, dict[str, Any]] = {}
    for source_key, spec in SOURCE_REGISTRY.items():
        if allowlist is not None and source_key not in allowlist:
            sources_summary[source_key] = {"status": "skipped_not_allowed"}
            continue
        due, due_reason = _is_due(
            session, spec, cob, auction_pass=auction_pass, backfill_sources=backfill_sources
        )
        if not due:
            sources_summary[source_key] = _skipped_summary(session, spec, due_reason)
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
        "backfill_sources": sorted(backfill_sources),
        "sources": sources_summary,
        "determination": determination_summary,
    }


# ---------------------------------------------------------------------------
# Capture stage
# ---------------------------------------------------------------------------


def _last_parsed_at(db: Session, spec: SourceSpec) -> datetime | None:
    """When this source last PARSED cleanly. Failed captures deliberately do
    not count, so a failing source stays due tomorrow night."""
    return db.scalar(
        select(func.max(DeskSourceCapture.captured_at)).where(
            DeskSourceCapture.source_key == spec.source_key,
            DeskSourceCapture.status == "parsed",
        )
    )


def _source_watermark(db: Session, spec: SourceSpec) -> date | None:
    """The newest as-of date the desk already holds FROM THIS SOURCE.

    Read from the observations' own lineage (``capture_id`` -> capture
    ``source_key``), never from the captures' ``as_of_date``: a capture is
    stamped with the COB it ran on, while an observation carries the date the
    publisher printed — and it is publisher dates a fetch must be bounded by.

    Superseded generations are counted on purpose. The question is not "is
    this value still current" but "have we already downloaded this date",
    and a row that was later corrected still proves the bytes were fetched.

    ``None`` means this source has produced nothing yet — a cold start, where
    the only honest bound is no bound at all.
    """
    return db.scalar(
        select(func.max(DeskObservation.as_of_date))
        .join(DeskSourceCapture, DeskSourceCapture.id == DeskObservation.capture_id)
        .where(DeskSourceCapture.source_key == spec.source_key)
    )


def _is_due(
    db: Session,
    spec: SourceSpec,
    cob: date,
    *,
    auction_pass: bool,
    backfill_sources: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """Cadence-aware due-ness from the latest PARSED capture per source.

    An explicit ``backfill_sources`` request wins over everything below it.
    A ``nightly=False`` source is otherwise skipped: a cheaper sibling covers
    its series every night, so walking it again buys nothing. It still runs
    once when it has never been captured — a source that is never allowed to
    backfill has no history to be cheap about.

    Daily sources run every night. The Friday ``auction_pass`` forces the
    auction sources due. Everything else is due when never captured or when
    the last parsed capture has aged past its cadence window.
    """
    if spec.source_key in backfill_sources:
        return True, "backfill_requested"
    if not spec.nightly:
        cold_start = _last_parsed_at(db, spec) is None
        return (True, "never_captured") if cold_start else (False, "backfill_only")
    if spec.cadence == "daily":
        return True, "daily"
    if auction_pass and spec.source_key in AUCTION_PASS_SOURCE_KEYS:
        return True, "auction_pass"
    return _cadence_due(db, spec, cob)


def _cadence_due(db: Session, spec: SourceSpec, cob: date) -> tuple[bool, str]:
    """Due-ness for a non-daily source: never captured, or aged past its
    cadence window."""
    last_parsed = _last_parsed_at(db, spec)
    if last_parsed is None:
        return True, "never_captured"
    age_days = (cob - last_parsed.date()).days
    limit = _DUE_AFTER_DAYS.get(spec.cadence, 6)
    if age_days >= limit:
        return True, f"stale_{age_days}d"
    return False, f"fresh_{age_days}d"


def _skipped_summary(db: Session, spec: SourceSpec, due_reason: str) -> dict[str, Any]:
    """What a skipped source reports to the morning desk.

    A backfill-only source also reports how far its history reaches, because
    "we did not fetch the archive tonight" is only reassuring next to the
    date the archive currently runs to.
    """
    summary: dict[str, Any] = {"status": "skipped_not_due", "reason": due_reason}
    if due_reason == "backfill_only":
        covered = _source_watermark(db, spec)
        summary["covered_through"] = covered.isoformat() if covered else None
    return summary


def _capture_source(db: Session, spec: SourceSpec, cob: date, due_reason: str) -> dict[str, Any]:
    """Fetch, persist, and ingest one source. Per-source failure is recorded
    on a ``failed`` DeskSourceCapture row and NEVER propagates — one broken
    site must not cost the desk the rest of the night's data.

    A lagging publication that simply has no edition yet
    (``SourceNotYetPublished``) is NOT a failure: it records a soft
    ``pending`` in the summary and leaves NO capture row, so the source stays
    due tomorrow (identical to a not-yet-due source) instead of paging the
    morning desk for a normal BoG/GFIM publication lag."""
    watermark = _source_watermark(db, spec)
    try:
        fetches = _fetch(spec, cob, since=watermark)
        data_fetches = _data_fetches(fetches)
        if not data_fetches:
            raise FetchError("fetch returned no data artifact")
    except SourceNotYetPublished as exc:
        return {
            "status": "pending",
            "due": due_reason,
            "reason": "not_yet_published",
            "detail": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - isolation is the contract here
        _record_failed_fetch(db, spec, cob, exc)
        return {
            "status": "failed",
            "due": due_reason,
            "since": watermark.isoformat() if watermark else None,
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
        # ``since`` is the watermark the fetch was bounded by and ``captures``
        # is how many pages that bound cost — together, the night's answer to
        # "how much did we ask the publisher for".
        "since": watermark.isoformat() if watermark else None,
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


def _fetch(spec: SourceSpec, cob: date, *, since: date | None = None) -> list[RawFetch]:
    """Run the proven capture mechanics for one source (real network).

    ``since`` is the desk's watermark for this source and bounds a paged
    walk to what is new; the single-document sources (a PDF edition, an XLSX
    report) have nothing to bound and ignore it.

    Tests monkeypatch module-level ``fetch_source`` (or inject an
    ``httpx.MockTransport`` client); the per-host client honors
    ``client_kwargs`` — BoG's broken TLS chain and browser-UA requirement
    live there, never here.
    """
    with httpx.Client(**client_kwargs(_host_for(spec.source_key))) as client:
        desk_session = DeskSession(client)
        if spec.source_key in _MONTHLY_EDITION_SOURCES:
            # Notices lag 1–2 months; walk back several months until one URL
            # resolves (BoG 404s future/current months that are not published).
            return _fetch_monthly_edition(spec.source_key, desk_session, cob)
        if spec.source_key.startswith("gfim_"):
            return fetch_source(spec.source_key, desk_session, year=str(cob.year))
        return fetch_source(spec.source_key, desk_session, since=since)


def _previous_month(day: date) -> date:
    return day.replace(day=1) - timedelta(days=1)


#: How many prior calendar months to try for APR/SEFD notice URLs.
_MONTHLY_EDITION_LOOKBACK = 6


def _fetch_monthly_edition(
    source_key: str, desk_session: DeskSession, cob: date
) -> list[RawFetch]:
    """Resolve the most-recent PUBLISHED edition, probing back a bounded window.

    BoG publishes the APR / SEFD notices one to two months in arrears, so the
    current month's slug legitimately 404s — that is the site working as
    designed, not an error. We walk back month by month and return the newest
    edition that actually resolves. If EVERY month in the window 404s, the
    publication just is not out yet: raise ``SourceNotYetPublished`` (soft
    ``pending``). A non-404 failure (bot filter, TLS, network, a page that
    resolved but carried no PDF) is a genuine fault and is raised as-is once
    the window is spent — but only if no edition resolved, so a transient
    hiccup on an older month never masks a good current one.
    """
    period = cob
    hard_error: Exception | None = None
    for _ in range(_MONTHLY_EDITION_LOOKBACK):
        try:
            return fetch_source(source_key, desk_session, period=period)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                hard_error = exc  # a real HTTP fault, not a not-yet-published 404
        except Exception as exc:  # noqa: BLE001 - remember, then try older edition
            hard_error = exc
        period = _previous_month(period)
    if hard_error is not None:
        raise hard_error
    raise SourceNotYetPublished(
        f"{source_key}: no published edition in the "
        f"{_MONTHLY_EDITION_LOOKBACK} months through {cob.strftime('%B %Y')}"
    )


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


def _capture_payload(db: Session, spec: SourceSpec, raw_fetch: RawFetch) -> dict[str, Any]:
    """Where this artifact's bytes go — the three payload shapes of ``snippets``.

    A source re-fetched nightly hands back the same bytes night after night,
    and every copy cost a full base64 inline payload. When an earlier capture
    of the SAME source already holds these exact bytes, this row keeps its
    lineage and defers the payload to that row instead of storing a second
    copy; readers resolve the hop, so the artifact is unchanged for everyone
    who reads it. The saving is in storage only — nothing about what is
    captured, or which row an observation hangs off, changes.
    """
    anchor_id = snippets.find_payload_anchor(
        db,
        source_key=spec.source_key,
        content=raw_fetch.content,
        content_sha256=raw_fetch.sha256,
    )
    if anchor_id is not None:
        return snippets.deferred_payload(anchor_id=anchor_id, meta=raw_fetch.meta)
    if len(raw_fetch.content) <= _INLINE_PAYLOAD_MAX_BYTES:
        return {
            snippets.CONTENT_BASE64: base64.b64encode(raw_fetch.content).decode("ascii"),
            "meta": raw_fetch.meta,
        }
    return {
        "meta": raw_fetch.meta,
        snippets.CONTENT_OMITTED: "exceeds inline cap; sha256 retained",
    }


def _persist_capture(
    db: Session, spec: SourceSpec, cob: date, raw_fetch: RawFetch
) -> DeskSourceCapture:
    """Persist one raw artifact as the lineage root of its observations.

    EVERY artifact gets a row, always — de-duplication skips duplicated
    bytes, never the lineage row an observation points back to.
    """
    payload = _capture_payload(db, spec, raw_fetch)
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
    """Draft and pre-compute the day's determination for Analyst review.

    Governance boundaries, deliberately hard:

    - No APPROVED methodology effective for the COB → observations only
      (recorded as skipped). Track-2 approval cannot be bypassed by a robot.
    - Any existing determination for the COB (whatever its status) is
      respected: a re-run never duplicates the draft, and a human rejection
      is never steamrolled by tonight's job.
    - Success ends at ``draft`` (progress status ``draft_ready``) with
      proposed rates/QA attached. The job NEVER submits for review, never
      approves, and never publishes — that is the Analyst → Supervisor path.
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
    except (HTTPException, calculation.CalculationError) as exc:
        # Structural refusals (no observations yet, undeclared series, ...)
        # are a nightly fact of life, not a job failure: the captures are
        # already committed, and retrying without new data cannot help.
        db.rollback()
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return {"status": "failed", "reason": str(detail)}
    derived = draft.derived_values or {}
    return {
        "status": "draft_ready",
        "determination_id": str(draft.id),
        "input_digest": draft.input_digest,
        "qa_passed": derived.get("qa_passed"),
        "rates_qa_passed": derived.get("rates_qa_passed"),
        "curves_qa_passed": derived.get("curves_qa_passed"),
    }
