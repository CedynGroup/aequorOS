"""Property proof that object references are tenant- and bank-bound (IDOR).

A route that names a second object beside ``{bank_id}`` — ``package_id`` in
the path, ``reporting_period_id`` in a body, ``scenario_id`` in a query — must
refuse an object that belongs to another organization or to a sibling bank of
the same organization, and must refuse it without side effects.  The route set
comes from the FastAPI registry: every route whose path carries an identifier
parameter other than ``bank_id``, plus every route whose body or query schema
carries a UUID-typed ``*_id`` field.  Detection walks ``route.dependant`` path,
query and body parameters and the Pydantic body models recursively; a path
parameter is an object reference when its name ends in ``_id`` and is not
``bank_id``, and a body or query field is one when its name is listed in
``tests.fixtures.object_references.REFERENCE_FIELDS``.  Actor-label body fields
(``assigned_to_user_id``, ``approved_by_user_id``, recipient user ids) are not
object references and are excluded there.  Catalogue path/query parameters
(``module``, ``model``, ``channel``, ``curve_name``, ``kind``, ``signing_role``)
take fixed valid values.

Two layouts reference a foreign object from bank A of organization A:

* ``cross_organization`` — every referenced object belongs to organization B;
* ``sibling_bank`` — every *bank-scoped* referenced object belongs to bank A2 of
  organization A while the caller holds bindings on bank A only.  An
  organization-scoped object (a case, a macro scenario, a binding) is
  legitimately shared across A's banks, so it is foreign only across
  organizations.

For each applicable route/layout the property asserts that a read discloses no
foreign identifier (a 200 that hides the row is a valid refusal) and that a
mutation never returns 2xx and leaves the row counts of every table in both
organizations byte-identical.  The positive control is proven at the data layer:
every seeded object is visible to the tenant that owns it under RLS, so a foreign
reference's refusal is an authorization decision, not a missing fixture.

Known product defects the property flags are quarantined in ``_KNOWN_DEFECTS``
(kept out of the strict assertion and pinned as still-defective so a fix forces
their promotion); see the PR description for the confirmed routes.

Routes the enumeration finds but this module cannot exercise automatically:

* ``POST/PATCH /api/v1/cases/{case_id}/financial-workspace/{unsupported_entity_type}``
  refuse every entity type before any lookup, so no object can be referenced;
* ``POST /api/v1/banks/{bank_id}/ingestion-batches`` carries ``mapping_config_id``
  in a multipart upload whose commit needs a translated workbook;
* push batches (``push_batch_id``) live in object storage rather than a table and
  authenticate with integration keys, not the human bindings this sweep issues.

Same-organization cross-*parent* nesting (for example a ``{scenario_id}`` from a
different case under one org) is not covered here — it needs a second parent
fixture — and remains for a follow-up.

Follows ``test_authorization_tenant_isolation_properties.py``: a migrated
Postgres schema with FORCE RLS, ``app.organization_id`` set in the same
transaction as every tenant write, and generated binding rows replaced rather
than users or organizations deleted.  The schema is dropped without the
downgrade round trip because issued integration keys and ``enterprise_stress``
runs are rows the older schemas refuse to carry.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final, Literal, cast, get_args, get_origin
from uuid import UUID, uuid5

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.core.security import ROLES
from app.db.base import Base
from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.s3 import get_object_storage
from app.main import create_app
from app.models import AuthorizationBinding, Organization, User
from app.services.regulatory_reporting import common as regulatory_common
from tests.api.helpers import headers
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    clear_database_caches,
    forward_migrated_postgres_schema,
)
from tests.fixtures.object_references import (
    AS_OF,
    KINDS_BY_NAME,
    MODEL_BY_KIND,
    NON_REFERENCE_FIELDS,
    OBJECT_KINDS,
    ObjectSet,
    TenantSeed,
    bank_row,
    path_parameter_kind,
    reference_field_kind,
    seed_objects,
)

_MODEL_BY_KIND = MODEL_BY_KIND
_NON_REFERENCE_FIELDS = NON_REFERENCE_FIELDS

__all__ = ["forward_migrated_postgres_schema"]

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres object-reference authorization properties.",
)

_USER_NAMESPACE = UUID("8f0c6f1e-3b7a-4c52-9d1e-2a6f4b8c9d01")
_BINDING_NAMESPACE = UUID("1c9d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f")
_ORG_A = "OR-IDORPR01"
_ORG_B = "OR-IDORPR02"
_TENANT_A = TenantSeed(
    organization_id=_ORG_A,
    bank_id="BK-IDORPR01",
    user_ids=tuple(uuid5(_USER_NAMESPACE, f"{_ORG_A}:{index}") for index in range(3)),
    marker="IDOR-TENANT-A-MARKER",
)
_TENANT_A2 = TenantSeed(
    organization_id=_ORG_A,
    bank_id="BK-IDORPR03",
    user_ids=(_TENANT_A.user_ids[1], _TENANT_A.user_ids[2], _TENANT_A.user_ids[0]),
    marker="IDOR-SIBLING-BANK-MARKER",
)
_TENANT_B = TenantSeed(
    organization_id=_ORG_B,
    bank_id="BK-IDORPR02",
    user_ids=tuple(uuid5(_USER_NAMESPACE, f"{_ORG_B}:{index}") for index in range(3)),
    marker="IDOR-TENANT-B-MARKER",
)
_GRANT_REASON = "object reference property binding"
_FULL_AUTHORITY_ROLES = ("admin",)
_FULL_AUTHORITY_BUNDLES = (
    RoleBundle.VIEWER,
    RoleBundle.AUDITOR,
    RoleBundle.ANALYST,
    RoleBundle.APPROVER,
)
_GENERATED_BUNDLES = (*_FULL_AUTHORITY_BUNDLES, RoleBundle.ACCOUNT_ADMIN)
_BINDING_SPEC = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.sampled_from(_GENERATED_BUNDLES),
    st.sampled_from(tuple(ModuleScope)),
    st.sampled_from(tuple(SensitivityScope)),
)
_CALLER_SPEC = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.lists(st.sampled_from(ROLES), max_size=2),
    st.lists(_BINDING_SPEC, max_size=6),
)
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CATALOGUE_VALUES: Final[Mapping[str, str]] = {
    "channel": "email",
    "curve_name": "AEQ.GHS.OIS",
    "model": "nmd-duration",
    "module": "liquidity",
    "signing_role": "preparer",
}
#: Generated bodies that a schema alone cannot make valid, merged before
#: object references are assigned.
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
#: Routes the enumeration finds that the sweep cannot exercise (module docstring).
_KNOWN_UNCOVERED: Final = frozenset(
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
        # (upload, parse, extract) this sweep does not stand up.
        ("POST", "/api/v1/cases/{case_id}/financial-workspace/map"),
    }
)
Layout = Literal["cross_organization", "sibling_bank"]
_LAYOUTS: Final[tuple[Layout, ...]] = ("cross_organization", "sibling_bank")
#: Confirmed product defects this property flags but PR #208's shared guard does
#: not yet cover; kept out of the strict assertion and pinned as still-defective
#: so a fix forces their promotion.  See the module docstring and PR description.
_KNOWN_DEFECTS: Final[frozenset[tuple[str, str, Layout]]] = frozenset(
    {
        (
            "POST",
            "/api/v1/banks/{bank_id}/system-of-record/{declaration_id}/approve",
            "sibling_bank",
        ),
        (
            "POST",
            "/api/v1/banks/{bank_id}/system-of-record/{declaration_id}/revoke",
            "sibling_bank",
        ),
    }
)


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
    bank_query: bool

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def mutation(self) -> bool:
        return self.method in _MUTATION_METHODS

    @property
    def bank_bound(self) -> bool:
        return "{bank_id}" in self.path or self.bank_query


@dataclass(frozen=True)
class Request:
    path: str
    query: dict[str, str]
    body: Any | None


@dataclass
class Tenants:
    a: ObjectSet
    a2: ObjectSet
    b: ObjectSet

    def owner(self, layout: Layout) -> ObjectSet:
        return self.b if layout == "cross_organization" else self.a2


# --- tenant seeding ---------------------------------------------------------


def _tenant_session(engine: Engine, organization_id: str) -> Session:
    session = Session(engine, expire_on_commit=False)
    session.info["organization_id"] = organization_id
    return session


def _binding_id(organization_id: str, index: int, suffix: str) -> UUID:
    return uuid5(_BINDING_NAMESPACE, f"{organization_id}:{index}:{suffix}")


def _binding(  # noqa: PLR0913 - the complete binding sentence is explicit
    *,
    binding_id: UUID,
    tenant: TenantSeed,
    user_id: UUID,
    role: RoleBundle,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
    institution_scope: InstitutionScope,
) -> AuthorizationBinding:
    return AuthorizationBinding(
        id=binding_id,
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
        grant_reason=_GRANT_REASON,
        status="active",
    )


def _seed_organization(engine: Engine, tenant: TenantSeed) -> None:
    with _tenant_session(engine, tenant.organization_id) as session:
        session.add(Organization(id=tenant.organization_id, name=tenant.marker))
        session.add(bank_row(tenant))
        for index, user_id in enumerate(tenant.user_ids):
            session.add(
                User(
                    id=user_id,
                    organization_id=tenant.organization_id,
                    email=f"idor-{index}@{tenant.organization_id.lower()}.example",
                    display_name=f"IDOR principal {tenant.organization_id} {index}",
                    role="viewer",
                    auth_provider="password",
                )
            )
        session.commit()


def _seed_sibling_bank(engine: Engine, tenant: TenantSeed) -> None:
    with _tenant_session(engine, tenant.organization_id) as session:
        session.add(bank_row(tenant))
        session.commit()


def _seed_tenant_objects(engine: Engine, tenant: TenantSeed) -> ObjectSet:
    with _tenant_session(engine, tenant.organization_id) as session:
        objects = seed_objects(session, tenant)
        session.commit()
    return objects


def _full_authority_bindings(tenant: TenantSeed, *, owner: bool) -> list[AuthorizationBinding]:
    """Every operational bundle on exactly one bank, plus Org Owner administration."""
    bindings: list[AuthorizationBinding] = []
    if owner:
        bindings.append(
            _binding(
                binding_id=_binding_id(tenant.bank_id, 0, "fixed"),
                tenant=tenant,
                user_id=tenant.actor_id,
                role=RoleBundle.ORG_OWNER,
                module_scope=ModuleScope.ACCOUNT,
                sensitivity_scope=SensitivityScope.ALL,
                institution_scope=InstitutionScope.ORGANIZATION,
            )
        )
    for index, role in enumerate(_FULL_AUTHORITY_BUNDLES, start=1):
        bindings.append(
            _binding(
                binding_id=_binding_id(tenant.bank_id, index, "fixed"),
                tenant=tenant,
                user_id=tenant.actor_id,
                role=role,
                module_scope=ModuleScope.ALL,
                sensitivity_scope=SensitivityScope.ALL,
                institution_scope=InstitutionScope.INSTITUTION,
            )
        )
    return bindings


def _replace_bindings(
    engine: Engine,
    organization_id: str,
    bindings: list[AuthorizationBinding],
) -> None:
    """Replace this module's caller bindings; seeded object rows and keys stay."""
    with _tenant_session(engine, organization_id) as session:
        session.execute(
            text(
                "DELETE FROM authorization_bindings"
                " WHERE organization_id = :organization_id AND grant_reason = :reason"
            ),
            {"organization_id": organization_id, "reason": _GRANT_REASON},
        )
        session.add_all(bindings)
        session.commit()


def _generated_bindings(
    tenant: TenantSeed,
    specs: list[tuple[int, RoleBundle, ModuleScope, SensitivityScope]],
) -> list[AuthorizationBinding]:
    bindings: list[AuthorizationBinding] = []
    for index, (user_slot, role, module, sensitivity) in enumerate(specs):
        account_admin = role is RoleBundle.ACCOUNT_ADMIN
        bindings.append(
            _binding(
                binding_id=_binding_id(tenant.organization_id, index, "generated"),
                tenant=tenant,
                user_id=tenant.user_ids[user_slot],
                role=role,
                module_scope=ModuleScope.ACCOUNT if account_admin else module,
                sensitivity_scope=SensitivityScope.ALL if account_admin else sensitivity,
                institution_scope=(
                    InstitutionScope.ORGANIZATION if account_admin else InstitutionScope.INSTITUTION
                ),
            )
        )
    return bindings


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
            elif name not in _NON_REFERENCE_FIELDS:
                yield Reference("body", f"{prefix}{name}", f"unknown:{name}")


def _route_references(route: APIRoute) -> tuple[list[Reference], type[BaseModel] | None]:
    references: list[Reference] = []
    for parameter in route.dependant.path_params:
        name = parameter.name
        if name == "bank_id" or not name.endswith("_id"):
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
        elif name not in _NON_REFERENCE_FIELDS:
            references.append(Reference("query", name, f"unknown:{name}"))
    body_model: type[BaseModel] | None = None
    for parameter in route.dependant.body_params:
        annotation = parameter.field_info.annotation
        for candidate in (annotation, *get_args(annotation)):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                body_model = candidate
                references.extend(_model_reference_fields(candidate, route.path, "", set()))
    return references, body_model


def _object_routes(app: FastAPI) -> list[ObjectRoute]:
    routes: list[ObjectRoute] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/"):
            continue
        references, body_model = _route_references(route)
        if not references:
            continue
        bank_query = any(parameter.name == "bank_id" for parameter in route.dependant.query_params)
        for method in sorted(route.methods):
            if (method, route.path) in _KNOWN_UNCOVERED:
                continue
            routes.append(
                ObjectRoute(
                    method=method,
                    path=route.path,
                    route=route,
                    references=tuple(references),
                    body_model=body_model,
                    bank_query=bank_query,
                )
            )
    assert routes, "the FastAPI object-reference route census is empty"
    unresolved = sorted(
        f"{route.label}: {reference.location} {reference.name}"
        for route in routes
        for reference in route.references
        if reference.kind.startswith("unknown:")
    )
    assert not unresolved, f"object identifiers without a catalogue entry: {unresolved}"
    return sorted(routes, key=lambda route: (route.path, route.method))


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


def _build_request(
    route: ObjectRoute,
    document: Mapping[str, Any],
    bank_id: str,
    resolve: Callable[[Reference], str],
) -> Request:
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
        body.update(_BODY_OVERRIDES.get((route.method, route.path), {}))
        for reference in route.references:
            if reference.location == "body":
                _assign(body, reference.name, resolve(reference), schema, document)
    return Request(path=route.path.format(**path_values), query=query, body=body)


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


def _foreign_resolver(
    route: ObjectRoute, layout: Layout, tenants: Tenants
) -> Callable[[Reference], str] | None:
    """Map each reference to the tenant whose object it should name, or None to skip."""
    owner = tenants.owner(layout)
    if layout == "cross_organization":
        # Every reference points at organization B; RLS and the guards must hide
        # each one from an organization-A caller.
        foreign = set(route.references)
    else:
        # Same organization, sibling bank A2: only bank-scoped objects have a
        # different owner.  An organization-scoped object (a case, a macro
        # scenario, a binding) is legitimately shared across A's banks, so it is
        # not foreign here and is covered by the cross-organization layout.
        foreign = {
            reference for reference in route.references if KINDS_BY_NAME[reference.kind].bank_scoped
        }
    if not foreign:
        return None
    return lambda reference: (owner if reference in foreign else tenants.a)[reference.kind]


# --- observation ------------------------------------------------------------


def _digest_statement() -> str:
    parts = [
        f"SELECT '{name}' AS name, md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"
        f" AS digest FROM {name} t"
        for name in sorted(Base.metadata.tables)
    ]
    return " UNION ALL ".join(parts)


def _snapshot(engine: Engine, statement: str) -> dict[tuple[str, str], str]:
    digests: dict[tuple[str, str], str] = {}
    for organization_id in (_ORG_A, _ORG_B):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": organization_id},
            )
            for name, digest in connection.execute(text(statement)):
                digests[organization_id, name] = digest
    return digests


def _changed_tables(
    before: Mapping[tuple[str, str], str], after: Mapping[tuple[str, str], str]
) -> list[str]:
    return sorted(
        f"{org}:{name}" for (org, name), digest in after.items() if before[org, name] != digest
    )


def _leaked(response_text: str, identifiers: set[str], requested: set[str]) -> list[str]:
    return sorted(
        identifier for identifier in identifiers - requested if identifier in response_text
    )


@dataclass(frozen=True)
class Outcome:
    """The classification of one foreign-reference request."""

    route: ObjectRoute
    layout: Layout
    problems: list[str]
    detail: str

    @property
    def key(self) -> tuple[str, str, Layout]:
        return (self.route.method, self.route.path, self.layout)

    def message(self, extra: list[str]) -> str:
        return (
            f"{self.route.label} [{self.layout}]: {'; '.join(self.problems + extra)} {self.detail}"
        )


@dataclass
class SweepResult:
    exercised: int = 0
    #: Routes that failed the strict property (not in the known-defect set).
    failures: list[str] = field(default_factory=list)
    #: Known-defect routes that are still defective, keyed for promotion checks.
    defective: set[tuple[str, str, Layout]] = field(default_factory=set)
    side_effects: list[str] = field(default_factory=list)

    def record(self, outcome: Outcome, changed: list[str]) -> None:
        problems = list(outcome.problems)
        if changed:
            problems.append(f"side effects in {changed}")
        if outcome.key in _KNOWN_DEFECTS:
            if problems:
                self.defective.add(outcome.key)
            return
        if problems:
            self.failures.append(outcome.message(changed))


@dataclass
class Sweep:
    """One app, its OpenAPI document, the seeded tenants and the table digests."""

    client: TestClient
    document: Mapping[str, Any]
    engine: Engine
    tenants: Tenants
    statement: str = field(default_factory=_digest_statement)

    def send(self, route: ObjectRoute, request: Request, auth: dict[str, str]) -> Any:
        return self.client.request(
            route.method,
            request.path,
            params=request.query,
            json=request.body,
            headers=auth,
        )

    def snapshot(self) -> dict[tuple[str, str], str]:
        return _snapshot(self.engine, self.statement)

    def refuse(self, route: ObjectRoute, layout: Layout, auth: dict[str, str]) -> Outcome | None:
        """Send one foreign reference as ``auth`` and classify the response.

        A read must not disclose any foreign identifier; a 200 that hides the row
        is a legitimate refusal.  A mutation must additionally decline to act:
        never a 2xx and (checked by the sweep) never a database change.
        """
        resolve = _foreign_resolver(route, layout, self.tenants)
        if resolve is None:
            return None
        request = _build_request(route, self.document, _TENANT_A.bank_id, resolve)
        requested = {resolve(reference) for reference in route.references}
        response = self.send(route, request, auth)
        leaked = _leaked(response.text, self.tenants.owner(layout).identifiers(), requested)
        problems: list[str] = []
        if leaked:
            problems.append(f"leaked {leaked}")
        if route.mutation:
            if response.status_code in (200, 201, 202, 204):
                problems.append(f"accepted with {response.status_code}")
        elif response.status_code not in (200, 403, 404):
            problems.append(f"unexpected read status {response.status_code}")
        return Outcome(route=route, layout=layout, problems=problems, detail=response.text[:300])

    def sweep(
        self, routes: list[ObjectRoute], auth: dict[str, str], *, per_request_snapshot: bool
    ) -> SweepResult:
        """Refuse every foreign layout of every route; mutations must not persist."""
        result = SweepResult()
        before = self.snapshot()
        for route in routes:
            for layout in _LAYOUTS:
                outcome = self.refuse(route, layout, auth)
                if outcome is None:
                    continue
                result.exercised += 1
                changed: list[str] = []
                if per_request_snapshot:
                    after = self.snapshot()
                    changed = _changed_tables(before, after)
                    before = after
                result.record(outcome, changed)
        if not per_request_snapshot:
            result.side_effects = _changed_tables(before, self.snapshot())
        return result

    def object_rows_exist(self) -> list[str]:
        """Every seeded object is visible to its owner tenant under RLS.

        The positive control: a foreign reference's 404 is an authorization
        decision, not a missing fixture, because the row is really there for the
        tenant that owns it.  Checked at the data layer so it holds for every
        object kind, including those whose read routes need richer domain state
        than a bare row provides.
        """
        missing: list[str] = []
        for owner in (self.tenants.b, self.tenants.a2):
            with _tenant_session(self.engine, owner.tenant.organization_id) as session:
                for kind in OBJECT_KINDS:
                    if kind.name in {"bank", "user"}:
                        continue
                    model = _MODEL_BY_KIND[kind.name]
                    if session.get(model, UUID(owner.ids[kind.name])) is None:
                        missing.append(f"{owner.tenant.bank_id}:{kind.name}")
        return missing


def _caller_headers(
    tenant: TenantSeed, roles: tuple[str, ...] = _FULL_AUTHORITY_ROLES
) -> dict[str, str]:
    return headers(
        org_id=tenant.organization_id,
        user_id=tenant.actor_id,
        roles=roles,
        authorization_version=1,
    )


# --- app wiring -------------------------------------------------------------


@pytest.fixture
def object_reference_app(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: Any,
    storage_engine: Any,
) -> tuple[FastAPI, Engine]:
    engine = forward_migrated_postgres_schema.app_engine
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    clear_database_caches()
    app = create_app()
    app.dependency_overrides[get_object_storage] = lambda: fake_storage
    app.dependency_overrides[get_ingestion_storage] = lambda: storage_engine
    return app, engine


def _seed_everything(engine: Engine) -> Tenants:
    _seed_organization(engine, _TENANT_A)
    _seed_sibling_bank(engine, _TENANT_A2)
    _seed_organization(engine, _TENANT_B)
    tenants = Tenants(
        a=_seed_tenant_objects(engine, _TENANT_A),
        a2=_seed_tenant_objects(engine, _TENANT_A2),
        b=_seed_tenant_objects(engine, _TENANT_B),
    )
    _replace_bindings(
        engine,
        _ORG_A,
        [
            *_full_authority_bindings(_TENANT_A, owner=True),
            *_full_authority_bindings(_TENANT_A2, owner=False),
        ],
    )
    _replace_bindings(engine, _ORG_B, _full_authority_bindings(_TENANT_B, owner=True))
    return tenants


def test_foreign_object_references_are_refused_without_side_effects(
    object_reference_app: tuple[FastAPI, Engine],
) -> None:
    """A fully entitled bank-A caller cannot reach any foreign object.

    Reads leak nothing, mutations never take effect, the seeded objects really
    exist for their owners, and every documented defect is still exactly as
    documented (a fix must promote it out of the quarantine).
    """
    app, engine = object_reference_app
    tenants = _seed_everything(engine)
    routes = _object_routes(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        sweep = Sweep(client=client, document=app.openapi(), engine=engine, tenants=tenants)
        result = sweep.sweep(routes, _caller_headers(_TENANT_A), per_request_snapshot=True)
        assert result.exercised >= len(routes)
        assert not result.failures, "\n".join(result.failures)
        assert not sweep.object_rows_exist(), "positive control: seeded objects are missing"
        unpromoted = _KNOWN_DEFECTS - result.defective
        assert not unpromoted, (
            "documented object-reference defects are no longer reproduced; remove them from "
            f"_KNOWN_DEFECTS and add the fixed routes to the strict property: {sorted(unpromoted)}"
        )


def test_generated_bank_a_callers_never_reach_foreign_objects(
    object_reference_app: tuple[FastAPI, Engine],
) -> None:
    """Any bank-A principal with generated bindings is refused without leaking."""
    app, engine = object_reference_app
    tenants = _seed_everything(engine)
    routes = _object_routes(app)

    @settings(max_examples=15, deadline=None)
    @given(caller=_CALLER_SPEC)
    def property_check(
        caller: tuple[int, list[str], list[tuple[int, RoleBundle, ModuleScope, SensitivityScope]]],
    ) -> None:
        user_slot, roles, specs = caller
        _replace_bindings(engine, _ORG_A, _generated_bindings(_TENANT_A, specs))
        auth = headers(
            org_id=_ORG_A,
            user_id=_TENANT_A.user_ids[user_slot],
            roles=tuple(roles),
            authorization_version=1,
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            sweep = Sweep(client=client, document=app.openapi(), engine=engine, tenants=tenants)
            result = sweep.sweep(routes, auth, per_request_snapshot=False)
            assert not result.failures, "\n".join(result.failures)

    property_check()


def test_object_reference_property_detects_weakened_package_guard(
    object_reference_app: tuple[FastAPI, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: a package lookup that ignores the bank must fail the property.

    The guard is weakened only under a rolled-back monkeypatch — no product code
    changes — and the same sweep that passes strictly must then report failures.
    """
    app, engine = object_reference_app
    tenants = _seed_everything(engine)
    package_routes = [
        route
        for route in _object_routes(app)
        if route.method == "GET" and route.path.endswith("/regulatory-packages/{package_id}")
    ]
    assert package_routes
    auth = _caller_headers(_TENANT_A)

    original = regulatory_common.get_package_or_404

    def unscoped_lookup(db: Session, ctx: Any, bank_id: str, package_id: UUID) -> Any:
        # The defect under test: resolve the package by id inside the caller's
        # organization but ignore which bank the path names.
        del bank_id
        return original(db, ctx, tenants.a2.tenant.bank_id, package_id)

    with TestClient(app, raise_server_exceptions=False) as client:
        sweep = Sweep(client=client, document=app.openapi(), engine=engine, tenants=tenants)
        with monkeypatch.context() as patched:
            patched.setattr(regulatory_common, "get_package_or_404", unscoped_lookup)
            patched.setattr(
                "app.services.regulatory_reporting.packages.get_package_or_404", unscoped_lookup
            )
            weakened = sweep.sweep(package_routes, auth, per_request_snapshot=False)
        assert weakened.failures, "the weakened package guard went unnoticed"
        restored = sweep.sweep(package_routes, auth, per_request_snapshot=False)
        assert not restored.failures, "\n".join(restored.failures)
