"""Every bank-scoped tenant route hides sibling-organization bank existence."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Bank, User
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, headers

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres bank-route tenancy sweep.",
)

_SIBLING_BANK_ID = "BK-RLSPRP02"
_MEMBER_ID = UUID("12121212-1212-4121-8121-121212121212")
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_REQUEST_BODY_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    (
        "POST",
        "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis",
    ): {
        "reporting_period_id": "11111111-2222-4333-8444-555555555555",
        "scenarios": [{"kind": "system", "code": "baseline"}],
    },
    (
        "POST",
        "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analyses",
    ): {
        "reporting_period_id": "11111111-2222-4333-8444-555555555555",
        "name": "Cross-tenant probe",
        "scenarios": [{"kind": "system", "code": "baseline"}],
    },
}


def _resolve_schema(schema: Mapping[str, Any], document: Mapping[str, Any]) -> Mapping[str, Any]:
    resolved = schema
    while "$ref" in resolved:
        target: Any = document
        for part in cast(str, resolved["$ref"]).removeprefix("#/").split("/"):
            target = target[part]
        resolved = cast(Mapping[str, Any], target)
    return resolved


def _schema_value(  # noqa: PLR0911, PLR0912 - OpenAPI schema variants are explicit
    schema: Mapping[str, Any],
    document: Mapping[str, Any],
) -> Any:
    schema = _resolve_schema(schema, document)
    if "const" in schema:
        return schema["const"]
    if enum := schema.get("enum"):
        return enum[0]
    for union_key in ("oneOf", "anyOf"):
        if options := schema.get(union_key):
            non_null = next(
                (option for option in options if option.get("type") != "null"),
                options[0],
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
            additional_schema = additional if isinstance(additional, dict) else {}
            value["probe"] = _schema_value(additional_schema, document)
        return value
    if schema_type == "array":
        count = max(1, int(schema.get("minItems", 0)))
        return [_schema_value(cast(Mapping[str, Any], schema.get("items", {})), document)] * count
    if schema_type == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        return int(schema.get("exclusiveMinimum", -1)) + 1
    if schema_type == "number":
        if "minimum" in schema:
            return float(schema["minimum"])
        return float(schema.get("exclusiveMinimum", -1)) + 1
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None

    format_examples: dict[str, str] = {
        "date": "2026-09-17",
        "date-time": datetime(2026, 9, 17, tzinfo=UTC).isoformat(),
        "email": "member@example.test",
        "uri": "https://example.test",
        "uuid": "11111111-2222-4333-8444-555555555555",
    }
    format_name = schema.get("format")
    value = format_examples.get(format_name, "test") if isinstance(format_name, str) else "test"
    min_length = int(schema.get("minLength", 0))
    value = value.ljust(min_length, "x")
    if "maxLength" in schema:
        value = value[: int(schema["maxLength"])]
    return value


def _operation_parameters(
    path_item: Mapping[str, Any],
    operation: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    return [
        *cast(list[Mapping[str, Any]], path_item.get("parameters", [])),
        *cast(list[Mapping[str, Any]], operation.get("parameters", [])),
    ]


def _fill_path(
    path: str,
    parameters: list[Mapping[str, Any]],
    document: Mapping[str, Any],
) -> str:
    filled = path
    for parameter in parameters:
        if parameter.get("in") != "path":
            continue
        name = cast(str, parameter["name"])
        value = (
            _SIBLING_BANK_ID
            if name == "bank_id"
            else _schema_value(cast(Mapping[str, Any], parameter["schema"]), document)
        )
        filled = filled.replace(f"{{{name}}}", str(value))
    return filled


def _request_kwargs(  # noqa: PLR0912 - request media and parameter variants are explicit
    method: str,
    path: str,
    path_item: Mapping[str, Any],
    operation: Mapping[str, Any],
    document: Mapping[str, Any],
) -> dict[str, Any]:
    parameters = _operation_parameters(path_item, operation)
    query: dict[str, Any] = {}
    request_headers: dict[str, str] = {}
    for parameter in parameters:
        location = parameter.get("in")
        name = cast(str, parameter.get("name"))
        if location not in {"query", "header"}:
            continue
        if name == "bank_id":
            value: Any = _SIBLING_BANK_ID
        elif not parameter.get("required"):
            continue
        else:
            value = _schema_value(cast(Mapping[str, Any], parameter["schema"]), document)
        if location == "query":
            query[name] = value
        else:
            request_headers[name] = str(value)

    kwargs: dict[str, Any] = {"params": query}
    if request_headers:
        kwargs["headers"] = request_headers

    content = cast(
        Mapping[str, Mapping[str, Any]],
        operation.get("requestBody", {}).get("content", {}),
    )
    if json_media := content.get("application/json"):
        kwargs["json"] = _REQUEST_BODY_OVERRIDES.get(
            (method, path),
            _schema_value(
                cast(Mapping[str, Any], json_media["schema"]),
                document,
            ),
        )
    elif multipart_media := content.get("multipart/form-data"):
        body = cast(
            dict[str, Any],
            _schema_value(cast(Mapping[str, Any], multipart_media["schema"]), document),
        )
        files: dict[str, tuple[str, bytes, str]] = {}
        data: dict[str, Any] = {}
        resolved = _resolve_schema(
            cast(Mapping[str, Any], multipart_media["schema"]),
            document,
        )
        properties = cast(Mapping[str, Mapping[str, Any]], resolved.get("properties", {}))
        for name, value in body.items():
            if properties[name].get("contentMediaType"):
                files[name] = ("probe.txt", b"probe", "text/plain")
            else:
                data[name] = value
        kwargs.update(data=data, files=files)
    return kwargs


def _bank_operations(
    document: Mapping[str, Any],
) -> list[tuple[str, str, Mapping[str, Any], Mapping[str, Any]]]:
    operations: list[tuple[str, str, Mapping[str, Any], Mapping[str, Any]]] = []
    for path, raw_path_item in cast(Mapping[str, Any], document["paths"]).items():
        path_item = cast(Mapping[str, Any], raw_path_item)
        for method, raw_operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            operation = cast(Mapping[str, Any], raw_operation)
            parameters = _operation_parameters(path_item, operation)
            has_bank_query = any(
                parameter.get("in") == "query" and parameter.get("name") == "bank_id"
                for parameter in parameters
            )
            if "{bank_id}" in path or has_bank_query:
                operations.append((method.upper(), path, path_item, operation))
    return operations


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def test_every_bank_route_hides_sibling_tenant_bank(
    db_client: TestClient,
    db_session: Session,
) -> None:
    db_session.add_all(
        [
            User(
                id=_MEMBER_ID,
                organization_id=ORG_1,
                email="rls-property-member@example.test",
                display_name="RLS Property Member",
                role="viewer",
            ),
            Bank(
                id=_SIBLING_BANK_ID,
                organization_id=ORG_2,
                name="Sibling Tenant Bank",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="Universal Bank",
                institution_type=FALLBACK_TYPE_CODE,
            ),
        ]
    )
    db_session.commit()

    app = cast(FastAPI, db_client.app)
    document = app.openapi()
    bank_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and (
            "{bank_id}" in route.path
            or any(parameter.name == "bank_id" for parameter in route.dependant.query_params)
        )
    ]
    unguarded = [
        route.path for route in bank_routes if "resolve_tenant_bank" not in _dependency_names(route)
    ]
    assert not unguarded, f"bank routes missing resolve_tenant_bank: {unguarded}"

    member_headers = headers(
        ORG_1,
        user_id=_MEMBER_ID,
        roles=(),
        authorization_version=1,
    )
    failures: list[str] = []
    operations = _bank_operations(document)

    for method, path, path_item, operation in operations:
        parameters = _operation_parameters(path_item, operation)
        request_path = _fill_path(path, parameters, document)
        kwargs = _request_kwargs(method, path, path_item, operation, document)
        request_headers = {**member_headers, **kwargs.pop("headers", {})}
        response = db_client.request(
            method,
            request_path,
            headers=request_headers,
            **kwargs,
        )
        body = response.json()
        expected_error_keys = {"code", "message", "request_id"}
        if (
            response.status_code != 404
            or set(body) != {"error"}
            or set(body.get("error", {})) != expected_error_keys
            or body["error"].get("code") != "not_found"
            or body["error"].get("message") != "Bank not found."
        ):
            failures.append(f"{method} {path}: {response.status_code} {body}")

    assert operations
    assert len(operations) == len(bank_routes)
    assert not failures, "\n".join(failures)
