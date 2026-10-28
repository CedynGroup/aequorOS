"""The explicit local endpoint must never alter deployed AWS routing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.key_management.aws import kms_client
from app.core.key_management.settings import KeyManagementSettings, get_key_settings
from app.main import create_app


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_deployed_startup_refuses_emulator(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("BANK_KMS_ENDPOINT_URL", "http://127.0.0.1:18080")
    get_settings.cache_clear()
    get_key_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="permitted only in local/test"):
            create_app()
    finally:
        get_settings.cache_clear()
        get_key_settings.cache_clear()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://kms.us-east-1.amazonaws.com",
        "http://example.com:8080",
        "http://localhost.evil.invalid:8080",
        "http://user:password@localhost:8080",
        "http://localhost:8080/path",
        "http://localhost:8080?token=secret",
        "file:///tmp/kms",
    ],
)
def test_endpoint_refuses_remote_or_credential_bearing_urls(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="local KMS emulator"):
        KeyManagementSettings.model_validate({"BANK_KMS_ENDPOINT_URL": endpoint})


def test_explicit_emulator_ignores_ambient_credentials_and_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BANK_KMS_ENDPOINT_URL", "http://127.0.0.1:18080")
    monkeypatch.setenv("AWS_ENDPOINT_URL_KMS", "https://unwanted.invalid")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-must-not-be-used")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-must-not-be-used")
    get_key_settings.cache_clear()
    try:
        client = kms_client("us-east-1")
        assert client.meta.endpoint_url == "http://127.0.0.1:18080"
        client.close()
    finally:
        get_key_settings.cache_clear()


def test_no_override_keeps_real_aws_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-no-network")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-no-network")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:18080")
    monkeypatch.setenv("AWS_ENDPOINT_URL_KMS", "http://127.0.0.1:18080")
    get_key_settings.cache_clear()
    client = kms_client("us-east-1")
    assert client.meta.endpoint_url == "https://kms.us-east-1.amazonaws.com"
    client.close()
