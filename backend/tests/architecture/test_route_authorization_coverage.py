"""Every registered endpoint needs a bounded, reviewable authorization rule.

Exercise the consumer over real FastAPI/Starlette registrations, including
negative controls. This guard checks declarations, not permission semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, WebSocket
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Host, Mount, Route
from starlette.types import Receive, Scope, Send

from app.api.deps import get_current_principal
from app.core.config import get_operator_settings, get_settings
from app.main import create_app
from app.operator.deps import get_operator_context
from app.operator.main import create_operator_app
from tests.fixtures.public_routes import REVIEWED_PUBLIC_ROUTES
from tests.support.route_authorization import (
    AuthorizationCatalog,
    EndpointDeclaration,
    PublicRouteReview,
    RouteKey,
    RuleDeclaration,
    callable_name,
    coverage_errors,
    load_catalog,
    registered_endpoints,
)

_CATALOG_PATH = Path(__file__).parents[1] / "fixtures" / "route_authorization.json"


@pytest.fixture
def applications(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Starlette]]:
    monkeypatch.setenv("OPERATOR_DATABASE_URL", "")
    monkeypatch.setenv("OPERATOR_DEV_AUTH_ENABLED", "0")
    get_operator_settings.cache_clear()
    # tests/conftest.py isolates these before application imports as well.
    assert not get_settings().worker.run_inprocess_worker
    try:
        yield {"tenant": create_app(), "operator": create_operator_app()}
    finally:
        get_operator_settings.cache_clear()


def test_every_registered_endpoint_has_its_declared_authorization(
    applications: dict[str, Starlette],
) -> None:
    errors = coverage_errors(
        applications,
        load_catalog(_CATALOG_PATH),
        {"tenant": get_current_principal, "operator": get_operator_context},
        REVIEWED_PUBLIC_ROUTES,
    )
    assert not errors, (
        "Authorization declaration coverage failed. Review the exact endpoint and existing "
        "checks before editing the catalog; never infer public access from missing auth:\n  "
        + "\n  ".join(errors)
    )


def _authenticate(authorization: Annotated[str | None, Header()] = None) -> str:
    if authorization != "Bearer fixture":
        raise HTTPException(status_code=401)
    return "fixture"


def _scoped_gate(principal: Annotated[str, Depends(_authenticate)]) -> None:
    if principal != "fixture":
        raise HTTPException(status_code=403)


class _ClassGate:
    def __init__(self, principal: Annotated[str, Depends(_authenticate)]) -> None:
        _scoped_gate(principal)


class _ReplacementClassGate(_ClassGate):
    pass


class _CallableGate:
    def __call__(self, principal: Annotated[str, Depends(_authenticate)]) -> None:
        _scoped_gate(principal)

    def method(self, principal: Annotated[str, Depends(_authenticate)]) -> None:
        _scoped_gate(principal)


_CALLABLE_GATE = _CallableGate()


def _endpoint() -> dict[str, bool]:
    return {"ok": True}


def _replacement() -> dict[str, bool]:
    return {"replacement": True}


def _declaration(path: str, method: str = "GET") -> EndpointDeclaration:
    return EndpointDeclaration(
        application="tenant", method=method, path=path, endpoint=callable_name(_endpoint)
    )


def _protected_rule(
    *routes: EndpointDeclaration, gate: Callable[..., object] = _scoped_gate
) -> RuleDeclaration:
    return RuleDeclaration(
        name="fixture-scoped",
        kind="dependency",
        basis="Fixture scope gate contains a nested authentication dependency.",
        references=("test_route_authorization_coverage.py:_scoped_gate",),
        authentication="tenant",
        dependencies=tuple(sorted((callable_name(_authenticate), callable_name(gate)))),
        routes=routes,
    )


def _public_rule(*routes: EndpointDeclaration) -> RuleDeclaration:
    return RuleDeclaration(
        name="fixture-public",
        kind="public",
        basis="Explicit fixture public endpoint, not an inferred exemption.",
        references=("test_route_authorization_coverage.py:_endpoint",),
        authentication=None,
        dependencies=(),
        routes=routes,
    )


def _catalog(*rules: RuleDeclaration) -> AuthorizationCatalog:
    return AuthorizationCatalog(schema_version=1, baseline="fixture", rules=rules)


def _app() -> FastAPI:
    return FastAPI(openapi_url=None)


def _public_reviews(*paths: str) -> dict[RouteKey, PublicRouteReview]:
    return {
        ("tenant", "GET", path): PublicRouteReview(
            callable_name(_endpoint), "Reviewed public fixture.", "Fixture decision."
        )
        for path in paths
    }


def test_inherited_and_nested_dependency_declaration_passes() -> None:
    app = _app()
    router = APIRouter(dependencies=[Depends(_scoped_gate)])
    router.add_api_route("/protected", _endpoint)
    app.include_router(router)
    assert (
        coverage_errors(
            {"tenant": app},
            _catalog(_protected_rule(_declaration("/protected"))),
            {"tenant": _authenticate},
            {},
        )
        == []
    )
    client = TestClient(app)
    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"Authorization": "Bearer fixture"}).json() == {
        "ok": True
    }


@pytest.mark.parametrize(
    ("gate", "name"),
    [
        (_scoped_gate, f"{_scoped_gate.__module__}:{_scoped_gate.__qualname__}"),
        (_ClassGate, f"{_ClassGate.__module__}:{_ClassGate.__qualname__}"),
        (_CALLABLE_GATE, f"{_CallableGate.__module__}:{_CallableGate.__qualname__}"),
        (
            _CALLABLE_GATE.method,
            f"{_CallableGate.method.__module__}:{_CallableGate.method.__qualname__}",
        ),
    ],
    ids=["function", "class", "callable-instance", "bound-method"],
)
def test_dependency_callable_provenance(gate: Callable[..., object], name: str) -> None:
    assert callable_name(gate) == name
    app = _app()
    app.add_api_route("/protected", _endpoint, dependencies=[Depends(gate)])
    assert (
        coverage_errors(
            {"tenant": app},
            _catalog(_protected_rule(_declaration("/protected"), gate=gate)),
            {"tenant": _authenticate},
            {},
        )
        == []
    )
    client = TestClient(app)
    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"Authorization": "Bearer fixture"}).json() == {
        "ok": True
    }


def test_replacing_class_gate_fails_even_when_authentication_remains() -> None:
    catalog = _catalog(_protected_rule(_declaration("/protected"), gate=_ClassGate))
    app = _app()
    app.add_api_route("/protected", _endpoint, dependencies=[Depends(_ClassGate)])
    assert coverage_errors({"tenant": app}, catalog, {"tenant": _authenticate}, {}) == []

    replacement = _app()
    replacement.add_api_route(
        "/protected", _endpoint, dependencies=[Depends(_ReplacementClassGate)]
    )
    errors = coverage_errors({"tenant": replacement}, catalog, {"tenant": _authenticate}, {})
    assert len(errors) == 1
    assert "dependency declaration drift" in errors[0]
    assert _ClassGate.__qualname__ in errors[0]
    assert _ReplacementClassGate.__qualname__ in errors[0]


@pytest.mark.parametrize("protected", [False, True])
def test_new_endpoint_is_unclassified_even_when_it_authenticates(protected: bool) -> None:
    app = _app()
    app.add_api_route("/public", _endpoint)
    app.add_api_route("/new", _endpoint, dependencies=[Depends(_scoped_gate)] if protected else [])
    errors = coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/public"))),
        {"tenant": _authenticate},
        _public_reviews("/public"),
    )
    assert errors == ["tenant GET /new: unclassified endpoint"]


def test_removing_a_scope_gate_fails_even_when_authentication_remains() -> None:
    app = _app()
    app.add_api_route("/protected", _endpoint, dependencies=[Depends(_authenticate)])
    errors = coverage_errors(
        {"tenant": app},
        _catalog(_protected_rule(_declaration("/protected"))),
        {"tenant": _authenticate},
        {},
    )
    assert len(errors) == 1
    assert "dependency declaration drift" in errors[0]


def test_dependency_name_spoof_does_not_count_as_the_authentication_boundary() -> None:
    def impostor() -> str:
        return "not authenticated"

    impostor.__module__ = _authenticate.__module__
    impostor.__qualname__ = _authenticate.__qualname__
    impostor.__name__ = _authenticate.__name__
    app = _app()
    app.add_api_route("/protected", _endpoint, dependencies=[Depends(impostor)])
    rule = RuleDeclaration(
        name="fixture-authentication",
        kind="dependency",
        basis="Only the actual authenticator counts.",
        references=("fixture",),
        authentication="tenant",
        dependencies=(callable_name(_authenticate),),
        routes=(_declaration("/protected"),),
    )
    assert coverage_errors({"tenant": app}, _catalog(rule), {"tenant": _authenticate}, {}) == [
        "tenant GET /protected: missing declared authentication boundary tenant"
    ]


def test_public_declaration_requires_separate_exact_review() -> None:
    app = _app()
    app.add_api_route("/public", _endpoint)
    catalog = _catalog(_public_rule(_declaration("/public")))
    assert coverage_errors({"tenant": app}, catalog, {}, {}) == [
        "tenant GET /public: missing exact reviewed public declaration"
    ]
    assert coverage_errors({"tenant": app}, catalog, {}, _public_reviews("/public")) == []


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/public"), ("GET", "/public/child"), ("GET", "/public-other")],
)
def test_public_review_does_not_expand_to_methods_or_neighbor_paths(method: str, path: str) -> None:
    app = _app()
    app.add_api_route("/public", _endpoint)
    app.add_api_route(path, _endpoint, methods=[method])
    assert coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/public"))),
        {},
        _public_reviews("/public"),
    ) == [f"tenant {method} {path}: unclassified endpoint"]


def test_catalog_alone_cannot_expand_public_access() -> None:
    app = _app()
    app.add_api_route("/public", _endpoint)
    app.add_api_route("/another-public", _endpoint)
    assert coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/public"), _declaration("/another-public"))),
        {},
        _public_reviews("/public"),
    ) == ["tenant GET /another-public: missing exact reviewed public declaration"]


def test_replacing_a_public_handler_invalidates_its_declaration() -> None:
    app = _app()
    app.add_api_route("/public", _replacement)
    errors = coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/public"))),
        {},
        _public_reviews("/public"),
    )
    assert len(errors) == 1
    assert "endpoint changed" in errors[0]


def test_removed_routes_and_unused_public_reviews_fail() -> None:
    app = _app()
    app.add_api_route("/remaining", _endpoint)
    errors = coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/removed"))),
        {},
        _public_reviews("/removed", "/obsolete"),
    )
    assert errors == [
        "tenant GET /obsolete: unused reviewed public declaration",
        "tenant GET /remaining: unclassified endpoint",
        "tenant GET /removed: stale authorization declaration",
    ]


def test_duplicate_registrations_and_declarations_fail() -> None:
    app = _app()
    app.add_api_route("/public", _endpoint)
    app.add_api_route("/public", _endpoint)
    assert coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/public"), _declaration("/public"))),
        {},
        _public_reviews("/public"),
    ) == [
        "tenant GET /public: duplicate authorization declaration",
        "tenant GET /public: duplicate registered endpoint",
    ]


def test_nested_mounts_use_full_paths_and_application_identity() -> None:
    child = _app()
    child.add_api_route("/protected", _endpoint, dependencies=[Depends(_scoped_gate)])
    middle = Starlette(routes=[Mount("/inner", child)])
    app = Starlette(routes=[Mount("/outer", middle)])
    declaration = _declaration("/outer/inner/protected")
    catalog = _catalog(_protected_rule(declaration))
    assert coverage_errors({"tenant": app}, catalog, {"tenant": _authenticate}, {}) == []
    errors = coverage_errors(
        {"tenant": app, "operator": app}, catalog, {"tenant": _authenticate}, {}
    )
    assert errors == ["operator GET /outer/inner/protected: unclassified endpoint"]


def test_framework_head_is_a_separate_method_not_an_implicit_public_grant() -> None:
    async def framework_endpoint(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/schema", framework_endpoint)])
    rows, errors = registered_endpoints({"tenant": app}, {})
    assert not errors
    assert [row.key for row in rows] == [
        ("tenant", "GET", "/schema"),
        ("tenant", "HEAD", "/schema"),
    ]
    declarations = tuple(
        EndpointDeclaration(
            application="tenant",
            method=method,
            path="/schema",
            endpoint=callable_name(framework_endpoint),
        )
        for method in ("GET", "HEAD")
    )
    reviews = {
        declaration.key: PublicRouteReview(
            callable_name(framework_endpoint), "Framework fixture.", "Fixture decision."
        )
        for declaration in declarations
    }
    assert (
        coverage_errors({"tenant": app}, _catalog(_public_rule(*declarations)), {}, reviews) == []
    )
    assert coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(declarations[0])),
        {},
        {declarations[0].key: reviews[declarations[0].key]},
    ) == ["tenant HEAD /schema: unclassified endpoint"]


def test_websocket_dependencies_are_enumerated_and_require_a_declaration() -> None:
    async def socket_endpoint(socket: WebSocket) -> None:
        await socket.accept()
        await socket.close()

    app = _app()
    app.add_api_websocket_route("/socket", socket_endpoint, dependencies=[Depends(_scoped_gate)])
    declaration = EndpointDeclaration(
        application="tenant",
        method="WEBSOCKET",
        path="/socket",
        endpoint=callable_name(socket_endpoint),
    )
    assert (
        coverage_errors(
            {"tenant": app},
            _catalog(_protected_rule(declaration)),
            {"tenant": _authenticate},
            {},
        )
        == []
    )
    assert coverage_errors(
        {"tenant": app},
        _catalog(_public_rule(_declaration("/unmounted"))),
        {"tenant": _authenticate},
        _public_reviews("/unmounted"),
    ) == [
        "tenant GET /unmounted: stale authorization declaration",
        "tenant WEBSOCKET /socket: unclassified endpoint",
    ]


def test_opaque_mounts_unsupported_routes_and_empty_populations_fail_closed() -> None:
    async def opaque(_scope: Scope, _receive: Receive, _send: Send) -> None:
        raise AssertionError("Inventory must never call opaque applications.")

    app = Starlette(routes=[Mount("/opaque", opaque), Host("host.test", Starlette())])
    rows, errors = registered_endpoints({"tenant": app}, {})
    assert rows == []
    assert errors == [
        "tenant /opaque: opaque or empty mount requires review",
        "tenant : unsupported route type Host",
        "tenant: no registered endpoints inspected",
    ]
    assert registered_endpoints({}, {}) == ([], ["no applications inspected"])


def test_catalog_parser_rejects_unknown_rule_kinds_and_fields() -> None:
    rule = _public_rule(_declaration("/public")).model_dump()
    rule["kind"] = "default-public"
    with pytest.raises(ValidationError):
        RuleDeclaration.model_validate(rule)
    rule["kind"] = "public"
    rule["ignore_missing_auth"] = True
    with pytest.raises(ValidationError):
        RuleDeclaration.model_validate(rule)
