"""/operator/v1/desk — the market research desk console API (STAFF surface).

The desk is AequorOS' own operation (spec §4): methodology register,
observations with the manual-entry fallback, Track-1 determinations, and
publication fan-out. Every mutation is recorded through
``record_operator_action`` — the desk's audit trail is part of the IOSCO
control story the spec's §4/§12 governance sections commit to.

The two governance tracks are deliberately DIFFERENT actions here (spec
§11a): weekly determination endpoints never touch methodology parameters,
and methodology endpoints never publish data — an analyst can never change
a parameter "in passing" during a weekly publish.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.models import (
    DeskDetermination,
    DeskMethodology,
    DeskObservation,
    DeskPublication,
    DeskSourceCapture,
)
from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services.methodology_pdf import render_methodology_pdf
from app.schemas.market_desk import (
    DeskCaptureContentView,
    DeskCaptureListRead,
    DeskCaptureRead,
    DeskDeterminationCreate,
    DeskDeterminationListRead,
    DeskDeterminationRead,
    DeskDeterminationReject,
    DeskEntitlementGrantDataset,
    DeskEntitlementGrantTier,
    DeskEntitlementListRead,
    DeskEntitlementRead,
    DeskEntitlementRevoke,
    DeskMethodologyApprove,
    DeskMethodologyCreate,
    DeskMethodologyListRead,
    DeskMethodologyRead,
    DeskMethodologyVersionPropose,
    DeskObservationCreate,
    DeskObservationListRead,
    DeskObservationRead,
    DeskObservationSnippet,
    DeskPackageView,
    DeskPublicationListRead,
    DeskPublicationRead,
    DeskResearchAdjustmentsPut,
)
from app.services import live_refresh_triggers
from app.services.market_desk import (
    calculation,
    determinations,
    entitlements,
    observations,
    package,
    publication,
    register,
    snippets,
)
from app.services.public_ids import normalize_public_id

router = APIRouter(prefix="/desk", tags=["operator-desk"])


# -- read-model builders ------------------------------------------------------------


def _methodology_read(row: DeskMethodology) -> DeskMethodologyRead:
    return DeskMethodologyRead(
        id=row.id,
        methodology_code=row.methodology_code,
        version=row.version,
        status=row.status,
        parameters=row.parameters,
        change_rationale=row.change_rationale,
        proposed_by=row.proposed_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        effective_from=row.effective_from,
        created_at=row.created_at,
    )


def _observation_read(row: DeskObservation) -> DeskObservationRead:
    return DeskObservationRead(
        id=row.id,
        capture_id=row.capture_id,
        series_code=row.series_code,
        as_of_date=row.as_of_date,
        value=row.value,
        unit=row.unit,
        attributes=row.attributes,
        quality_flags=row.quality_flags,
        entered_by=row.entered_by,
        superseded_by=row.superseded_by,
        created_at=row.created_at,
    )


def _capture_read(row: DeskSourceCapture) -> DeskCaptureRead:
    return DeskCaptureRead(
        id=row.id,
        source_key=row.source_key,
        captured_at=row.captured_at,
        as_of_date=row.as_of_date,
        source_url=row.source_url,
        content_sha256=row.content_sha256,
        storage_path=row.storage_path,
        parser_version=row.parser_version,
        status=row.status,
        parse_error=row.parse_error,
        created_by=row.created_by,
    )


def _determination_read(row: DeskDetermination) -> DeskDeterminationRead:
    return DeskDeterminationRead(
        id=row.id,
        cob_date=row.cob_date,
        methodology_code=row.methodology_code,
        methodology_version=row.methodology_version,
        input_snapshot=row.input_snapshot,
        input_digest=row.input_digest,
        derived_values=row.derived_values,
        qa_results=row.qa_results,
        research_adjustments=list(row.research_adjustments or []),
        status=row.status,
        prepared_by=row.prepared_by,
        reviewed_by=row.reviewed_by,
        review_note=row.review_note,
        published_at=row.published_at,
        supersedes_id=row.supersedes_id,
        created_at=row.created_at,
    )


def _publication_read(row: DeskPublication) -> DeskPublicationRead:
    return DeskPublicationRead(
        id=row.id,
        determination_id=row.determination_id,
        published_by=row.published_by,
        published_at=row.published_at,
        status=row.status,
        results=row.results,
    )


# -- methodology register (Track 2) --------------------------------------------------


@router.get("/methodologies", response_model=DeskMethodologyListRead)
def list_methodologies(
    db: OperatorDb,
    _operator: Operator,
    methodology_code: str | None = None,
) -> DeskMethodologyListRead:
    rows = register.list_methodologies(db, methodology_code=methodology_code)
    return DeskMethodologyListRead(
        methodologies=[_methodology_read(row) for row in rows], total=len(rows)
    )


@router.post("/methodologies", response_model=DeskMethodologyRead, status_code=201)
def create_methodology(
    payload: DeskMethodologyCreate, db: OperatorDb, operator: Operator
) -> DeskMethodologyRead:
    row = register.create_methodology(
        db,
        methodology_code=payload.methodology_code,
        parameters=payload.parameters,
        change_rationale=payload.change_rationale,
        proposed_by=operator.email,
    )
    record_operator_action(
        db,
        operator,
        action="desk.methodology.create",
        detail={"methodology_code": row.methodology_code, "version": row.version},
    )
    db.commit()
    return _methodology_read(row)


@router.post(
    "/methodologies/ensure-default", response_model=DeskMethodologyRead, status_code=201
)
def ensure_default_methodology(db: OperatorDb, operator: Operator) -> DeskMethodologyRead:
    """Idempotent bootstrap of the AEQ-GHS-CURVES v1 draft (service-seeded,
    never a data migration — approval still happens through Track 2)."""
    row = register.ensure_default_methodology(db)
    record_operator_action(
        db,
        operator,
        action="desk.methodology.ensure_default",
        detail={"methodology_code": row.methodology_code, "version": row.version},
    )
    db.commit()
    return _methodology_read(row)


@router.post(
    "/methodologies/{methodology_code}/versions",
    response_model=DeskMethodologyRead,
    status_code=201,
)
def propose_methodology_version(
    methodology_code: str,
    payload: DeskMethodologyVersionPropose,
    db: OperatorDb,
    operator: Operator,
) -> DeskMethodologyRead:
    row = register.propose_version(
        db,
        methodology_code=methodology_code,
        parameters=payload.parameters,
        change_rationale=payload.change_rationale,
        proposed_by=operator.email,
    )
    record_operator_action(
        db,
        operator,
        action="desk.methodology.propose_version",
        detail={"methodology_code": methodology_code, "version": row.version},
    )
    db.commit()
    return _methodology_read(row)


@router.post(
    "/methodologies/{methodology_code}/versions/{version}/approve",
    response_model=DeskMethodologyRead,
)
def approve_methodology_version(
    methodology_code: str,
    version: int,
    payload: DeskMethodologyApprove,
    db: OperatorDb,
    operator: Operator,
) -> DeskMethodologyRead:
    row = register.approve_version(
        db,
        methodology_code=methodology_code,
        version=version,
        approved_by=operator.email,
        effective_from=payload.effective_from,
    )
    refresh_jobs = live_refresh_triggers.enqueue_methodology_change(
        db,
        methodology_code=methodology_code,
        version=version,
    )
    record_operator_action(
        db,
        operator,
        action="desk.methodology.approve_version",
        detail={
            "methodology_code": methodology_code,
            "version": version,
            "effective_from": payload.effective_from.isoformat(),
            "live_refreshes_enqueued": len(refresh_jobs),
        },
    )
    db.commit()
    return _methodology_read(row)


@router.get("/methodologies/{methodology_code}/versions/{version}/pdf")
def get_methodology_pdf(
    methodology_code: str,
    version: int,
    db: OperatorDb,
    _operator: Operator,
    download: bool = Query(default=False),
) -> Response:
    """Render one governed methodology version for in-console review or download."""
    row = register.get_version(db, methodology_code, version)
    filename = f"{row.methodology_code}-v{row.version}-methodology.pdf"
    disposition = "attachment" if download else "inline"
    return Response(
        content=render_methodology_pdf(row),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# -- observations / captures ----------------------------------------------------------


@router.get("/observations", response_model=DeskObservationListRead)
def list_observations(  # noqa: PLR0913 - FastAPI query parameters, one per filter
    db: OperatorDb,
    _operator: Operator,
    series_code: str | None = None,
    as_of_from: date | None = None,
    as_of_to: date | None = None,
    include_superseded: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DeskObservationListRead:
    """Paginated observation ledger. ``total`` is the real filtered count (the
    table runs to hundreds of thousands of rows), never the page length."""
    rows = observations.list_observations(
        db,
        series_code=series_code,
        as_of_from=as_of_from,
        as_of_to=as_of_to,
        include_superseded=include_superseded,
        limit=limit,
        offset=offset,
    )
    total = observations.count_observations(
        db,
        series_code=series_code,
        as_of_from=as_of_from,
        as_of_to=as_of_to,
        include_superseded=include_superseded,
    )
    return DeskObservationListRead(
        observations=[_observation_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/observations", response_model=DeskObservationRead, status_code=201)
def create_observation(
    payload: DeskObservationCreate, db: OperatorDb, operator: Operator
) -> DeskObservationRead:
    row = observations.record_manual_observation(
        db,
        series_code=payload.series_code,
        as_of_date=payload.as_of_date,
        value=payload.value,
        unit=payload.unit,
        entered_by=operator.email,
        attributes=payload.attributes,
        quality_flags=list(payload.quality_flags),
    )
    record_operator_action(
        db,
        operator,
        action="desk.observation.manual_entry",
        detail={
            "series_code": row.series_code,
            "as_of_date": row.as_of_date.isoformat(),
            "value": str(row.value),
            "unit": row.unit,
        },
    )
    db.commit()
    return _observation_read(row)


@router.get("/captures", response_model=DeskCaptureListRead)
def list_captures(
    db: OperatorDb,
    _operator: Operator,
    source_key: str | None = None,
) -> DeskCaptureListRead:
    rows = observations.list_captures(db, source_key=source_key)
    return DeskCaptureListRead(captures=[_capture_read(row) for row in rows], total=len(rows))


@router.get("/captures/{capture_id}/content", response_model=DeskCaptureContentView)
def get_capture_content(
    capture_id: UUID,
    db: OperatorDb,
    _operator: Operator,
    needle: str | None = None,
) -> DeskCaptureContentView:
    """Decode silver capture payload for field-level source review."""
    view = snippets.capture_content_view(db, capture_id, needle=needle)
    return DeskCaptureContentView.model_validate(view)


@router.get(
    "/captures/{capture_id}/snippet",
    response_model=DeskObservationSnippet,
)
def get_capture_snippet(
    capture_id: UUID,
    db: OperatorDb,
    _operator: Operator,
    value: str | None = None,
) -> DeskObservationSnippet:
    """Snippet window around an extracted observation value."""
    view = snippets.snippet_for_observation(db, capture_id=capture_id, value=value)
    return DeskObservationSnippet.model_validate(view)


# -- entitlements (spec §10) ------------------------------------------------------------
#
# An entitlement is TENANT state — which desk datasets one bank receives — so
# every route below passes the Tenant Inspector gate for the named organization
# and writes an ``operator_audit_log`` row carrying ``target_org``, exactly like
# ``GET /operator/v1/tenants/{org}/entitlements`` and every ``inspector.fix.*``
# write. Until 2026-08-23 they did neither (audit finding D-26): the org came
# from the client, the gate was the base ``Operator`` dependency (which admits
# the lowest ``developer`` tier), the read left NO audit row at all, and
# omitting ``organization_id`` dumped every tenant's grants in one response.
#
# Two facts make an ungated mutation here worse than it looks. Migration
# ``202608230036`` put ``market_data_entitlements`` under FORCE row-level
# security — it is now a tenant-isolated table, so writing one from an
# unaudited path crosses an isolation boundary. And the read path
# (``market_desk.entitlements.active_datasets``) GRANDFATHERS an org with no
# visible rows to the standard tier, so a leak in either direction changes what
# a tenant can see: revoking the wrong org's rows silently upgrades it to
# standard rather than cutting it off.


def _entitlement_read(row: Any) -> DeskEntitlementRead:
    return DeskEntitlementRead(
        id=row.id,
        organization_id=row.organization_id,
        dataset_code=row.dataset_code,
        tier=row.tier,
        status=row.status,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        granted_by=row.granted_by,
        revoked_by=row.revoked_by,
        revoked_at=row.revoked_at,
        notes=row.notes,
        created_at=row.created_at,
    )


@router.get("/entitlements", response_model=DeskEntitlementListRead)
def list_entitlements(
    db: OperatorDb,
    operator: Operator,
    organization_id: str,
    include_revoked: bool = False,
) -> DeskEntitlementListRead:
    """One tenant's desk dataset grants plus the catalog. ``organization_id``
    is REQUIRED — the unfiltered form dumped every tenant's commercial terms in
    a single unaudited response. Requires an active Tenant Inspector session
    (403 otherwise); the read is audited."""
    org_id = normalize_public_id(organization_id)
    session = require_active_inspection(db, operator, org_id)
    rows = entitlements.list_entitlements(
        db, organization_id=org_id, include_revoked=include_revoked
    )
    record_operator_action(
        db,
        operator,
        action="inspector.read.entitlements",
        target_org=org_id,
        detail={"session_id": str(session.id), "include_revoked": include_revoked},
    )
    db.commit()
    return DeskEntitlementListRead(
        entitlements=[_entitlement_read(r) for r in rows],
        total=len(rows),
        catalog=entitlements.catalog(),
    )


@router.post("/entitlements/grant-tier", response_model=DeskEntitlementListRead, status_code=201)
def grant_entitlement_tier(
    payload: DeskEntitlementGrantTier, db: OperatorDb, operator: Operator
) -> DeskEntitlementListRead:
    """Grant a whole tier (core / standard / premium) to one tenant.
    Session-gated + audited as ``inspector.entitlement.grant_tier``."""
    org_id = normalize_public_id(payload.organization_id)
    session = require_active_inspection(db, operator, org_id)
    rows, changed = entitlements.grant_tier(
        db,
        organization_id=org_id,
        tier=payload.tier,
        effective_from=payload.effective_from,
        granted_by=operator.email,
        notes=payload.notes,
    )
    refresh_jobs = (
        live_refresh_triggers.enqueue_entitlement_change(
            db,
            organization_id=org_id,
            reason=f"market-data entitlement tier granted:{payload.tier}",
        )
        if changed
        else []
    )
    record_operator_action(
        db,
        operator,
        action="inspector.entitlement.grant_tier",
        target_org=org_id,
        detail={
            "session_id": str(session.id),
            "tier": payload.tier,
            "effective_from": payload.effective_from.isoformat(),
            "datasets": [r.dataset_code for r in rows],
            "live_refreshes_enqueued": len(refresh_jobs),
        },
    )
    db.commit()
    all_rows = entitlements.list_entitlements(db, organization_id=org_id)
    return DeskEntitlementListRead(
        entitlements=[_entitlement_read(r) for r in all_rows],
        total=len(all_rows),
        catalog=entitlements.catalog(),
    )


@router.post("/entitlements/grant-dataset", response_model=DeskEntitlementRead, status_code=201)
def grant_entitlement_dataset(
    payload: DeskEntitlementGrantDataset, db: OperatorDb, operator: Operator
) -> DeskEntitlementRead:
    """Grant a single dataset override to one tenant.
    Session-gated + audited as ``inspector.entitlement.grant_dataset``."""
    org_id = normalize_public_id(payload.organization_id)
    session = require_active_inspection(db, operator, org_id)
    row, changed = entitlements.grant_dataset(
        db,
        organization_id=org_id,
        dataset_code=payload.dataset_code,
        effective_from=payload.effective_from,
        granted_by=operator.email,
        notes=payload.notes,
    )
    refresh_jobs = (
        live_refresh_triggers.enqueue_entitlement_change(
            db,
            organization_id=org_id,
            reason=f"market-data entitlement granted:{payload.dataset_code}",
        )
        if changed
        else []
    )
    record_operator_action(
        db,
        operator,
        action="inspector.entitlement.grant_dataset",
        target_org=org_id,
        detail={
            "session_id": str(session.id),
            "dataset_code": payload.dataset_code,
            "effective_from": payload.effective_from.isoformat(),
            "live_refreshes_enqueued": len(refresh_jobs),
        },
    )
    db.commit()
    return _entitlement_read(row)


@router.post("/entitlements/{entitlement_id}/revoke", response_model=DeskEntitlementRead)
def revoke_entitlement(
    entitlement_id: UUID,
    payload: DeskEntitlementRevoke,
    db: OperatorDb,
    operator: Operator,
) -> DeskEntitlementRead:
    """End-date one tenant's grant. The body names the organization so the call
    can be gated and audited against it; a grant belonging to another tenant is
    a 404, never a silent cross-tenant revoke. Session-gated + audited as
    ``inspector.entitlement.revoke``."""
    org_id = normalize_public_id(payload.organization_id)
    session = require_active_inspection(db, operator, org_id)
    row, changed = entitlements.revoke(
        db, entitlement_id, organization_id=org_id, revoked_by=operator.email
    )
    refresh_jobs = (
        live_refresh_triggers.enqueue_entitlement_change(
            db,
            organization_id=org_id,
            reason=f"market-data entitlement revoked:{row.dataset_code}",
        )
        if changed
        else []
    )
    record_operator_action(
        db,
        operator,
        action="inspector.entitlement.revoke",
        target_org=org_id,
        detail={
            "session_id": str(session.id),
            "entitlement_id": str(row.id),
            "dataset_code": row.dataset_code,
            "live_refreshes_enqueued": len(refresh_jobs),
        },
    )
    db.commit()
    return _entitlement_read(row)


# -- determinations (Track 1) -----------------------------------------------------------


@router.get("/determinations", response_model=DeskDeterminationListRead)
def list_determinations(
    db: OperatorDb,
    _operator: Operator,
    cob_date: date | None = None,
    status: str | None = None,
) -> DeskDeterminationListRead:
    rows = determinations.list_determinations(db, cob_date=cob_date, status_filter=status)
    return DeskDeterminationListRead(
        determinations=[_determination_read(row) for row in rows], total=len(rows)
    )


@router.get("/determinations/{determination_id}", response_model=DeskDeterminationRead)
def get_determination(
    determination_id: UUID, db: OperatorDb, _operator: Operator
) -> DeskDeterminationRead:
    return _determination_read(determinations.get(db, determination_id))


@router.get(
    "/determinations/{determination_id}/package",
    response_model=DeskPackageView,
)
def get_determination_package(
    determination_id: UUID, db: OperatorDb, _operator: Operator
) -> DeskPackageView:
    """Research Desk wizard payload: completeness, WoW deltas, provenance."""
    row = determinations.get(db, determination_id)
    view = package.build_package_view(db, row)
    return DeskPackageView.model_validate(view)


@router.post("/determinations", response_model=DeskDeterminationRead, status_code=201)
def create_determination(
    payload: DeskDeterminationCreate, db: OperatorDb, operator: Operator
) -> DeskDeterminationRead:
    row = determinations.create_draft(
        db,
        cob_date=payload.cob_date,
        prepared_by=operator.email,
        methodology_code=payload.methodology_code or register.DEFAULT_METHODOLOGY_CODE,
    )
    record_operator_action(
        db,
        operator,
        action="desk.determination.create_draft",
        detail={
            "determination_id": str(row.id),
            "cob_date": row.cob_date.isoformat(),
            "methodology_code": row.methodology_code,
            "methodology_version": row.methodology_version,
            "input_digest": row.input_digest,
        },
    )
    db.commit()
    return _determination_read(row)


@router.post("/determinations/{determination_id}/compute", response_model=DeskDeterminationRead)
def compute_determination(
    determination_id: UUID, db: OperatorDb, operator: Operator
) -> DeskDeterminationRead:
    """Run the §5 calculation pipeline on a DRAFT: finalize the input
    snapshot, derive curves and rates, record QA results. Deterministic —
    recomputing an unchanged draft is a no-op by value."""
    row = determinations.get(db, determination_id)
    methodology = register.get_version(db, row.methodology_code, row.methodology_version)
    try:
        calculation.compute_determination(db, row, methodology=methodology)
    except calculation.CalculationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    record_operator_action(
        db,
        operator,
        action="desk.determination.compute",
        detail={
            "determination_id": str(row.id),
            "input_digest": row.input_digest,
            "qa_passed": bool(row.derived_values.get("qa_passed")),
        },
    )
    db.commit()
    return _determination_read(row)


@router.put(
    "/determinations/{determination_id}/adjustments",
    response_model=DeskDeterminationRead,
)
def put_determination_adjustments(
    determination_id: UUID,
    payload: DeskResearchAdjustmentsPut,
    db: OperatorDb,
    operator: Operator,
) -> DeskDeterminationRead:
    """Replace research adjustments on a draft and recompute so package digest
    and derived rates reflect judgment (Option B Track-1)."""
    before = list(determinations.get(db, determination_id).research_adjustments or [])
    row = determinations.set_research_adjustments(
        db,
        determination_id,
        adjustments=[item.model_dump() for item in payload.adjustments],
        applied_by=operator.email,
    )
    methodology = register.get_version(db, row.methodology_code, row.methodology_version)
    try:
        calculation.compute_determination(db, row, methodology=methodology)
    except calculation.CalculationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    record_operator_action(
        db,
        operator,
        action="desk.determination.adjustments",
        detail={
            "determination_id": str(row.id),
            "before": before,
            "after": list(row.research_adjustments or []),
            "package_digest": (row.derived_values or {}).get("package_digest"),
        },
    )
    db.commit()
    return _determination_read(row)


@router.post("/determinations/{determination_id}/submit", response_model=DeskDeterminationRead)
def submit_determination(
    determination_id: UUID, db: OperatorDb, operator: Operator
) -> DeskDeterminationRead:
    # Submit requires a computed rates package (rates_qa_passed). Empty
    # drafts cannot reach the Supervisor.
    calculation.ensure_approvable(determinations.get(db, determination_id))
    row = determinations.submit_for_review(db, determination_id)
    record_operator_action(
        db,
        operator,
        action="desk.determination.submit",
        detail={
            "determination_id": str(row.id),
            "package_digest": (row.derived_values or {}).get("package_digest"),
        },
    )
    db.commit()
    return _determination_read(row)


@router.post("/determinations/{determination_id}/approve", response_model=DeskDeterminationRead)
def approve_determination(
    determination_id: UUID, db: OperatorDb, operator: Operator
) -> DeskDeterminationRead:
    # Hard QA gates (spec §5 step 6) are enforced at this API layer: the
    # determinations state machine is deliberately QA-agnostic.
    calculation.ensure_approvable(determinations.get(db, determination_id))
    row = determinations.approve(db, determination_id, reviewed_by=operator.email)
    record_operator_action(
        db,
        operator,
        action="desk.determination.approve",
        detail={"determination_id": str(row.id)},
    )
    db.commit()
    return _determination_read(row)


@router.post("/determinations/{determination_id}/reject", response_model=DeskDeterminationRead)
def reject_determination(
    determination_id: UUID,
    payload: DeskDeterminationReject,
    db: OperatorDb,
    operator: Operator,
) -> DeskDeterminationRead:
    row = determinations.reject(
        db, determination_id, reviewed_by=operator.email, reason=payload.reason
    )
    record_operator_action(
        db,
        operator,
        action="desk.determination.reject",
        detail={"determination_id": str(row.id), "reason": payload.reason},
    )
    db.commit()
    return _determination_read(row)


@router.post(
    "/determinations/{determination_id}/supersede",
    response_model=DeskDeterminationRead,
    status_code=201,
)
def supersede_determination(
    determination_id: UUID, db: OperatorDb, operator: Operator
) -> DeskDeterminationRead:
    """Correction path: a NEW draft for the same COB date carrying
    ``supersedes_id`` — the published original is never edited."""
    row = determinations.supersede(db, determination_id, prepared_by=operator.email)
    record_operator_action(
        db,
        operator,
        action="desk.determination.supersede",
        detail={"determination_id": str(row.id), "supersedes_id": str(determination_id)},
    )
    db.commit()
    return _determination_read(row)


@router.post("/determinations/{determination_id}/publish", response_model=DeskPublicationRead)
def publish_determination(
    determination_id: UUID, db: OperatorDb, operator: Operator
) -> DeskPublicationRead:
    """Fan the approved determination out to every tenant through the
    adapter seam. Publish is a deliberate, logged action (spec §11a)."""
    row = publication.publish(db, determination_id, actor=operator.email)
    record_operator_action(
        db,
        operator,
        action="desk.determination.publish",
        detail={
            "determination_id": str(determination_id),
            "publication_id": str(row.id),
            "status": row.status,
            "banks": len(row.results),
        },
    )
    db.commit()
    return _publication_read(row)


@router.get("/publications", response_model=DeskPublicationListRead)
def list_publications(
    db: OperatorDb,
    _operator: Operator,
    determination_id: UUID | None = None,
) -> DeskPublicationListRead:
    rows = publication.list_publications(db, determination_id=determination_id)
    return DeskPublicationListRead(
        publications=[_publication_read(row) for row in rows], total=len(rows)
    )
