"""Compare registered ASGI endpoints with bounded authorization declarations.

This is test infrastructure, never a runtime permission evaluator. Dependency
presence and callable names do not prove a handler's policy is correct.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, MethodType
from typing import Annotated, Literal

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.applications import Starlette
from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute

type RouteKey = tuple[str, str, str]
type ReviewedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@dataclass(frozen=True)
class PublicRouteReview:
    endpoint: str
    basis: str
    reference: str


class EndpointDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    application: Literal["tenant", "operator"]
    method: str = Field(pattern=r"^[A-Z]+$")
    path: str = Field(pattern=r"^/")
    endpoint: ReviewedText

    @property
    def key(self) -> RouteKey:
        return self.application, self.method, self.path


class RuleDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: ReviewedText
    kind: Literal["dependency", "handler", "public", "rejection"]
    basis: ReviewedText
    references: tuple[ReviewedText, ...] = Field(min_length=1)
    authentication: Literal["tenant", "operator"] | None
    dependencies: tuple[str, ...]
    routes: tuple[EndpointDeclaration, ...] = Field(min_length=1)


class AuthorizationCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    baseline: ReviewedText
    rules: tuple[RuleDeclaration, ...] = Field(min_length=1)


def load_catalog(path: Path) -> AuthorizationCatalog:
    return AuthorizationCatalog.model_validate_json(path.read_bytes())


def callable_name(call: object) -> str:
    """Stable provenance, including names assigned by existing gate factories."""
    if isinstance(call, MethodType):
        call = call.__func__
    if isinstance(call, FunctionType):
        name = f"{call.__module__}:{call.__qualname__}"
        if call.__name__ != call.__qualname__.rsplit(".", 1)[-1]:
            name += f"[{call.__name__}]"
        return name
    if isinstance(call, type):
        return f"{call.__module__}:{call.__qualname__}"
    return f"{type(call).__module__}:{type(call).__qualname__}"


def _dependency_calls(dependant: Dependant) -> list[object]:
    calls: list[object] = []
    pending = list(dependant.dependencies)
    while pending:
        node = pending.pop()
        if node.call is not None:
            calls.append(node.call)
        pending.extend(node.dependencies)
    return calls


@dataclass(frozen=True)
class RegisteredEndpoint:
    key: RouteKey
    endpoint: str
    dependencies: tuple[str, ...]
    authentication: frozenset[str]


def _registered_endpoint(
    key: RouteKey, route: Route | WebSocketRoute, authenticators: Mapping[str, object]
) -> RegisteredEndpoint:
    calls = (
        _dependency_calls(route.dependant)
        if isinstance(route, APIRoute | APIWebSocketRoute)
        else []
    )
    return RegisteredEndpoint(
        key=key,
        endpoint=callable_name(route.endpoint),
        dependencies=tuple(sorted({callable_name(call) for call in calls})),
        authentication=frozenset(
            name
            for name, authenticator in authenticators.items()
            if any(call is authenticator for call in calls)
        ),
    )


def _label(key: RouteKey) -> str:
    return " ".join(key)


def _walk_routes(
    application: str,
    routes: Sequence[BaseRoute],
    authenticators: Mapping[str, object],
    *,
    prefix: str = "",
    ancestors: frozenset[int] = frozenset(),
) -> tuple[list[RegisteredEndpoint], list[str]]:
    endpoints: list[RegisteredEndpoint] = []
    errors: list[str] = []
    for route in routes:
        if id(route) in ancestors:
            errors.append(f"{application} {prefix}: cyclic route mount")
        elif isinstance(route, Mount):
            path = prefix + route.path
            if not route.routes:
                errors.append(f"{application} {path}: opaque or empty mount requires review")
                continue
            children, problems = _walk_routes(
                application,
                route.routes,
                authenticators,
                prefix=path,
                ancestors=ancestors | {id(route)},
            )
            endpoints.extend(children)
            errors.extend(problems)
        elif isinstance(route, Route | WebSocketRoute):
            path = prefix + route.path
            methods = {"WEBSOCKET"} if isinstance(route, WebSocketRoute) else route.methods
            if not methods:
                errors.append(f"{application} {path}: unbounded HTTP methods require review")
                continue
            endpoints.extend(
                _registered_endpoint((application, method, path), route, authenticators)
                for method in sorted(methods)
            )
        else:
            errors.append(f"{application} {prefix}: unsupported route type {type(route).__name__}")
    return endpoints, errors


def registered_endpoints(
    applications: Mapping[str, Starlette], authenticators: Mapping[str, object]
) -> tuple[list[RegisteredEndpoint], list[str]]:
    endpoints: list[RegisteredEndpoint] = []
    errors: list[str] = []
    if not applications:
        errors.append("no applications inspected")
    for application, app in sorted(applications.items()):
        rows, problems = _walk_routes(application, app.routes, authenticators)
        if not rows:
            errors.append(f"{application}: no registered endpoints inspected")
        endpoints.extend(rows)
        errors.extend(problems)
    return sorted(endpoints, key=lambda row: row.key), sorted(errors)


def _declaration_errors(
    actual: RegisteredEndpoint, expected: EndpointDeclaration, rule: RuleDeclaration
) -> list[str]:
    errors: list[str] = []
    label = _label(actual.key)
    if actual.endpoint != expected.endpoint:
        errors.append(f"{label}: endpoint changed from {expected.endpoint} to {actual.endpoint}")
    if actual.dependencies != tuple(sorted(rule.dependencies)):
        errors.append(
            f"{label}: dependency declaration drift; "
            f"expected {sorted(rule.dependencies)}, observed {list(actual.dependencies)}"
        )
    if rule.kind == "dependency":
        if rule.authentication is None or rule.authentication not in actual.authentication:
            errors.append(
                f"{label}: missing declared authentication boundary {rule.authentication}"
            )
    elif rule.authentication is not None or actual.authentication:
        errors.append(f"{label}: non-dependency declaration cannot conceal authentication wiring")
    return errors


def coverage_errors(
    applications: Mapping[str, Starlette],
    catalog: AuthorizationCatalog,
    authenticators: Mapping[str, object],
    public_reviews: Mapping[RouteKey, PublicRouteReview],
) -> list[str]:
    """Default-deny declaration coverage, with exact keys and no path matching."""
    observed, errors = registered_endpoints(applications, authenticators)
    actual: dict[RouteKey, RegisteredEndpoint] = {}
    for row in observed:
        if row.key in actual:
            errors.append(f"{_label(row.key)}: duplicate registered endpoint")
        else:
            actual[row.key] = row

    declared: set[RouteKey] = set()
    declared_public: set[RouteKey] = set()
    rule_names: set[str] = set()
    for rule in catalog.rules:
        if rule.name in rule_names:
            errors.append(f"duplicate rule name {rule.name}")
        rule_names.add(rule.name)
        if len(rule.dependencies) != len(set(rule.dependencies)):
            errors.append(f"{rule.name}: duplicate dependency declaration")
        for expected in rule.routes:
            key = expected.key
            if rule.kind == "public":
                declared_public.add(key)
                review = public_reviews.get(key)
                if (
                    review is None
                    or review.endpoint != expected.endpoint
                    or not review.basis.strip()
                    or not review.reference.strip()
                ):
                    errors.append(f"{_label(key)}: missing exact reviewed public declaration")
            if key in declared:
                errors.append(f"{_label(key)}: duplicate authorization declaration")
            declared.add(key)
            if key not in actual:
                errors.append(f"{_label(key)}: stale authorization declaration")
                continue
            errors.extend(_declaration_errors(actual[key], expected, rule))
    errors.extend(f"{_label(key)}: unclassified endpoint" for key in actual.keys() - declared)
    errors.extend(
        f"{_label(key)}: unused reviewed public declaration"
        for key in public_reviews.keys() - declared_public
    )
    return sorted(errors)
