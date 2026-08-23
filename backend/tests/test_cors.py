from __future__ import annotations

from typing import cast

from starlette.middleware.cors import CORSMiddleware

from app.core.config import get_operator_settings, get_settings
from app.main import create_app
from app.operator.main import create_operator_app


def _cors_options(app: object) -> dict[str, object]:
    middleware = next(
        item for item in app.user_middleware if item.cls is CORSMiddleware  # type: ignore[attr-defined]
    )
    return middleware.kwargs


def test_tenant_cors_uses_explicit_configurable_methods_and_headers(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://bank.example.test")
    monkeypatch.setenv("CORS_METHODS", "GET,POST,PATCH,OPTIONS")
    monkeypatch.setenv("CORS_HEADERS", "Authorization,Content-Type,X-Request-ID")
    get_settings.cache_clear()

    options = _cors_options(create_app())
    methods = cast("list[str]", options["allow_methods"])
    headers = cast("list[str]", options["allow_headers"])

    assert methods == ["GET", "POST", "PATCH", "OPTIONS"]
    assert headers == ["Authorization", "Content-Type", "X-Request-ID"]
    assert "*" not in methods
    assert "*" not in headers


def test_operator_cors_uses_explicit_configurable_methods_and_headers(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_CORS_ORIGINS", "https://console.example.test")
    monkeypatch.setenv("OPERATOR_CORS_METHODS", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
    monkeypatch.setenv("OPERATOR_CORS_HEADERS", "Authorization,Content-Type")
    get_operator_settings.cache_clear()

    options = _cors_options(create_operator_app())
    methods = cast("list[str]", options["allow_methods"])
    headers = cast("list[str]", options["allow_headers"])

    assert methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert headers == ["Authorization", "Content-Type"]
    assert "*" not in methods
    assert "*" not in headers