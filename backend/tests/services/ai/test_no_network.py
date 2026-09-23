"""No test may reach a model, and no ordinary process may load a vendor's code.

Two different guarantees, and since D-053 both are per VENDOR:

* **The suite never calls out.** Every real client refuses to construct in tests,
  so a forgotten ``use_model`` fixture is a loud failure rather than a silent real
  request billed to somebody's account. A failover test that reached tier 2 or 3
  for real would bill a second and a third account, so the guard has to cover all
  three or it silently stops covering the feature.
* **No vendor's transport or credential reaches the API, the core worker or the
  operator app.** The lazy import inside each adapter is what holds that, plus
  the tier resolving its adapters through ``importlib`` rather than importing
  them, and it is checked in a SUBPROCESS because this process has already
  imported everything.
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


@pytest.mark.parametrize(
    "module",
    ["app.services.ai.openai_model", "app.services.ai.google_model"],
)
def test_importing_a_vendor_adapter_does_not_load_its_transport(module: str) -> None:
    """The same rule as the SDK, for the two adapters that speak HTTP directly."""
    assert (
        _subprocess_check(f"import sys; import {module}; print('httpx' in sys.modules)") == "False"
    )


@pytest.mark.parametrize("app_module", ["app.main", "app.worker", "app.operator.main"])
def test_no_ordinary_process_loads_a_vendor_adapter(app_module: str) -> None:
    """The tier resolves its adapters with ``importlib`` at call time, and the
    enqueue gate reaches them the same way, so no process that merely imports the
    application parses a vendor credential class."""
    script = (
        f"import sys; import {app_module}; "
        "print(any(name.startswith('app.services.ai.openai_model') "
        "or name.startswith('app.services.ai.google_model') for name in sys.modules))"
    )
    assert _subprocess_check(script) == "False"


@pytest.mark.parametrize(
    ("module_name", "class_name"),
    [
        ("app.services.ai.openai_model", "OpenAiModel"),
        ("app.services.ai.google_model", "GoogleModel"),
    ],
)
def test_constructing_any_real_vendor_client_in_tests_is_refused(
    monkeypatch: pytest.MonkeyPatch, module_name: str, class_name: str
) -> None:
    import importlib  # noqa: PLC0415 - resolving the adapter by name, as the tier does

    module = importlib.import_module(module_name)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    get_settings.cache_clear()
    with pytest.raises(ai_client.RealModelForbiddenError):
        getattr(module, class_name)()


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


def test_no_vendor_key_is_part_of_the_settings_aggregate() -> None:
    """A process that does not call a vendor never parses that vendor's key."""
    settings = get_settings()
    dumped = str(settings.model_dump()).casefold()
    for name in ("anthropic_api_key", "openai_api_key", "gemini_api_key"):
        assert name not in dumped
        assert not hasattr(settings.ai, name)


def test_the_credential_settings_class_hides_its_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SecretStr: the key must not land in a log line or a traceback repr."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    credentials = ai_client.AiCredentialSettings()
    assert credentials.anthropic_api_key is not None
    assert "sk-ant-secret-value" not in repr(credentials)
    assert credentials.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"
