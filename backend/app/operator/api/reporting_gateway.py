"""Enrol a bank on the AequorOS Reporting Gateway (staff console, channel operator only).

The gateway is AequorOS' own reporting channel; enrolment is setup the channel
operator performs, so banks never handle certificates or keys. Like connecting a
bank's encryption key, it writes into one tenant's configuration, so it needs an
active inspection session for that organization. Every read and every
enrolment, successful or not, is an operator audit entry; a successful
enrolment is also recorded in the bank's own audit trail.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_operator_settings
from app.operator.deps import OperatorAdmin, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.reporting.public import (
    GatewayCertificateRenewalRequest,
    GatewayEnrolmentRead,
    GatewayEnrolmentRequest,
    gateway_enrolment,
    gateway_renewal,
)
from app.services.public_ids import normalize_public_id


def get_reporting_gateway_transport() -> httpx.BaseTransport | None:
    """The network transport for gateway calls. Tests override this dependency
    with a fake gateway; the route itself never chooses a transport."""
    return None


GatewayTransport = Annotated[httpx.BaseTransport | None, Depends(get_reporting_gateway_transport)]

router = APIRouter(
    prefix="/tenants/{org_id}/banks/{bank_id}/reporting-gateway",
    tags=["operator-reporting-gateway"],
)


@router.get("", response_model=GatewayEnrolmentRead)
def get_reporting_gateway_enrolment(
    org_id: str, bank_id: str, db: OperatorDb, operator: OperatorAdmin
) -> GatewayEnrolmentRead:
    """The bank's enrolment on the gateway. Never returns key material."""
    org_id, bank_id = normalize_public_id(org_id), normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    result = gateway_enrolment.enrolment_status(
        db,
        org_id,
        bank_id,
        renew_before_days=get_operator_settings().reporting_gateway_renew_before_days,
    )
    record_operator_action(
        db,
        operator,
        action="reporting_gateway.read",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "enrolled": result.enrolled,
            "session_id": str(inspection.id),
        },
    )
    db.commit()
    return result


@router.post("/enrolment", response_model=GatewayEnrolmentRead)
def enrol_bank_on_reporting_gateway(  # noqa: PLR0913 - path, body, session, actor, transport
    org_id: str,
    bank_id: str,
    payload: GatewayEnrolmentRequest,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayEnrolmentRead:
    """Enrol the bank on the gateway, or repeat a partial enrolment (idempotent).

    Creates the bank's API client key and filing signature key in the platform
    vault, has the gateway issue the client and register the key, then tests
    the bank's connection. Nothing is filed and nothing is signed.
    """
    org_id, bank_id = normalize_public_id(org_id), normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    session_id = str(inspection.id)
    settings = get_operator_settings()
    identity = gateway_enrolment.operator_identity(
        base_url=settings.reporting_gateway_url,
        directory=settings.reporting_gateway_operator_dir,
        forward_certificate=settings.reporting_gateway_forward_client_certificate,
    )
    try:
        result = gateway_enrolment.enrol_bank(
            db,
            org_id,
            bank_id,
            payload,
            operator=identity,
            environment=settings.reporting_gateway_environment,
            transport=transport,
        )
    except HTTPException as exc:
        db.rollback()
        record_operator_action(
            db,
            operator,
            action="reporting_gateway.enrolment_failed",
            target_org=org_id,
            detail={
                "bank_id": bank_id,
                "succeeded": False,
                "status_code": exc.status_code,
                "session_id": session_id,
            },
        )
        db.commit()
        raise
    record_operator_action(
        db,
        operator,
        action="reporting_gateway.enrolled",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "institution_code": result.institution_code,
            "environment": result.environment,
            "client_id": result.client_id,
            "signing_kid": result.signing_kid,
            "connection_test_ok": result.last_connection_test_ok,
            "session_id": session_id,
        },
    )
    db.commit()
    return result


@router.post("/certificate-renewal", response_model=GatewayEnrolmentRead)
def renew_reporting_gateway_certificate(  # noqa: PLR0913 - path, body, session, actor, transport
    org_id: str,
    bank_id: str,
    payload: GatewayCertificateRenewalRequest,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayEnrolmentRead:
    """Renew the bank's gateway client certificate now, or resume an unfinished renewal.

    A new key and client are issued and tested as the bank before the bank is switched
    over; the old client is then revoked. The bank keeps its working certificate if
    any step before the switch fails. Nothing is filed and nothing is signed.
    """
    org_id, bank_id = normalize_public_id(org_id), normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    session_id = str(inspection.id)
    settings = get_operator_settings()
    identity = gateway_enrolment.operator_identity(
        base_url=settings.reporting_gateway_url,
        directory=settings.reporting_gateway_operator_dir,
        forward_certificate=settings.reporting_gateway_forward_client_certificate,
    )
    try:
        result = gateway_renewal.renew_certificate(
            db,
            org_id,
            bank_id,
            gateway_renewal.RenewalRequest(
                trigger="staff", requested_by=operator.email, reason=payload.reason
            ),
            operator=identity,
            renew_before_days=settings.reporting_gateway_renew_before_days,
            transport=transport,
        )
    except HTTPException as exc:
        db.rollback()
        record_operator_action(
            db,
            operator,
            action="reporting_gateway.certificate_renewal_failed",
            target_org=org_id,
            detail={
                "bank_id": bank_id,
                "succeeded": False,
                "status_code": exc.status_code,
                "reason": payload.reason,
                "session_id": session_id,
            },
        )
        db.commit()
        raise
    renewal = result.certificate_renewal
    record_operator_action(
        db,
        operator,
        action="reporting_gateway.certificate_renewed",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "client_id": result.client_id,
            "previous_client_id": renewal.previous_client_id if renewal else None,
            "renewal_status": renewal.status if renewal else None,
            "reason": payload.reason,
            "session_id": session_id,
        },
    )
    db.commit()
    return result


@router.post("/certificate-renewal/cancel", response_model=GatewayEnrolmentRead)
def cancel_reporting_gateway_certificate_renewal(  # noqa: PLR0913 - path, body, session, actor, transport
    org_id: str,
    bank_id: str,
    payload: GatewayCertificateRenewalRequest,
    db: OperatorDb,
    operator: OperatorAdmin,
    transport: GatewayTransport,
) -> GatewayEnrolmentRead:
    """Abandon an unfinished renewal that has not switched the bank over yet.

    A client the gateway already issued for it is revoked and its key discarded; the
    bank keeps its current certificate.
    """
    org_id, bank_id = normalize_public_id(org_id), normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    session_id = str(inspection.id)
    settings = get_operator_settings()
    identity = gateway_enrolment.operator_identity(
        base_url=settings.reporting_gateway_url,
        directory=settings.reporting_gateway_operator_dir,
        forward_certificate=settings.reporting_gateway_forward_client_certificate,
    )
    try:
        result = gateway_renewal.cancel_renewal(
            db,
            org_id,
            bank_id,
            gateway_renewal.RenewalRequest(
                trigger="staff", requested_by=operator.email, reason=payload.reason
            ),
            operator=identity,
            renew_before_days=settings.reporting_gateway_renew_before_days,
            transport=transport,
        )
    except HTTPException as exc:
        db.rollback()
        record_operator_action(
            db,
            operator,
            action="reporting_gateway.certificate_renewal_cancel_failed",
            target_org=org_id,
            detail={
                "bank_id": bank_id,
                "succeeded": False,
                "status_code": exc.status_code,
                "reason": payload.reason,
                "session_id": session_id,
            },
        )
        db.commit()
        raise
    record_operator_action(
        db,
        operator,
        action="reporting_gateway.certificate_renewal_cancelled",
        target_org=org_id,
        detail={"bank_id": bank_id, "reason": payload.reason, "session_id": session_id},
    )
    db.commit()
    return result
