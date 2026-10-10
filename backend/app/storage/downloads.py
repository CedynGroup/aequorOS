"""Short-lived application download capabilities for encrypted bank objects.

Every redemption uses StorageClient.read and hence the bank key. An S3 URL
would deliver ciphertext and could not enforce revocation at redemption time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import get_settings, is_undeployed_environment
from app.core.tls import require_https
from app.storage.client import StorageAccessError, StorageLocation, Tier
from app.storage.config import get_storage_settings

_AUDIENCE = "bank-object-download-v1"


class DownloadCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    tier: Tier
    path: str
    version: str | None
    exp: int
    aud: str

    def location(self) -> StorageLocation:
        return StorageLocation(self.slug, self.tier, self.path)


def _secret() -> str:
    secret = get_settings().auth.jwt_secret
    if not secret:
        raise StorageAccessError("Encrypted download signing is not configured.")
    return secret


def issue(location: StorageLocation, version: str | None, expires: int) -> str:
    if not 1 <= expires <= 900:
        raise StorageAccessError("Download lifetime must be between 1 and 900 seconds.")
    capability = DownloadCapability(
        slug=location.institution_slug,
        tier=location.tier,
        path=location.object_path,
        version=version,
        exp=int((datetime.now(UTC) + timedelta(seconds=expires)).timestamp()),
        aud=_AUDIENCE,
    )
    token = jwt.encode(capability.model_dump(), _secret(), algorithm="HS256")
    base = get_storage_settings().download_base_url
    if base:
        require_https(base, field="STORAGE_DOWNLOAD_BASE_URL")
    if not is_undeployed_environment() and not base:
        raise StorageAccessError("STORAGE_DOWNLOAD_BASE_URL is required for encrypted downloads.")
    return f"{base.rstrip('/')}/api/v1/storage/download?token={quote(token, safe='')}"


def verify(token: str) -> DownloadCapability:
    try:
        decoded: object = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience=_AUDIENCE,
            options={"require": ["exp", "aud", "slug", "tier", "path"]},
        )
        return DownloadCapability.model_validate(decoded)
    except (jwt.PyJWTError, ValidationError) as exc:
        raise StorageAccessError("The encrypted download link is invalid or expired.") from exc
