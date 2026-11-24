"""``GET /v1/.well-known/jwks.json``: the gateway receipt keys (current and retired)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.context import get_ctx
from app.keys.jwk import public_jwk

router = APIRouter(tags=["Keys"])


class PublicJwk(BaseModel):
    kty: Literal["EC"]
    crv: Literal["P-256"]
    x: str
    y: str
    kid: str
    use: Literal["sig"]
    alg: Literal["ES256"]


class Jwks(BaseModel):
    keys: list[PublicJwk]


@router.get(
    "/v1/.well-known/jwks.json",
    response_model=Jwks,
    summary="Gateway receipt-key public keys",
    description="Public, no authentication. Verifies receipts and access tokens offline.",
)
def gateway_jwks(request: Request) -> Jwks:
    keys = get_ctx(request).keys.public_keys("receipt")
    return Jwks.model_validate({"keys": [public_jwk(key.public_key, key.kid) for key in keys]})
