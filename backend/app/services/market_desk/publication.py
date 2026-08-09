"""Publication: fan one approved determination out to every tenant (spec §2).

The desk NEVER writes canonical market-data rows directly — it publishes by
calling ``execute_pull`` per bank, exactly like a vendor adapter, so every
desk-published row carries the full batch/lineage/raw-tier provenance the
no-seeding order demands, supersedes cleanly by natural key, and triggers the
same debounced ``pipeline_refresh`` recalculation ingestion does.

Fan-out runs IN-PROCESS (a synchronous loop over banks inside the publish
action) — deliberately NO new job type this phase. The shared jobs table has
a stale-prod-worker hazard: a new job type is inert (or worse, poison-pilled)
until the worker deployment that knows it ships, so a future async
``desk_publication`` job must land JOB_TYPES (app/services/job_queue.py) and
worker HANDLERS (app/worker.py) in the SAME deploy — see the orphaned
``notification_email_mirror`` precedent, which exists in JOB_TYPES with no
handler.

Partial failure is the contract, not an error: per-bank failures are
recorded (bank-facing strings only) and successful banks are never rolled
back — re-publishing the same determination is idempotent because the pull
runner supersedes by (currency, curve_name) / index / pair natural keys.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import select

from app.adapters.market_data.aequor_desk.adapter import (
    ADAPTER_VERSION,
    VENDOR,
    build_extraction,
    determination_scopes,
)
from app.adapters.market_data.errors import MarketDataError
from app.adapters.market_data.pull_runner import execute_pull
from app.models import Bank, DeskDetermination, DeskPublication, Organization
from app.services.ingestion import bank_slug
from app.services.market_desk import determinations, register

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# The one bank-facing string a generic fan-out failure may surface; internals
# go to the log (the adapter seam's §12.3 leak rule applies to the desk too).
_GENERIC_ERROR = "Desk publication to this institution failed; AequorOS has been notified."


def _publishable(db: Session, determination: DeskDetermination) -> None:
    # 'published' is deliberately allowed alongside 'approved': re-publishing
    # heals partial fan-outs (supersession makes re-delivery harmless).
    if determination.status not in ("approved", "published"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Determination is {determination.status!r}; only an approved (or "
                "previously published) determination can be published."
            ),
        )
    methodology = register.get_version(
        db, determination.methodology_code, determination.methodology_version
    )
    if methodology.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Methodology {determination.methodology_code!r} "
                f"v{determination.methodology_version} is {methodology.status!r}; "
                "publication requires an approved methodology version."
            ),
        )
    if not determination.derived_values:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Determination carries no derived values; nothing to publish.",
        )
    if not determination_scopes(determination):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Determination's derived values map to no publishable data scopes.",
        )


def publish(db: Session, determination_id: Any, *, actor: str) -> DeskPublication:
    """Publish one determination into EVERY bank's canonical store.

    Entitlements (spec §10: not every tenant necessarily gets every dataset)
    are a later phase — this phase skips no bank. Each bank is one
    ``execute_pull`` (which commits its own batch); a per-bank failure rolls
    back only that bank's partial work and the loop continues.
    """
    determination = determinations.get(db, determination_id)
    _publishable(db, determination)
    scopes = determination_scopes(determination)
    # Make the approved state durable before fan-out: per-bank failures roll
    # the session back, and pending pre-publish state must never ride on the
    # first bank's commit.
    db.commit()

    banks = list(
        db.scalars(
            select(Bank)
            .join(Organization, Bank.organization_id == Organization.id)
            .order_by(Bank.id)
        )
    )

    results: list[dict[str, Any]] = []
    succeeded = 0
    for bank in banks:
        try:
            slug = bank_slug(db, bank)
            pull = execute_pull(
                db,
                organization_id=bank.organization_id,
                bank=bank,
                bank_slug=slug,
                vendor=VENDOR,
                adapter_version=ADAPTER_VERSION,
                scopes=scopes,
                as_of_date=determination.cob_date,
                extract=lambda scope: build_extraction(determination, scope),  # noqa: B023 - consumed synchronously inside execute_pull
                quota_units=0,
                actor_user_id=None,
            )
        except MarketDataError as exc:
            db.rollback()
            logger.warning(
                "Desk publication failed for bank %s: %s", bank.id, exc.internal_detail
            )
            results.append(
                {"bank_id": bank.id, "status": "failed", "error": exc.bank_facing.message}
            )
            continue
        except Exception:
            db.rollback()
            logger.exception("Desk publication failed for bank %s", bank.id)
            results.append({"bank_id": bank.id, "status": "failed", "error": _GENERIC_ERROR})
            continue
        if pull.errors:
            # execute_pull commits partial scope failures as a terminal batch;
            # surface them but count the bank by what actually landed.
            outcome = "failed" if pull.canonical_records_produced == 0 else "complete"
            entry: dict[str, Any] = {
                "bank_id": bank.id,
                "ingestion_batch_id": pull.batch_id,
                "status": outcome,
                "error": "; ".join(pull.errors),
            }
        else:
            outcome = "complete"
            entry = {
                "bank_id": bank.id,
                "ingestion_batch_id": pull.batch_id,
                "status": "complete",
            }
        if outcome == "complete":
            succeeded += 1
        results.append(entry)

    if not banks or succeeded == len(banks):
        publication_status = "complete"
    elif succeeded:
        publication_status = "partial"
    else:
        publication_status = "failed"

    # db may have been rolled back by a trailing failure; re-fetch defensively.
    determination = determinations.get(db, determination.id)
    if publication_status != "failed" and determination.status != "published":
        determinations.mark_published(db, determination)

    publication = DeskPublication(
        determination_id=determination.id,
        published_by=actor,
        results=results,
        status=publication_status,
    )
    db.add(publication)
    db.commit()
    return publication


def list_publications(
    db: Session, *, determination_id: Any | None = None
) -> list[DeskPublication]:
    query = select(DeskPublication).order_by(DeskPublication.published_at.desc())
    if determination_id is not None:
        query = query.where(DeskPublication.determination_id == determination_id)
    return list(db.scalars(query))
