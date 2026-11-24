"""Institution principals for ``/v1`` (ARCHITECTURE.md layers 1 and 2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.audit.chain import Actor
from app.auth.clients import authenticate_client, insufficient_scope
from app.core.context import get_ctx
from app.core.errors import ApiError

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AccessToken",
    description="Gateway access token from POST /oauth/token, bound to the client certificate "
    "presented with every call (mutual TLS).",
)


@dataclass(frozen=True)
class InstitutionPrincipal:
    client_id: str
    institution_id: str
    institution_code: str
    institution_name: str
    environment: str
    scopes: frozenset[str]
    thumbprint: str
    certificate_not_after: datetime

    @property
    def actor(self) -> Actor:
        return Actor(
            "institution", self.client_id, self.institution_name, f"API client {self.client_id}"
        )


def require_scope(scope: str) -> Callable[..., InstitutionPrincipal]:
    def dependency(
        request: Request,
        _credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> InstitutionPrincipal:
        verified = authenticate_client(request, get_ctx(request))
        institution = verified.client.institution
        if verified.client.kind != "institution" or institution is None:
            raise ApiError("forbidden", "Operator clients cannot use the institution API.")
        if scope not in verified.scopes:
            raise insufficient_scope(scope)
        return InstitutionPrincipal(
            client_id=verified.client.client_id,
            institution_id=institution.id,
            institution_code=institution.code,
            institution_name=institution.name,
            environment=verified.client.environment,
            scopes=verified.scopes,
            thumbprint=verified.certificate.thumbprint,
            certificate_not_after=verified.certificate.not_after,
        )

    return dependency
