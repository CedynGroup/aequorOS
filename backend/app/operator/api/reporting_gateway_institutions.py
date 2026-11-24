"""Every institution on the AequorOS Reporting Gateway (staff console, channel operator only).

AequorOS client banks are enrolled from their tenant page, where AequorOS holds their
keys. These routes manage the channel itself: list every institution, and enrol and
equip an institution that files with its own software. That institution generates
its own keys; staff relay its certificate request and public keys, and hand back the
certificate the gateway issues. None of this touches a tenant, so no inspection
session applies; every call, successful or not, is an operator audit entry.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_operator_settings
from app.operator.api.reporting_gateway import GatewayTransport
from app.operator.deps import OperatorAdmin, OperatorContext, OperatorDb, record_operator_action
from app.reporting.public import (
    GatewayClientIssue,
    GatewayClientIssued,
    GatewayClientRevoke,
    GatewayIdentity,
    GatewayInstitution,
    GatewayInstitutionCreate,
    GatewayInstitutionList,
    GatewayInstitutionStatusChange,
    GatewaySigningKeyRegister,
    gateway_enrolment,
    gateway_institutions,
)

router = APIRouter(prefix="/reporting-gateway/institutions", tags=["operator-reporting-gateway"])

type _Detail = dict[str, str | bool | None]


def _identity() -> GatewayIdentity:
    settings = get_operator_settings()
    return gateway_enrolment.operator_identity(
        base_url=settings.reporting_gateway_url,
        directory=settings.reporting_gateway_operator_dir,
        forward_certificate=settings.reporting_gateway_forward_client_certificate,
    )


def _refused(
    db: Session, operator: OperatorContext, action: str, detail: _Detail, exc: HTTPException
) -> None:
    """Record a refused act in its own transaction; the caller re-raises."""
    db.rollback()
    record_operator_action(
        db,
        operator,
        action=f"{action}_failed",
        detail={**detail, "succeeded": False, "status_code": exc.status_code},
    )
    db.commit()


def _audited_read[T](  # noqa: PLR0913 - the read, its audit name and its evidence
    db: Session,
    operator: OperatorContext,
    action: str,
    detail: _Detail,
    read: Callable[[], T],
    evidence: Callable[[T], _Detail] | None = None,
) -> T:
    """Run one gateway read and record it, refused or not, before answering."""
    try:
        result = read()
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(
        db,
        operator,
        action=action,
        detail={**detail, **(evidence(result) if evidence else {}), "succeeded": True},
    )
    db.commit()
    return result


@router.get("", response_model=GatewayInstitutionList)
def list_reporting_gateway_institutions(
    db: OperatorDb, operator: OperatorAdmin, transport: GatewayTransport
) -> GatewayInstitutionList:
    """Every institution on the channel, by code, with its AequorOS bank where it has one."""
    return _audited_read(
        db,
        operator,
        "reporting_gateway.institutions_read",
        {},
        lambda: gateway_institutions.list_institutions(db, _identity(), transport),
        lambda result: {"count": str(len(result.items))},
    )


@router.get("/{code}", response_model=GatewayInstitution)
def get_reporting_gateway_institution(
    code: str, db: OperatorDb, operator: OperatorAdmin, transport: GatewayTransport
) -> GatewayInstitution:
    return _audited_read(
        db,
        operator,
        "reporting_gateway.institution_read",
        {"institution_code": code},
        lambda: gateway_institutions.get_institution(db, _identity(), code, transport),
    )


@router.post("", response_model=GatewayInstitution)
def enrol_reporting_gateway_institution(
    payload: GatewayInstitutionCreate,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayInstitution:
    """Enrol an institution that files with its own software (idempotent on the code)."""
    action = "reporting_gateway.institution_enrolled"
    detail: _Detail = {"institution_code": payload.code, "name": payload.name, "type": payload.type}
    try:
        result = gateway_institutions.enrol_institution(db, _identity(), payload, transport)
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(db, operator, action=action, detail={**detail, "succeeded": True})
    db.commit()
    return result


@router.post("/{code}/clients", response_model=GatewayClientIssued)
def issue_reporting_gateway_client(  # noqa: PLR0913 - path, body, session, actor, transport
    code: str,
    payload: GatewayClientIssue,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayClientIssued:
    """Issue an API client from the institution's own certificate request.

    Returns the client id and certificate to hand to the institution (both public).
    """
    action = "reporting_gateway.client_issued"
    detail: _Detail = {"institution_code": code, "environment": payload.environment}
    try:
        result = gateway_institutions.issue_client(db, _identity(), code, payload, transport)
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(
        db,
        operator,
        action=action,
        detail={**detail, "client_id": result.client_id, "succeeded": True},
    )
    db.commit()
    return result


@router.post("/{code}/signing-keys", response_model=GatewayInstitution)
def register_reporting_gateway_signing_key(  # noqa: PLR0913 - path, body, session, actor, transport
    code: str,
    payload: GatewaySigningKeyRegister,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayInstitution:
    """Register the institution's filing signature key (its public key only)."""
    action = "reporting_gateway.signing_key_registered"
    detail: _Detail = {"institution_code": code, "kid": payload.kid}
    try:
        result = gateway_institutions.register_signing_key(
            db, _identity(), code, payload, transport
        )
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(db, operator, action=action, detail={**detail, "succeeded": True})
    db.commit()
    return result


@router.post("/{code}/signing-keys/{kid}/retire", response_model=GatewayInstitution)
def retire_reporting_gateway_signing_key(  # noqa: PLR0913 - path, session, actor, transport
    code: str,
    kid: str,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayInstitution:
    """Retire a signature key; filings already signed with it stay verifiable."""
    action = "reporting_gateway.signing_key_retired"
    detail: _Detail = {"institution_code": code, "kid": kid}
    try:
        result = gateway_institutions.retire_signing_key(db, _identity(), code, kid, transport)
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(db, operator, action=action, detail={**detail, "succeeded": True})
    db.commit()
    return result


@router.post("/{code}/status", response_model=GatewayInstitution)
def change_reporting_gateway_institution_status(  # noqa: PLR0913 - path, body, session, actor, transport
    code: str,
    payload: GatewayInstitutionStatusChange,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayInstitution:
    """Mark an institution live, or suspend it (its clients then get no tokens)."""
    action = "reporting_gateway.institution_status_changed"
    detail: _Detail = {"institution_code": code, "status": payload.status, "reason": payload.reason}
    try:
        result = gateway_institutions.change_status(db, _identity(), code, payload, transport)
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(db, operator, action=action, detail={**detail, "succeeded": True})
    db.commit()
    return result


@router.post("/{code}/clients/{client_id}/revoke", response_model=GatewayInstitution)
def revoke_reporting_gateway_client(  # noqa: PLR0913 - path, body, session, actor, transport
    code: str,
    client_id: str,
    payload: GatewayClientRevoke,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayInstitution:
    """Revoke one of the institution's API clients."""
    action = "reporting_gateway.client_revoked"
    detail: _Detail = {"institution_code": code, "client_id": client_id, "reason": payload.reason}
    try:
        result = gateway_institutions.revoke_client(
            db, _identity(), code, client_id, payload, transport
        )
    except HTTPException as exc:
        _refused(db, operator, action, detail, exc)
        raise
    record_operator_action(db, operator, action=action, detail={**detail, "succeeded": True})
    db.commit()
    return result
