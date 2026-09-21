"""No test may reach a model, and no ordinary process may load the SDK.

Two different guarantees:

* **The suite never calls out.** ``AnthropicModel`` refuses to construct in
  tests, so a forgotten ``use_model`` fixture is a loud failure rather than a
  silent real request billed to somebody's account.
* **The SDK stays out of the API, the core worker and the operator app.** The
  lazy import inside ``AnthropicModel.__init__`` is what holds that, and it is
  checked in a SUBPROCESS because this process has already imported everything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.ai import client as ai_client

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _subprocess_check(script: str) -> str:
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and inline script
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=_BACKEND_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(_BACKEND_ROOT),
            "RUN_INPROCESS_WORKER": "0",
            "DATABASE_URL": "",
            "APP_ENV": "test",
            "AUTH_JWT_SECRET": "test-jwt-signing-secret-not-for-production-00",
            "LOG_LEVEL": "ERROR",
        },
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip().splitlines()[-1]


def test_constructing_a_real_model_in_tests_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ai_client.RealModelForbiddenError):
        ai_client.AnthropicModel()


def test_importing_the_api_does_not_load_the_sdk() -> None:
    assert (
        _subprocess_check("import sys; import app.main; print('anthropic' in sys.modules)")
        == "False"
    )


def test_importing_the_worker_does_not_load_the_sdk() -> None:
    """The core worker holds no model credential and loads no model code."""
    assert (
        _subprocess_check("import sys; import app.worker; print('anthropic' in sys.modules)")
        == "False"
    )


def test_importing_the_operator_app_does_not_load_the_sdk() -> None:
    assert (
        _subprocess_check("import sys; import app.operator.main; print('anthropic' in sys.modules)")
        == "False"
    )


def test_even_importing_the_client_module_does_not_load_the_sdk() -> None:
    """The import is inside ``__init__``, not at module scope."""
    assert (
        _subprocess_check(
            "import sys; import app.services.ai.client; print('anthropic' in sys.modules)"
        )
        == "False"
    )


def test_the_recorded_backend_is_refused_outside_local_and_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_MODEL_BACKEND", "recorded")
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    with pytest.raises(ai_client.RealModelForbiddenError):
        ai_client.get_model()


def test_a_test_override_is_always_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    get_settings.cache_clear()
    canned = ai_client.RecordedModel([])
    with ai_client.use_model(canned):
        assert ai_client.get_model() is canned


def test_backend_configured_is_false_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    assert ai_client.backend_configured() is False


def test_the_key_is_not_part_of_the_settings_aggregate() -> None:
    """A process that does not call the model never parses the key."""
    settings = get_settings()
    dumped = settings.model_dump()
    assert "anthropic_api_key" not in str(dumped).casefold()
    assert not hasattr(settings.ai, "anthropic_api_key")


def test_the_credential_settings_class_hides_its_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SecretStr: the key must not land in a log line or a traceback repr."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    credentials = ai_client.AiCredentialSettings()
    assert credentials.anthropic_api_key is not None
    assert "sk-ant-secret-value" not in repr(credentials)
    assert credentials.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"
