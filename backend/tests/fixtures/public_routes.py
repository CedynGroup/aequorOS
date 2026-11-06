"""Reviewed anonymous surfaces, bounded by application, method, path and handler.

These entries preserve existing behavior, not permission to expose another
route. Adding an endpoint or method requires an explicit public-access review.
Credential exchanges and signed capabilities are NOT public declarations.
"""

from __future__ import annotations

from tests.support.route_authorization import PublicRouteReview, RouteKey

_FRAMEWORK_REFERENCE = "app/main.py:create_app; app/operator/main.py:create_operator_app"
_SCHEMA = PublicRouteReview(
    "fastapi.applications:FastAPI.setup.<locals>.openapi",
    "Existing FastAPI schema endpoint, with no credentials or bank records.",
    _FRAMEWORK_REFERENCE,
)
_SWAGGER = PublicRouteReview(
    "fastapi.applications:FastAPI.setup.<locals>.swagger_ui_html",
    "Existing FastAPI interactive API documentation.",
    _FRAMEWORK_REFERENCE,
)
_REDIRECT = PublicRouteReview(
    "fastapi.applications:FastAPI.setup.<locals>.swagger_ui_redirect",
    "Existing FastAPI Swagger OAuth redirect document, not token issuance.",
    _FRAMEWORK_REFERENCE,
)
_REDOC = PublicRouteReview(
    "fastapi.applications:FastAPI.setup.<locals>.redoc_html",
    "Existing FastAPI ReDoc documentation.",
    _FRAMEWORK_REFERENCE,
)

REVIEWED_PUBLIC_ROUTES: dict[RouteKey, PublicRouteReview] = {
    ("tenant", "GET", "/openapi.json"): _SCHEMA,
    ("tenant", "HEAD", "/openapi.json"): _SCHEMA,
    ("tenant", "GET", "/docs"): _SWAGGER,
    ("tenant", "HEAD", "/docs"): _SWAGGER,
    ("tenant", "GET", "/docs/oauth2-redirect"): _REDIRECT,
    ("tenant", "HEAD", "/docs/oauth2-redirect"): _REDIRECT,
    ("tenant", "GET", "/redoc"): _REDOC,
    ("tenant", "HEAD", "/redoc"): _REDOC,
    ("operator", "GET", "/openapi.json"): _SCHEMA,
    ("operator", "HEAD", "/openapi.json"): _SCHEMA,
    ("operator", "GET", "/docs"): _SWAGGER,
    ("operator", "HEAD", "/docs"): _SWAGGER,
    ("operator", "GET", "/docs/oauth2-redirect"): _REDIRECT,
    ("operator", "HEAD", "/docs/oauth2-redirect"): _REDIRECT,
    ("operator", "GET", "/redoc"): _REDOC,
    ("operator", "HEAD", "/redoc"): _REDOC,
    ("tenant", "GET", "/api/health/live"): PublicRouteReview(
        "app.api.health:live",
        "Unauthenticated orchestrator liveness probe, with no bank records.",
        "app/api/health.py:live",
    ),
    ("tenant", "GET", "/api/health/ready"): PublicRouteReview(
        "app.api.health:ready",
        "Unauthenticated orchestrator readiness probe; diagnosis stays in controlled logs.",
        "app/api/health.py:ready and public readiness disclosure contract",
    ),
    ("operator", "GET", "/operator/health"): PublicRouteReview(
        "app.operator.main:create_operator_app.<locals>.operator_health",
        "Explicit unauthenticated operator health probe.",
        "app/operator/main.py:create_operator_app",
    ),
    ("tenant", "GET", "/api/v1/auth/sso/status"): PublicRouteReview(
        "app.identity.api.auth:sso_status",
        "Explicit public boolean SSO-availability probe for the login page.",
        "app/identity/api/auth.py:sso_status",
    ),
    ("tenant", "POST", "/api/v1/auth/logout"): PublicRouteReview(
        "app.identity.api.auth:logout",
        "Explicit unauthenticated logout: always 204, even for an unknown refresh token.",
        "app/identity/api/auth.py:logout",
    ),
}
