"""Operator identity: dev-token gate, production boot refusal, 401 envelope.

This module's other-suite cases live in ``test_auth_integration.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core import security
from app.core.config import get_operator_settings, get_settings
from app.core.security import is_loopback_issuer_allowed
from app.operator.main import create_operator_app


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_dev_auth_refuses_boot_on_every_deployed_environment(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("OPERATOR_DEV_AUTH_ENABLED", "1")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="OPERATOR_DEV_AUTH_ENABLED"):
            create_operator_app()
    finally:
        get_settings.cache_clear()
        get_operator_settings.cache_clear()


def test_an_unrecognised_app_env_never_reaches_the_guard_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of "anything unrecognised is treated as deployed".

    ``APP_ENV`` is a closed Literal, so a typo like ``prod`` or a new name like
    ``uat`` cannot be constructed in the first place — settings refuse it. The
    inverted guard and the closed enum are complementary: the enum stops a
    value nobody vetted from existing, and the allow-list stops a vetted one
    from silently taking the undeployed branch.
    """
    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="APP_ENV"):
            get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_dev_auth_still_works_where_the_developer_IS_the_deployment(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    """The carve-out must still exist, or the inversion would be a break, not a
    fix: ``local`` and ``test`` are the two environments a developer runs."""
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("OPERATOR_DEV_AUTH_ENABLED", "1")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    try:
        assert create_operator_app() is not None
    finally:
        get_settings.cache_clear()
        get_operator_settings.cache_clear()


class TestLoopbackIssuerCarveOut:
    """Plain-http OIDC endpoints: loopback-only, and only on an UNDEPLOYED
    environment (``local``/``test``). Exists so the workforce flow can be
    exercised locally against a stub IdP.

    Unlike operator dev auth and ``outbound``'s private-target hatch, this
    carve-out has no second opt-in flag, and the value it screens (an SSO
    connection's ``issuer``) is set by a tenant admin — so "not production" was
    not tight enough. It used to permit plain-http loopback on ``staging``,
    where the same containers run on a reachable host and ``127.0.0.1`` is the
    operator control plane (:8100) and OpenBao (:8200).
    """

    def test_loopback_http_allowed_on_an_undeployed_environment(self) -> None:
        # The suite runs with APP_ENV=test (see tests/conftest.py).
        assert is_loopback_issuer_allowed("http://127.0.0.1:8110")
        assert is_loopback_issuer_allowed("http://localhost:8110")

    def test_non_loopback_http_always_rejected(self) -> None:
        assert not is_loopback_issuer_allowed("http://idp.example.com")
        assert not is_loopback_issuer_allowed("http://192.168.1.10:8110")

    @pytest.mark.parametrize("app_env", ["staging", "production"])
    def test_loopback_http_rejected_on_every_deployed_environment(
        self, monkeypatch: pytest.MonkeyPatch, app_env: str
    ) -> None:
        """Staging is a DEPLOYED environment, so it gets production's rule.

        Without this, an org admin could aim the backend's discovery fetch at
        the staging host's own loopback services through a field they control.
        """
        monkeypatch.setenv("APP_ENV", app_env)
        get_settings.cache_clear()
        try:
            assert not is_loopback_issuer_allowed("http://127.0.0.1:8110")
            assert not is_loopback_issuer_allowed("http://localhost:8100")
            assert not is_loopback_issuer_allowed("http://[::1]:8200")
        finally:
            get_settings.cache_clear()

    def test_a_staging_deployment_refuses_a_loopback_issuer_at_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The end-to-end consequence: on staging the carve-out no longer
        short-circuits the https requirement, so the fetch never happens."""
        monkeypatch.setenv("APP_ENV", "staging")
        get_settings.cache_clear()
        security._discover_jwks_uri.cache_clear()
        try:
            with pytest.raises(security.TokenInvalidError, match="must be https"):
                security._discover_jwks_uri("http://127.0.0.1:8100")
        finally:
            security._discover_jwks_uri.cache_clear()
            get_settings.cache_clear()

    def test_https_discovery_rule_unchanged_for_non_loopback(self) -> None:
        with pytest.raises(security.TokenInvalidError, match="must be https"):
            security._discover_jwks_uri("http://idp.example.com")
