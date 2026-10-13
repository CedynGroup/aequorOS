from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.key_management import aws
from app.core.key_management.settings import KeyManagementSettings
from app.core.key_management.types import KeyReference, KeyUnavailableError


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize("platform_account", [None, "111122223333", "444455556666"])
def test_deployed_custody_requires_a_key_outside_the_platform_account(
    environment: str,
    platform_account: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    get_settings.cache_clear()
    settings = KeyManagementSettings.model_construct(platform_account=platform_account)
    monkeypatch.setattr(aws, "get_key_settings", lambda: settings)
    key = KeyReference(
        "aws_kms",
        "arn:aws:kms:us-east-1:111122223333:key/bank-owned",
        "us-east-1",
        "111122223333",
    )
    if platform_account == "444455556666":
        aws.validate_reference(key)
    else:
        with pytest.raises(KeyUnavailableError):
            aws.validate_reference(key)
