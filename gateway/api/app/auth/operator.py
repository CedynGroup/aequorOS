"""Operator principals for ``/ops/api`` (SPEC Part 5): an operator machine client with
scope ``ops:enrol``. Institution tokens and staff ID tokens are refused."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.audit.chain import Actor
from app.auth.clients import OPERATOR_SCOPE, authenticate_client, insufficient_scope
from app.core.context import get_ctx

operator_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="OperatorAccessToken",
    description="Operator client access token (scope ops:enrol), bound to the operator's "
    "client certificate.",
)


@dataclass(frozen=True)
class OperatorPrincipal:
    client_id: str

    @property
    def actor(self) -> Actor:
        return Actor(
            "operator", self.client_id, "Channel operator", f"Operator client {self.client_id}"
        )


def require_operator(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Depends(operator_bearer),
) -> OperatorPrincipal:
    verified = authenticate_client(request, get_ctx(request))
    if verified.client.kind != "operator" or OPERATOR_SCOPE not in verified.scopes:
        raise insufficient_scope(OPERATOR_SCOPE)
    return OperatorPrincipal(client_id=verified.client.client_id)
