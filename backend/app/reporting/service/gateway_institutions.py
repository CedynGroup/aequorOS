"""Every institution on the AequorOS Reporting Gateway, managed from the staff console.

AequorOS client banks are enrolled from their tenant page
(:mod:`app.reporting.service.gateway_enrolment`), where AequorOS holds their keys.
This module is for the channel itself: it lists every institution the gateway
carries, and enrols an institution that files with its own software. That
institution generates its own keys; staff relay its certificate request and public
keys to the gateway and hand back the issued certificate. AequorOS never holds an
outside institution's private keys.

Every call goes through the channel operator's own gateway client (``ops:enrol``),
the same one bank enrolment uses. The routes audit each call; these functions only
talk to the gateway and read which AequorOS banks hold which institution codes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity.public import Bank
from app.reporting.models.packages import RegulatoryChannelConfig
from app.reporting.schemas.reporting_gateway import (
    GatewayClientIssue,
    GatewayClientIssued,
    GatewayClientRevoke,
    GatewayInstitution,
    GatewayInstitutionCreate,
    GatewayInstitutionList,
    GatewayInstitutionStatusChange,
    GatewayLinkedBank,
    GatewaySigningKeyRegister,
)
from app.reporting.service.channels.errors import ChannelError
from app.reporting.service.channels.gateway_http import (
    GatewayErrors,
    GatewayIdentity,
    GatewaySession,
)
from app.reporting.service.channels.reporting_gateway import CHANNEL_CODE

_ERRORS = GatewayErrors(
    by_code={
        "conflict": "The gateway already holds a different record under this code or key name.",
        "not_found": "The gateway has no such institution, client or key.",
        "invalid_request": "The gateway refused the request.",
        "certificate_required": "The gateway did not receive the operator client certificate.",
        "certificate_mismatch": (
            "The operator client certificate does not match its gateway registration."
        ),
        "invalid_client_assertion": "The gateway refused the operator client's credentials.",
        "insufficient_scope": "The operator client is not allowed to manage institutions.",
        "forbidden": "The gateway refused the operator client.",
        "rate_limited": "The gateway is busy; try again shortly.",
    },
    refused="The gateway refused the request.",
    unreachable="The reporting gateway could not be reached.",
    unavailable="The reporting gateway is unavailable.",
    no_token="The reporting gateway issued no access token to the operator client.",
    show_detail=True,
)
_PAGE = 200
_MAX_PAGES = 50


class _Operator:
    """One operator session for one console action."""

    def __init__(
        self,
        identity: GatewayIdentity,
        transport: httpx.BaseTransport | None,
        clock: Callable[[], datetime] | None,
    ) -> None:
        self._session = GatewaySession(identity, _ERRORS, transport=transport, clock=clock)
        self._token: str | None = None

    def __enter__(self) -> _Operator:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._session.__exit__(None, None, None)

    def call(self, method: str, path: str, json_body: object | None = None) -> dict[str, Any]:
        if self._token is None:
            self._token = self._session.access_token("ops:enrol")
        try:
            return self._session.send(method, path, token=self._token, json_body=json_body)
        except ChannelError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
            ) from exc


def _operator(
    identity: GatewayIdentity,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime] | None = None,
) -> _Operator:
    return _Operator(identity, transport, clock)


def _code_path(code: str) -> str:
    return f"/ops/api/institutions/{quote(code, safe='')}"


def _linked_banks(db: Session) -> dict[str, GatewayLinkedBank]:
    """Institution code → the AequorOS bank enrolled under it (cross-tenant, staff only)."""
    rows = db.execute(
        select(RegulatoryChannelConfig, Bank)
        .join(Bank, Bank.id == RegulatoryChannelConfig.bank_id)
        .where(RegulatoryChannelConfig.channel == CHANNEL_CODE)
    ).tuples()
    linked: dict[str, GatewayLinkedBank] = {}
    for row, bank in rows:
        config = cast(dict[str, object], dict(row.config))
        code = config.get("institution_code")
        if isinstance(code, str) and config.get("enrolled_at"):
            linked[code] = GatewayLinkedBank(
                organization_id=bank.organization_id, bank_id=bank.id, name=bank.name
            )
    return linked


def _institution(raw: dict[str, Any], linked: dict[str, GatewayLinkedBank]) -> GatewayInstitution:
    try:
        institution = GatewayInstitution.model_validate({**raw, "aequoros_bank": None})
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The gateway returned an institution record AequorOS cannot read.",
        ) from exc
    institution.aequoros_bank = linked.get(institution.code)
    return institution


def list_institutions(
    db: Session, identity: GatewayIdentity, transport: httpx.BaseTransport | None = None
) -> GatewayInstitutionList:
    """Every institution on the channel, by code, with its AequorOS bank where it has one."""
    linked = _linked_banks(db)
    items: list[GatewayInstitution] = []
    cursor: str | None = None
    with _operator(identity, transport) as operator:
        for _ in range(_MAX_PAGES):
            query = f"?limit={_PAGE}" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
            page = operator.call("GET", f"/ops/api/institutions{query}")
            raw_items = page.get("items")
            for raw in cast(list[dict[str, Any]], raw_items if isinstance(raw_items, list) else []):
                items.append(_institution(raw, linked))
            next_cursor = page.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
    return GatewayInstitutionList(items=items)


def get_institution(
    db: Session,
    identity: GatewayIdentity,
    code: str,
    transport: httpx.BaseTransport | None = None,
) -> GatewayInstitution:
    with _operator(identity, transport) as operator:
        raw = operator.call("GET", _code_path(code))
    return _institution(raw, _linked_banks(db))


def enrol_institution(
    db: Session,
    identity: GatewayIdentity,
    request: GatewayInstitutionCreate,
    transport: httpx.BaseTransport | None = None,
) -> GatewayInstitution:
    """Enrol an institution filing with its own software (idempotent on the code).

    An AequorOS client bank is enrolled from its tenant page instead, so that
    AequorOS creates and holds its keys; its code is refused here.
    """
    linked = _linked_banks(db)
    if request.code in linked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Institution code {request.code} belongs to the AequorOS client bank "
                f"{linked[request.code].name}. Enrol it from its tenant page."
            ),
        )
    with _operator(identity, transport) as operator:
        raw = operator.call(
            "POST",
            "/ops/api/institutions",
            {
                "code": request.code,
                "name": request.name,
                "type": request.type,
                "contacts": [contact.model_dump() for contact in request.contacts],
            },
        )
    return _institution(raw, linked)


def _refuse_aequoros_bank(db: Session, code: str) -> None:
    linked = _linked_banks(db).get(code)
    if linked is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{linked.name} is an AequorOS client bank: AequorOS holds its keys. Manage "
                "its client and signature key from its tenant page."
            ),
        )


def issue_client(
    db: Session,
    identity: GatewayIdentity,
    code: str,
    request: GatewayClientIssue,
    transport: httpx.BaseTransport | None = None,
) -> GatewayClientIssued:
    """Have the gateway issue an API client from the institution's own request."""
    _refuse_aequoros_bank(db, code)
    with _operator(identity, transport) as operator:
        raw = operator.call(
            "POST",
            f"{_code_path(code)}/clients",
            {
                "environment": request.environment,
                "csr_pem": request.csr_pem,
                "jwks": {"keys": [request.public_jwk]},
                "software": request.software.model_dump() if request.software else None,
            },
        )
    try:
        return GatewayClientIssued.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The gateway did not return the issued client.",
        ) from exc


def register_signing_key(
    db: Session,
    identity: GatewayIdentity,
    code: str,
    request: GatewaySigningKeyRegister,
    transport: httpx.BaseTransport | None = None,
) -> GatewayInstitution:
    _refuse_aequoros_bank(db, code)
    with _operator(identity, transport) as operator:
        operator.call(
            "POST",
            f"{_code_path(code)}/signing-keys",
            {"kid": request.kid, "public_jwk": request.public_jwk},
        )
        raw = operator.call("GET", _code_path(code))
    return _institution(raw, _linked_banks(db))


def retire_signing_key(
    db: Session,
    identity: GatewayIdentity,
    code: str,
    kid: str,
    transport: httpx.BaseTransport | None = None,
) -> GatewayInstitution:
    _refuse_aequoros_bank(db, code)
    with _operator(identity, transport) as operator:
        operator.call("POST", f"{_code_path(code)}/signing-keys/{quote(kid, safe='')}/retire")
        raw = operator.call("GET", _code_path(code))
    return _institution(raw, _linked_banks(db))


def change_status(
    db: Session,
    identity: GatewayIdentity,
    code: str,
    request: GatewayInstitutionStatusChange,
    transport: httpx.BaseTransport | None = None,
) -> GatewayInstitution:
    """Mark an institution live, or suspend it (its clients then get no tokens)."""
    with _operator(identity, transport) as operator:
        operator.call(
            "POST",
            f"{_code_path(code)}/status",
            {"status": request.status, "reason": request.reason},
        )
        raw = operator.call("GET", _code_path(code))
    return _institution(raw, _linked_banks(db))


def revoke_client(  # noqa: PLR0913 - one console action on one client of one institution
    db: Session,
    identity: GatewayIdentity,
    code: str,
    client_id: str,
    request: GatewayClientRevoke,
    transport: httpx.BaseTransport | None = None,
) -> GatewayInstitution:
    _refuse_aequoros_bank(db, code)
    with _operator(identity, transport) as operator:
        institution = operator.call("GET", _code_path(code))
        clients = institution.get("clients")
        owned = {
            str(item.get("client_id"))
            for item in cast(list[dict[str, Any]], clients if isinstance(clients, list) else [])
        }
        if client_id not in owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution {code} has no client {client_id}.",
            )
        operator.call(
            "POST",
            f"/ops/api/clients/{quote(client_id, safe='')}/revoke",
            {"reason": request.reason},
        )
        raw = operator.call("GET", _code_path(code))
    return _institution(raw, _linked_banks(db))
