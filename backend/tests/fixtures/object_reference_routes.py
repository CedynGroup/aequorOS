"""Shared route census and request generation for the object-reference tests.

The deterministic coverage test and the generative property both need the same
three things and must agree on them exactly: which routes carry an object
identifier beside ``{bank_id}``, how to build a request that references a
foreign tenant's object, and which foreign references belong to a documented
product defect.  Keeping that here means a new object-reference route joins both
tests at once. The single-foreign-child layout holds parent references at home
while substituting one sibling-owned child, including same-org cross-parent nesting.

Route discovery walks the FastAPI registry: a path parameter is an object
reference when its name ends in ``_id`` and is not ``bank_id``; a body or query
field is one when :func:`object_references.reference_field_kind` maps it to a
seeded kind.  Catalogue path/query parameters (``module``, ``signing_role``, …)
take fixed valid values so the request reaches the object lookup.

Both suites read the census at collection, so an uncatalogued identifier
errors them before a single case runs; ``tests/architecture/
test_object_reference_census.py`` runs :func:`uncatalogued_references` on
every PR without Postgres and names the identifiers to catalogue.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final, Literal, cast, get_args, get_origin
from uuid import UUID, uuid5

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from app.core.authorization import InstitutionScope, ModuleScope, RoleBundle, SensitivityScope
from app.models import AuthorizationBinding
from tests.fixtures.object_references import (
    AS_OF,
    KINDS_BY_NAME,
    NON_REFERENCE_FIELDS,
    ObjectSet,
    TenantSeed,
    path_parameter_kind,
    reference_field_kind,
)

_USER_NAMESPACE = UUID("8f0c6f1e-3b7a-4c52-9d1e-2a6f4b8c9d01")
ORG_A: Final = "OR-IDORPR01"
ORG_B: Final = "OR-IDORPR02"
GRANT_REASON: Final = "object reference property binding"

TENANT_A: Final = TenantSeed(
    organization_id=ORG_A,
    bank_id="BK-IDORPR01",
    user_ids=tuple(uuid5(_USER_NAMESPACE, f"{ORG_A}:{index}") for index in range(3)),
    marker="IDOR-TENANT-A-MARKER",
)
#: Sibling bank in organization A; its principals are A's principals rotated so
#: A's own actor is not A2's actor.
TENANT_A2: Final = TenantSeed(
    organization_id=ORG_A,
    bank_id="BK-IDORPR03",
    user_ids=(TENANT_A.user_ids[1], TENANT_A.user_ids[2], TENANT_A.user_ids[0]),
    marker="IDOR-SIBLING-BANK-MARKER",
)
TENANT_B: Final = TenantSeed(
    organization_id=ORG_B,
    bank_id="BK-IDORPR02",
    user_ids=tuple(uuid5(_USER_NAMESPACE, f"{ORG_B}:{index}") for index in range(3)),
    marker="IDOR-TENANT-B-MARKER",
)

Layout = Literal["cross_organization", "sibling_bank", "single_foreign_child"]
LAYOUTS: Final[tuple[Layout, ...]] = (
    "cross_organization",
    "sibling_bank",
    "single_foreign_child",
)

_MUTATION_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CATALOGUE_VALUES: Final[Mapping[str, str]] = {
    "channel": "email",
    "section_key": "executive_summary",
    "item_id": "050a",
    "seq": "1",
    "version_no": "1",
    "curve_name": "AEQ.GHS.OIS",
    "model": "nmd-duration",
    "module": "liquidity",
    "signing_role": "preparer",
}
#: Generated bodies a schema alone cannot make valid, merged before object
#: references are assigned.
_BODY_OVERRIDES: Final[Mapping[tuple[str, str], Mapping[str, Any]]] = {
    ("POST", "/api/v1/assessments"): {"assessment_type": "vendor_risk"},
    ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses"): {
        "scenarios": [{"kind": "custom"}]
    },
    ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis"): {
        "scenarios": [{"kind": "custom"}]
    },
    ("PATCH", "/api/v1/cases/{case_id}/financial-workspace/institutions/{institution_id}"): {
        "name": "probe"
    },
    (
        "PATCH",
        "/api/v1/cases/{case_id}/financial-workspace/reporting-periods/{reporting_period_id}",
    ): {"label": "probe"},
    ("PATCH", "/api/v1/cases/{case_id}/scenarios/{scenario_id}"): {"name": "probe"},
    ("PATCH", "/api/v1/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}"): {
        "label": "probe"
    },
}
#: ICAAP deferred (captain, 2026-09-20); catalogue before ICAAP ships.  Every
#: ICAAP route that carries an object identifier is named here one by one — no
#: prefix wildcard — so picking the workspace back up means deleting each entry
#: and seeding its kind, not discovering after merge that the census never saw
#: it.  The routes without an identifier (list, create) are not object-reference
#: routes and are absent from the census by construction.
ICAAP_DEFERRED: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}"),
        ("PATCH", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/allocation"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/allocation"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/appetite"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/appetite/metrics"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/appetite/metrics/{metric_id}"),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/appetite/metrics/{metric_id}/retire",
        ),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/archive"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments"),
        (
            "GET",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments/{attachment_id}/download",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments/{attachment_id}/withdraw",
        ),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/audit-reviews"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/audit-reviews"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/audit-reviews/{review_id}"),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/audit-reviews/{review_id}/finalise",
        ),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}/bindings"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}/manual-table"),
        ("DELETE", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}/pin"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}/pin"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}/refresh"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks/{block_id}/retire"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/capital-triggers"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/challenges"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/challenges"),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/challenges/{challenge_id}/responses",
        ),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/clone"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/disclosure"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/disclosure"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/disclosure/decision"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/disclosure/submit"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/draft.docx"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/draft.pdf"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/filing"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/freeze"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/freeze-preflight"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/parameters"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/capital-plan-proposal"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/items"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/items/{item_id}"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/items/{item_id}/approve"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/items/{item_id}/compute"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/items/{item_id}/retire"),
        (
            "GET",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/items/{item_id}/revisions",
        ),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/pillar2/table5"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/readiness"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/rebase"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation"),
        (
            "PUT",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/controls/{control_code}/explanations/{comparison_key}",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/requirement/compute",
        ),
        (
            "PUT",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/requirement/lines/{line_key}/explanation",
        ),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/resources/lines"),
        (
            "DELETE",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/resources/lines/{line_id}",
        ),
        (
            "PUT",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/resources/lines/{line_id}",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/reconciliation/resources/load-regulatory",
        ),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/return"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/risks"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/risks"),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/risks/{risk_key}"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/risks/{risk_key}/retire"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/ai-drafts"),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/ai-drafts",
        ),
        (
            "GET",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/ai-drafts/{suggestion_id}",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/ai-drafts/{suggestion_id}/accept",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/ai-drafts/{suggestion_id}/reject",
        ),
        (
            "PUT",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/requirements/{item_id}",
        ),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/versions"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/versions"),
        (
            "GET",
            "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/versions/{version_no}",
        ),
        ("PUT", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/working"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/stages"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/stages/{seq}/decisions"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/cycles/{cycle_id}/submit-for-review"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/supervisory-addons/{addon_id}/confirm"),
        ("GET", "/api/v1/banks/{bank_id}/icaap/supervisory-addons/{addon_id}/letter"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/supervisory-addons/{addon_id}/withdraw"),
        ("PATCH", "/api/v1/banks/{bank_id}/icaap/workflow-templates/{template_id}"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/workflow-templates/{template_id}/decision"),
        ("POST", "/api/v1/banks/{bank_id}/icaap/workflow-templates/{template_id}/submit"),
    }
)
#: Routes the enumeration finds that the sweep cannot exercise automatically.
KNOWN_UNCOVERED: Final[frozenset[tuple[str, str]]] = ICAAP_DEFERRED | frozenset(
    {
        ("POST", "/api/v1/cases/{case_id}/financial-workspace/{unsupported_entity_type}"),
        (
            "PATCH",
            "/api/v1/cases/{case_id}/financial-workspace/{unsupported_entity_type}/{entity_id}",
        ),
        ("POST", "/api/v1/banks/{bank_id}/ingestion-batches"),
        ("POST", "/api/v1/banks/{bank_id}/push-batches/{push_batch_id}/records"),
        ("POST", "/api/v1/banks/{bank_id}/push-batches/{push_batch_id}/commit"),
        ("GET", "/api/v1/banks/{bank_id}/push-batches/{push_batch_id}"),
        # References a completed ``document_extraction`` — a multi-step fixture
        # (upload, parse, extract) the sweep does not stand up.
        ("POST", "/api/v1/cases/{case_id}/financial-workspace/map"),
    }
)
#: Confirmed product defects the tests reproduce but that are not yet fixed;
#: kept out of the strict assertions and pinned as still-defective so a fix
#: forces their promotion.  Empty means every route in the census is under the
#: strict tests.
KNOWN_DEFECTS: Final[frozenset[tuple[str, str, Layout]]] = frozenset()


@dataclass(frozen=True)
class Reference:
    """One object identifier a route accepts and the kind it must resolve."""

    location: Literal["path", "query", "body"]
    name: str
    kind: str


@dataclass(frozen=True)
class ObjectRoute:
    method: str
    path: str
    route: APIRoute
    references: tuple[Reference, ...]
    body_model: type[BaseModel] | None

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def mutation(self) -> bool:
        return self.method in _MUTATION_METHODS

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


@dataclass(frozen=True)
class Request:
    path: str
    query: dict[str, str]
    body: dict[str, Any] | None


# --- route census -----------------------------------------------------------


def _field_types(annotation: Any) -> list[tuple[Any, bool]]:
    """Concrete types behind an annotation, flagged when reached through a list."""
    types: list[tuple[Any, bool]] = []
    pending: list[tuple[Any, bool]] = [(annotation, False)]
    while pending:
        candidate, in_list = pending.pop()
        origin = get_origin(candidate)
        if origin is list:
            pending.extend((item, True) for item in get_args(candidate))
        elif origin is not None and get_args(candidate) and origin is not Literal:
            pending.extend((item, in_list) for item in get_args(candidate))
        else:
            types.append((candidate, in_list))
    return types


def _model_reference_fields(
    model: type[BaseModel], path: str, prefix: str, seen: set[type[BaseModel]]
) -> Iterator[Reference]:
    if model in seen:
        return
    seen.add(model)
    for name, model_field in model.model_fields.items():
        types = _field_types(model_field.annotation)
        for candidate, in_list in types:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                nested = f"{prefix}{name}[]." if in_list else f"{prefix}{name}."
                yield from _model_reference_fields(candidate, path, nested, seen)
        concrete = {candidate for candidate, _ in types}
        if concrete == {str} and name == "bank_id" and prefix == "":
            yield Reference("body", name, "bank")
        elif UUID in concrete and name.endswith("_id") and name != "bank_id":
            kind = reference_field_kind(path, name)
            if kind is not None:
                yield Reference("body", f"{prefix}{name}", kind)
            elif name not in NON_REFERENCE_FIELDS:
                yield Reference("body", f"{prefix}{name}", f"unknown:{name}")


def _route_references(route: APIRoute) -> tuple[list[Reference], type[BaseModel] | None]:
    references: list[Reference] = []
    for parameter in route.dependant.path_params:
        name = parameter.name
        if name == "bank_id" or not name.endswith("_id"):
            continue
        # Framework checklist keys are catalogue strings, not tenant object IDs.
        if name == "item_id" and route.path.endswith(
            "/sections/{section_key}/requirements/{item_id}"
        ):
            continue
        kind = path_parameter_kind(route.path, name)
        references.append(Reference("path", name, kind or f"unknown:{name}"))
    for parameter in route.dependant.query_params:
        name = parameter.name
        if name == "bank_id" or not name.endswith("_id"):
            continue
        kind = reference_field_kind(route.path, name)
        if kind is not None:
            references.append(Reference("query", name, kind))
        elif name not in NON_REFERENCE_FIELDS:
            references.append(Reference("query", name, f"unknown:{name}"))
    body_model: type[BaseModel] | None = None
    for parameter in route.dependant.body_params:
        annotation = parameter.field_info.annotation
        for candidate in (annotation, *get_args(annotation)):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                body_model = candidate
                references.extend(_model_reference_fields(candidate, route.path, "", set()))
    return references, body_model


def _census(app: FastAPI) -> list[ObjectRoute]:
    """Every ``/api/v1`` route carrying an object identifier beside ``bank_id``,
    catalogued or not, minus :data:`KNOWN_UNCOVERED`."""
    routes: list[ObjectRoute] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/"):
            continue
        references, body_model = _route_references(route)
        if not references:
            continue
        for method in sorted(route.methods):
            if (method, route.path) in KNOWN_UNCOVERED:
                continue
            routes.append(
                ObjectRoute(
                    method=method,
                    path=route.path,
                    route=route,
                    references=tuple(references),
                    body_model=body_model,
                )
            )
    return sorted(routes, key=lambda route: (route.path, route.method))


def _uncatalogued(routes: list[ObjectRoute]) -> list[str]:
    return sorted(
        f"{route.label}: {reference.location} {reference.name}"
        for route in routes
        for reference in route.references
        if reference.kind.startswith("unknown:")
    )


def uncatalogued_references(app: FastAPI) -> list[str]:
    """Object identifiers the census finds with no catalogue entry.

    Each entry reads ``"METHOD /path: location name"``.  A new route lands here
    until its identifier has an :class:`~tests.fixtures.object_references.ObjectKind`
    (path) or a ``REFERENCE_FIELDS`` row (body/query), or the route is listed in
    :data:`KNOWN_UNCOVERED` with its reason.
    """
    return _uncatalogued(_census(app))


def object_routes(app: FastAPI) -> list[ObjectRoute]:
    """The exercisable census: every route with all of its identifiers catalogued."""
    routes = _census(app)
    assert routes, "the FastAPI object-reference route census is empty"
    unresolved = _uncatalogued(routes)
    assert not unresolved, f"object identifiers without a catalogue entry: {unresolved}"
    return routes


# --- request generation -----------------------------------------------------


def _schema_value(schema: Mapping[str, Any], document: Mapping[str, Any]) -> Any:  # noqa: PLR0911, PLR0912
    while "$ref" in schema:
        target: Any = document
        for part in cast(str, schema["$ref"]).removeprefix("#/").split("/"):
            target = target[part]
        schema = cast(Mapping[str, Any], target)
    if "const" in schema:
        return schema["const"]
    if enum := schema.get("enum"):
        return enum[0]
    for union_key in ("oneOf", "anyOf"):
        if options := schema.get(union_key):
            non_null = next(
                (option for option in options if option.get("type") != "null"), options[0]
            )
            return _schema_value(non_null, document)
    if all_of := schema.get("allOf"):
        merged: dict[str, Any] = {}
        for option in all_of:
            value = _schema_value(option, document)
            if isinstance(value, dict):
                merged.update(value)
        return merged
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = cast(Mapping[str, Mapping[str, Any]], schema.get("properties", {}))
        value = {
            name: _schema_value(properties[name], document)
            for name in cast(list[str], schema.get("required", []))
        }
        if int(schema.get("minProperties", 0)) > len(value):
            additional = schema.get("additionalProperties", {})
            value["probe"] = _schema_value(
                additional if isinstance(additional, dict) else {}, document
            )
        return value
    if schema_type == "array":
        count = max(1, int(schema.get("minItems", 0)))
        return [_schema_value(cast(Mapping[str, Any], schema.get("items", {})), document)] * count
    if schema_type == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        return int(schema.get("exclusiveMinimum", 0)) + 1
    if schema_type == "number":
        if "minimum" in schema:
            return float(schema["minimum"])
        return float(schema.get("exclusiveMinimum", 0)) + 1
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None
    format_examples = {
        "date": AS_OF.isoformat(),
        "date-time": datetime(2026, 9, 18, tzinfo=UTC).isoformat(),
        "email": "object-reference@example.test",
        "uri": "https://example.test",
        "uuid": "11111111-2222-4333-8444-555555555555",
    }
    format_name = schema.get("format")
    value = format_examples.get(format_name, "probe") if isinstance(format_name, str) else "probe"
    value = value.ljust(int(schema.get("minLength", 0)), "x")
    if "maxLength" in schema:
        value = value[: int(schema["maxLength"])]
    return value


def _assign(
    body: Any, field_path: str, value: str, schema: Mapping[str, Any], document: Any
) -> None:
    """Set ``field_path`` (``a.b[].c``) inside a generated body, creating omitted parts."""
    head, _, rest = field_path.partition(".")
    while "$ref" in schema:
        target: Any = document
        for part in cast(str, schema["$ref"]).removeprefix("#/").split("/"):
            target = target[part]
        schema = cast(Mapping[str, Any], target)
    properties = cast(Mapping[str, Mapping[str, Any]], schema.get("properties", {}))
    name = head.removesuffix("[]")
    if not rest:
        body[name] = value
        return
    child_schema = properties[name]
    if head.endswith("[]"):
        items = body.setdefault(name, [_schema_value(child_schema, document)[0]])
        item_schema = cast(Mapping[str, Any], child_schema.get("items", {}))
        for item in items:
            _assign(item, rest, value, item_schema, document)
        return
    child = body.setdefault(name, _schema_value(child_schema, document))
    _assign(child, rest, value, child_schema, document)


def _body_schema(route: ObjectRoute, document: Mapping[str, Any]) -> Mapping[str, Any]:
    operation = document["paths"][route.path][route.method.lower()]
    return operation["requestBody"]["content"]["application/json"]["schema"]


def _catalogue_value(annotation: Any, name: str) -> str:
    if get_origin(annotation) is Literal:
        return str(get_args(annotation)[0])
    if name in _CATALOGUE_VALUES:
        return _CATALOGUE_VALUES[name]
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return str(next(iter(annotation)).value)
    if annotation is UUID:
        return "11111111-2222-4333-8444-555555555555"
    return AS_OF.isoformat() if "date" in name else "probe"


def build_request(
    route: ObjectRoute,
    document: Mapping[str, Any],
    bank_id: str,
    resolve: Callable[[Reference], str],
) -> Request:
    """Build a request for ``route`` whose ``bank_id`` is ``bank_id`` and whose
    object references are resolved by ``resolve``."""
    path_values: dict[str, str] = {"bank_id": bank_id}
    references = {reference.name: reference for reference in route.references}
    for parameter in route.route.dependant.path_params:
        if parameter.name in path_values:
            continue
        if parameter.name in references:
            path_values[parameter.name] = resolve(references[parameter.name])
        else:
            path_values[parameter.name] = _catalogue_value(
                parameter.field_info.annotation, parameter.name
            )
    query: dict[str, str] = {}
    for parameter in route.route.dependant.query_params:
        if parameter.name == "bank_id":
            query[parameter.name] = bank_id
        elif parameter.name in references:
            query[parameter.name] = resolve(references[parameter.name])
        elif parameter.default is PydanticUndefined:
            query[parameter.name] = _catalogue_value(
                parameter.field_info.annotation, parameter.name
            )
    body: dict[str, Any] | None = None
    if route.body_model is not None:
        schema = _body_schema(route, document)
        body = cast(dict[str, Any], _schema_value(schema, document))
        body.update(_BODY_OVERRIDES.get(route.key, {}))
        for reference in route.references:
            if reference.location == "body":
                _assign(body, reference.name, resolve(reference), schema, document)
    return Request(path=route.path.format(**path_values), query=query, body=body)


def foreign_children(route: ObjectRoute) -> tuple[Reference, ...]:
    """References nested beneath a home parent that can belong to a sibling object set."""
    parents = [reference for reference in route.references if reference.location == "path"]
    if len(route.references) < 2:
        return ()
    return tuple(
        reference
        for reference in route.references
        if (not parents or reference != parents[0])
        and (
            reference.location == "path"
            or KINDS_BY_NAME[reference.kind].bank_scoped
            or route.path.startswith("/api/v1/cases/{case_id}/")
        )
    )


def layout_children(route: ObjectRoute, layout: Layout) -> tuple[Reference | None, ...]:
    """Enumerate each isolated child case, or the original all-foreign case."""
    if layout == "single_foreign_child":
        return foreign_children(route)
    return (None,)


def foreign_references(
    route: ObjectRoute, layout: Layout, child: Reference | None = None
) -> set[Reference]:
    """Which of ``route``'s references belong to the foreign tenant for ``layout``.

    Cross-organization makes every reference foreign; a sibling bank in the same
    organization makes only the *bank-scoped* references foreign, because an
    organization-scoped object (a case, a macro scenario, a binding) is
    legitimately shared across the organization's banks.
    """
    if layout == "single_foreign_child":
        assert child in foreign_children(route), "select one eligible foreign child"
        assert child is not None
        return {child}
    assert child is None
    if layout == "cross_organization":
        return set(route.references)
    return {
        reference for reference in route.references if KINDS_BY_NAME[reference.kind].bank_scoped
    }


def foreign_request(  # noqa: PLR0913 - explicit home, foreign owner and isolated child
    route: ObjectRoute,
    document: Mapping[str, Any],
    layout: Layout,
    *,
    home: ObjectSet,
    owner: ObjectSet,
    child: Reference | None = None,
) -> tuple[Request, set[str]] | None:
    """A request under bank A that references ``layout``'s foreign objects.

    Returns the request and the set of foreign identifiers it names (for the
    no-leak check), or ``None`` when the layout has no foreign reference for this
    route (an organization-scoped route under the sibling-bank layout).
    """
    foreign = foreign_references(route, layout, child)
    if not foreign:
        return None

    def resolve(reference: Reference) -> str:
        return (owner if reference in foreign else home)[reference.kind]

    request = build_request(route, document, TENANT_A.bank_id, resolve)
    requested = {resolve(reference) for reference in route.references}
    return request, requested


def leaked(response_text: str, identifiers: set[str], requested: set[str]) -> list[str]:
    """Foreign identifiers that appear in a response body but were not the ones sent."""
    return sorted(
        identifier for identifier in identifiers - requested if identifier in response_text
    )


def binding(  # noqa: PLR0913 - the complete binding sentence is explicit
    *,
    tenant: TenantSeed,
    user_id: UUID,
    role: RoleBundle,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
    institution_scope: InstitutionScope,
    binding_id: UUID | None = None,
) -> AuthorizationBinding:
    row = AuthorizationBinding(
        organization_id=tenant.organization_id,
        principal_user_id=user_id,
        principal_type="human",
        role_bundle=role.value,
        institution_scope=institution_scope.value,
        institution_id=None
        if institution_scope is InstitutionScope.ORGANIZATION
        else tenant.bank_id,
        module_scope=module_scope.value,
        sensitivity_scope=sensitivity_scope.value,
        granted_by_type="system",
        granted_by_id="object-reference-property",
        grant_reason=GRANT_REASON,
        status="active",
    )
    if binding_id is not None:
        row.id = binding_id
    return row


_FULL_AUTHORITY_BUNDLES: Final = (
    RoleBundle.VIEWER,
    RoleBundle.AUDITOR,
    RoleBundle.ANALYST,
    RoleBundle.APPROVER,
)


def full_authority_bindings(tenant: TenantSeed, *, owner: bool) -> list[AuthorizationBinding]:
    """Every operational bundle on exactly one bank, plus Org Owner when ``owner``."""
    bindings: list[AuthorizationBinding] = []
    if owner:
        bindings.append(
            binding(
                tenant=tenant,
                user_id=tenant.actor_id,
                role=RoleBundle.ORG_OWNER,
                module_scope=ModuleScope.ACCOUNT,
                sensitivity_scope=SensitivityScope.ALL,
                institution_scope=InstitutionScope.ORGANIZATION,
            )
        )
    for role in _FULL_AUTHORITY_BUNDLES:
        bindings.append(
            binding(
                tenant=tenant,
                user_id=tenant.actor_id,
                role=role,
                module_scope=ModuleScope.ALL,
                sensitivity_scope=SensitivityScope.ALL,
                institution_scope=InstitutionScope.INSTITUTION,
            )
        )
    return bindings
