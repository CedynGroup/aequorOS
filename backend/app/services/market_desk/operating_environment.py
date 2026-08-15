"""Operating-Environment governed assessments + desk-as-vendor publication.

Spec: ``docs/internal/operating_environment_score.md``. This service:

  1. resolves the auto-pulled inputs — the published sovereign rating (the
     same canonical path ``implied_rating._current_sovereign`` reads) and the
     MPR / policy rate (the ``GHS.MPR`` reference index) — and accepts the
     desk-entered macro + banking-system aggregates and the one judgment
     sub-score;
  2. computes the pure BICRA score (``app.domain.rating.operating_environment``);
  3. persists a GLOBAL governed assessment with maker-checker
     (``draft → pending_review → approved → published``); and
  4. on publish, fans ``GHANA_OPERATING_ENVIRONMENT_SCORE`` (value in ``[0,1]``)
     out to EVERY tenant through the desk-as-vendor ``pull_runner.execute_pull``
     path (``vendor='aequor_desk'`` → ``source_system=AEQUOR_DESK``), so the
     implied-rating model picks it up with no engine change (it already reads
     the index and falls back to a methodology default when none is published).

The desk session is cross-tenant (operator BYPASSRLS), so the sovereign / MPR
resolution deliberately joins across tenants — a jurisdiction-level input is
identical in every tenant's canonical copy. Publication mirrors
``publication.publish``: per-bank ``execute_pull`` (each commits its own
batch), partial failure is the contract, and re-publishing is idempotent
because supersession is keyed by ``(index_code, scenario, horizon)``.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import select

from app.adapters.market_data.aequor_desk.adapter import ADAPTER_VERSION, VENDOR
from app.adapters.market_data.errors import MarketDataError
from app.adapters.market_data.pull_runner import (
    IndexRecord,
    MarketDataBundle,
    ScopeExtraction,
    execute_pull,
)
from app.adapters.market_data.scope_taxonomy import DataScope
from app.db.base import utc_now
from app.domain.rating.operating_environment import (
    DEFAULT_PARAMETERS,
    JUDGMENT_INPUT_CODES,
    THRESHOLD_INPUT_CODES,
    OperatingEnvironmentError,
    OperatingEnvironmentInputs,
    OperatingEnvironmentResult,
    compute_operating_environment,
    normalize_sovereign_category,
)
from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalMarketIndex,
    DeskObservation,
    DeskOperatingEnvironmentAssessment,
    Jurisdiction,
    Organization,
)
from app.services.ingestion import bank_slug

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# The published jurisdiction index the implied-rating model reads.
INDEX_CODE = "GHANA_OPERATING_ENVIRONMENT_SCORE"
# The MPR reference-rate series (percent), auto-pulled when not desk-entered.
POLICY_RATE_SERIES = "GHS.MPR"
# The carrier scope for the fan-out envelope (lineage / raw tier / cache): a
# macro-Ghana scope. It is only the envelope — supersession keys on the index
# code, so it never collides with the MPR reference rate on the same scope.
_PUBLISH_SCOPE = DataScope.MACRO_GHANA_POLICY_RATE_PATH
_ACCEPTED_VALIDATION = ("accepted", "warning")
_GENERIC_ERROR = (
    "Operating-environment publication to this institution failed; AequorOS has been notified."
)


# ---------------------------------------------------------------------------
# Input resolution (auto-pull sovereign + MPR; accept desk-entered inputs).
# ---------------------------------------------------------------------------


def _decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Operating-environment input {label!r} must be numeric.",
        ) from exc


def _missing(label: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Operating-environment assessment requires {label}; enter it or publish it "
            "before retrying."
        ),
    )


def _resolve_sovereign(
    db: Session, jurisdiction_code: str, cob_date: date, explicit: str | None
) -> tuple[str, dict[str, str]]:
    """Sovereign category + provenance: desk-entered override else auto-pull.

    Auto-pull reads the latest current-generation published rating for the
    jurisdiction's ``sovereign_rating_issuer`` (cross-tenant — the sovereign is
    identical in every tenant's canonical copy).
    """
    if explicit is not None and explicit.strip():
        try:
            category = normalize_sovereign_category(explicit)
        except OperatingEnvironmentError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        return category, {"source": "desk_entered", "rating": explicit.strip()}

    jurisdiction = db.get(Jurisdiction, jurisdiction_code)
    issuer = jurisdiction.sovereign_rating_issuer if jurisdiction is not None else None
    if not issuer:
        raise _missing(
            f"a sovereign_rating_issuer in the {jurisdiction_code} jurisdiction registry "
            "(or an explicit sovereign_rating)"
        )
    row = db.scalar(
        select(CanonicalCounterpartyRating)
        .where(
            CanonicalCounterpartyRating.issuer == issuer,
            CanonicalCounterpartyRating.as_of_date <= cob_date,
            CanonicalCounterpartyRating.superseded_by.is_(None),
            CanonicalCounterpartyRating.validation_status.in_(_ACCEPTED_VALIDATION),
        )
        .order_by(
            CanonicalCounterpartyRating.as_of_date.desc(),
            CanonicalCounterpartyRating.ingested_at.desc(),
        )
        .limit(1)
    )
    if row is None:
        raise _missing(f"a published {issuer} sovereign rating (or an explicit sovereign_rating)")
    try:
        category = normalize_sovereign_category(row.rating)
    except OperatingEnvironmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return category, {
        "source": "canonical_rating",
        "issuer": row.issuer,
        "agency": row.agency,
        "rating": row.rating,
        "as_of_date": row.as_of_date.isoformat(),
    }


def _resolve_policy_rate(
    db: Session, cob_date: date, explicit: Decimal | None
) -> tuple[Decimal, dict[str, str]]:
    """MPR (percent) + provenance: desk-entered override else auto-pull.

    Prefers the published ``GHS.MPR`` reference index; falls back to a current
    desk observation of the same series. Both carry the rate in percent.
    """
    if explicit is not None:
        return explicit, {"source": "desk_entered"}
    index_row = db.scalar(
        select(CanonicalMarketIndex)
        .where(
            CanonicalMarketIndex.index_code == POLICY_RATE_SERIES,
            CanonicalMarketIndex.scenario == "base",
            CanonicalMarketIndex.as_of_date <= cob_date,
            CanonicalMarketIndex.superseded_by.is_(None),
            CanonicalMarketIndex.validation_status.in_(_ACCEPTED_VALIDATION),
        )
        .order_by(
            CanonicalMarketIndex.as_of_date.desc(),
            CanonicalMarketIndex.ingested_at.desc(),
        )
        .limit(1)
    )
    if index_row is not None:
        return _decimal(index_row.value, POLICY_RATE_SERIES), {
            "source": "market_index",
            "index_code": POLICY_RATE_SERIES,
            "as_of_date": index_row.as_of_date.isoformat(),
        }
    observation = db.scalar(
        select(DeskObservation)
        .where(
            DeskObservation.series_code == POLICY_RATE_SERIES,
            DeskObservation.as_of_date <= cob_date,
            DeskObservation.superseded_by.is_(None),
        )
        .order_by(DeskObservation.as_of_date.desc())
        .limit(1)
    )
    if observation is not None:
        return _decimal(observation.value, POLICY_RATE_SERIES), {
            "source": "desk_observation",
            "series_code": POLICY_RATE_SERIES,
            "as_of_date": observation.as_of_date.isoformat(),
        }
    raise _missing(
        f"a published {POLICY_RATE_SERIES} policy rate (or an explicit policy_rate_pct)"
    )


def _resolve_inputs(
    db: Session, jurisdiction_code: str, cob_date: date, inputs: dict[str, Any]
) -> tuple[OperatingEnvironmentInputs, dict[str, Any]]:
    """Build the domain inputs + a value-based snapshot with provenance."""
    sovereign_category, sovereign_prov = _resolve_sovereign(
        db, jurisdiction_code, cob_date, inputs.get("sovereign_rating")
    )
    explicit_rate = inputs.get("policy_rate_pct")
    policy_rate, policy_prov = _resolve_policy_rate(
        db,
        cob_date,
        None if explicit_rate is None else _decimal(explicit_rate, "policy_rate_pct"),
    )

    observations: dict[str, Decimal] = {}
    for code in THRESHOLD_INPUT_CODES:
        if code == "policy_rate_pct":
            observations[code] = policy_rate
            continue
        if code not in inputs or inputs[code] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Operating-environment input {code!r} is required.",
            )
        observations[code] = _decimal(inputs[code], code)

    judgments: dict[str, int] = {}
    for code in JUDGMENT_INPUT_CODES:
        if code not in inputs or inputs[code] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Operating-environment judgment sub-score {code!r} is required.",
            )
        judgments[code] = int(inputs[code])

    domain_inputs = OperatingEnvironmentInputs(
        observations=observations,
        judgments=judgments,
        sovereign_category=sovereign_category,
    )
    snapshot: dict[str, Any] = {
        "jurisdiction_code": jurisdiction_code,
        "cob_date": cob_date.isoformat(),
        "observations": {code: str(value) for code, value in observations.items()},
        "judgments": dict(judgments),
        "sovereign_category": sovereign_category,
        "provenance": {"sovereign": sovereign_prov, "policy_rate": policy_prov},
    }
    return domain_inputs, snapshot


def _breakdown_payload(
    result: OperatingEnvironmentResult, provenance: dict[str, Any]
) -> dict[str, Any]:
    return {
        "methodology_version": result.parameters_version,
        "score": str(result.score),
        "strength_raw": str(result.strength_raw),
        "composite_risk": str(result.composite_risk),
        "risk_min": str(result.risk_min),
        "risk_max": str(result.risk_max),
        "sovereign_category": result.sovereign_category,
        "governor_cap": str(result.governor_cap),
        "governor_applied": result.governor_applied,
        "input_digest": result.input_digest,
        "provenance": provenance,
        "pillars": [
            {
                "code": pillar.code,
                "score": str(pillar.score),
                "weight": str(pillar.weight),
                "sub_factors": [
                    {
                        "code": sub_factor.code,
                        "score": str(sub_factor.score),
                        "weight": str(sub_factor.weight),
                        "inputs": [
                            {
                                "code": item.code,
                                "kind": item.kind,
                                "value": str(item.value),
                                "sub_score": item.sub_score,
                                "weight": str(item.weight),
                            }
                            for item in sub_factor.inputs
                        ],
                    }
                    for sub_factor in pillar.sub_factors
                ],
            }
            for pillar in result.pillars
        ],
    }


def _compute(
    db: Session, jurisdiction_code: str, cob_date: date, inputs: dict[str, Any]
) -> tuple[OperatingEnvironmentResult, dict[str, Any], dict[str, Any]]:
    domain_inputs, snapshot = _resolve_inputs(db, jurisdiction_code, cob_date, inputs)
    try:
        result = compute_operating_environment(domain_inputs, DEFAULT_PARAMETERS)
    except OperatingEnvironmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    breakdown = _breakdown_payload(result, snapshot["provenance"])
    return result, snapshot, breakdown


# ---------------------------------------------------------------------------
# Public service surface.
# ---------------------------------------------------------------------------


def compute_preview(
    db: Session, *, jurisdiction_code: str, cob_date: date, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Resolve inputs, compute, and return the breakdown — persists nothing."""
    result, snapshot, breakdown = _compute(db, jurisdiction_code, cob_date, inputs)
    return {
        "jurisdiction_code": jurisdiction_code,
        "cob_date": cob_date,
        "methodology_version": result.parameters_version,
        "score": result.score,
        "input_digest": result.input_digest,
        "inputs": snapshot,
        "breakdown": breakdown,
    }


def stage_draft(
    db: Session,
    *,
    jurisdiction_code: str,
    cob_date: date,
    inputs: dict[str, Any],
    proposed_by: str,
) -> DeskOperatingEnvironmentAssessment:
    """Compute and persist a DRAFT (never approved, never published)."""
    result, snapshot, breakdown = _compute(db, jurisdiction_code, cob_date, inputs)
    row = DeskOperatingEnvironmentAssessment(
        jurisdiction_code=jurisdiction_code,
        cob_date=cob_date,
        methodology_version=result.parameters_version,
        input_snapshot=snapshot,
        input_digest=result.input_digest,
        computed_breakdown=breakdown,
        score=result.score,
        status="draft",
        proposed_by=proposed_by,
    )
    db.add(row)
    db.flush()
    return row


def _get_or_404(db: Session, assessment_id: Any) -> DeskOperatingEnvironmentAssessment:
    row = db.get(DeskOperatingEnvironmentAssessment, assessment_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Operating-environment assessment does not exist.",
        )
    return row


def _require_status(row: DeskOperatingEnvironmentAssessment, *allowed: str) -> None:
    if row.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Operating-environment assessment is {row.status!r}; this action requires "
                f"{' or '.join(repr(a) for a in allowed)}."
            ),
        )


def get(db: Session, assessment_id: Any) -> DeskOperatingEnvironmentAssessment:
    return _get_or_404(db, assessment_id)


def list_assessments(
    db: Session,
    *,
    jurisdiction_code: str | None = None,
    status_filter: str | None = None,
) -> list[DeskOperatingEnvironmentAssessment]:
    query = select(DeskOperatingEnvironmentAssessment).order_by(
        DeskOperatingEnvironmentAssessment.cob_date.desc(),
        DeskOperatingEnvironmentAssessment.created_at.desc(),
    )
    if jurisdiction_code is not None:
        query = query.where(
            DeskOperatingEnvironmentAssessment.jurisdiction_code == jurisdiction_code
        )
    if status_filter is not None:
        query = query.where(DeskOperatingEnvironmentAssessment.status == status_filter)
    return list(db.scalars(query))


def submit(db: Session, assessment_id: Any) -> DeskOperatingEnvironmentAssessment:
    """Maker step: draft → pending_review."""
    row = _get_or_404(db, assessment_id)
    _require_status(row, "draft")
    row.status = "pending_review"
    db.flush()
    return row


def approve(
    db: Session, assessment_id: Any, *, approved_by: str
) -> DeskOperatingEnvironmentAssessment:
    """Checker step: pending_review → approved. Four-eyes (approver ≠ proposer)."""
    row = _get_or_404(db, assessment_id)
    _require_status(row, "pending_review")
    if approved_by.strip().lower() == row.proposed_by.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "An operating-environment assessment cannot be approved by its proposer "
                "(maker-checker, spec §Governance)."
            ),
        )
    row.status = "approved"
    row.approved_by = approved_by
    row.approved_at = utc_now()
    db.flush()
    return row


def _extract(
    scope: DataScope, *, score: Decimal, assessment: DeskOperatingEnvironmentAssessment
) -> ScopeExtraction:
    """Translate the approved score into one index record for the pull runner."""
    bundle = MarketDataBundle(
        indices=[
            IndexRecord(
                index_code=INDEX_CODE,
                value=score,
                scenario="base",
                horizon_months=None,
                source_reference=f"desk:operating_environment:{assessment.id}",
            )
        ],
        sample_values={INDEX_CODE: f"{score:.4f}"},
    )
    raw_payload = {
        "assessment_id": str(assessment.id),
        "jurisdiction_code": assessment.jurisdiction_code,
        "cob_date": assessment.cob_date.isoformat(),
        "methodology_version": assessment.methodology_version,
        "input_digest": assessment.input_digest,
        "score": str(score),
        "scope": scope.value,
        "inputs": assessment.input_snapshot,
        "breakdown": assessment.computed_breakdown,
    }
    return ScopeExtraction(raw_payload=raw_payload, bundle=bundle)


def publish(db: Session, assessment_id: Any, *, actor: str) -> dict[str, Any]:
    """Fan the approved score out to EVERY tenant as the jurisdiction index.

    Each bank is one ``execute_pull`` (which commits its own batch); a per-bank
    failure rolls back only that bank's partial work and the loop continues
    (partial success is the contract). Re-publishing is idempotent — the pull
    runner supersedes by the index natural key. ``published`` is accepted
    alongside ``approved`` so a partial fan-out can be healed.
    """
    _ = actor  # recorded by the endpoint's operator-audit row
    row = _get_or_404(db, assessment_id)
    _require_status(row, "approved", "published")
    score = Decimal(str(row.score))
    # Make the approved state durable before fan-out: per-bank failures roll
    # the session back, and pre-publish state must not ride the first commit.
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
                scopes=[_PUBLISH_SCOPE],
                as_of_date=row.cob_date,
                extract=lambda scope, s=score, a=row: _extract(scope, score=s, assessment=a),
                quota_units=0,
                actor_user_id=None,
            )
        except MarketDataError as exc:
            db.rollback()
            logger.warning(
                "Operating-environment publication failed for bank %s: %s",
                bank.id,
                exc.internal_detail,
            )
            results.append(
                {"bank_id": bank.id, "status": "failed", "error": exc.bank_facing.message}
            )
            continue
        except Exception:
            db.rollback()
            logger.exception("Operating-environment publication failed for bank %s", bank.id)
            results.append({"bank_id": bank.id, "status": "failed", "error": _GENERIC_ERROR})
            continue
        if pull.errors:
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

    row = _get_or_404(db, assessment_id)
    if publication_status != "failed" and row.status != "published":
        row.status = "published"
        row.published_at = utc_now()
        db.flush()
    db.commit()
    return {
        "assessment_id": row.id,
        "status": publication_status,
        "banks": len(results),
        "results": results,
    }
