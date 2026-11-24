"""Split the service's OpenAPI document into the published contract documents."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from app.core.jsonvalue import to_json_object
from app.core.settings import Settings
from app.main import API_VERSION, create_app

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contract"

DOCUMENTS: dict[str, tuple[str, str, Callable[[str], bool]]] = {
    "institution-api.v1.openapi.json": (
        "AequorOS Reporting Gateway — Institution API v1",
        "The public filing API (SPEC.md Part 1): POST /oauth/token and /v1. Every call needs the "
        "client certificate (mutual TLS) and a certificate-bound access token.",
        lambda path: path == "/oauth/token" or path.startswith("/v1/"),
    ),
    "portal-api.openapi.json": (
        "AequorOS Reporting Gateway — Supervisor API",
        "The BoG supervisor API (SPEC.md Part 3) behind the portal. Bearer: the BoG identity "
        "provider's ID token.",
        lambda path: path.startswith("/portal/api/"),
    ),
    "operator-api.openapi.json": (
        "AequorOS Reporting Gateway — Operator API",
        "Enrolment by the channel operator's machine client (SPEC.md Part 5): mutual TLS, "
        "private_key_jwt and a certificate-bound token with scope ops:enrol.",
        lambda path: path.startswith("/ops/api/"),
    ),
}


def _refs(value: JsonValue, found: set[str]) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[-1])
        for item in value.values():
            _refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _refs(item, found)


def full_document() -> dict[str, JsonValue]:
    with tempfile.TemporaryDirectory() as keys:
        settings = Settings(
            env="sandbox",
            database_url="sqlite+pysqlite:///:memory:",
            keys_dir=Path(keys),
            scheduler_enabled=False,
        )
        app = create_app(settings, run_scheduler=False)
        return to_json_object(app.openapi())


def split(document: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
    paths = document.get("paths")
    components = document.get("components")
    assert isinstance(paths, dict)
    assert isinstance(components, dict)
    schemas = components.get("schemas")
    assert isinstance(schemas, dict)
    security = components.get("securitySchemes")
    out: dict[str, dict[str, JsonValue]] = {}
    for filename, (title, description, keep) in DOCUMENTS.items():
        selected: dict[str, JsonValue] = {p: v for p, v in paths.items() if keep(p)}
        needed: set[str] = set()
        _refs(selected, needed)
        pending = set(needed)
        while pending:
            name = pending.pop()
            more: set[str] = set()
            _refs(schemas.get(name), more)
            for extra in more - needed:
                needed.add(extra)
                pending.add(extra)
        used_security: set[str] = set()
        for operation_map in selected.values():
            if isinstance(operation_map, dict):
                for operation in operation_map.values():
                    requirements = (
                        operation.get("security") if isinstance(operation, dict) else None
                    )
                    if isinstance(requirements, list):
                        for requirement in requirements:
                            if isinstance(requirement, dict):
                                used_security.update(requirement.keys())
        component_out: dict[str, JsonValue] = {"schemas": {k: schemas[k] for k in sorted(needed)}}
        if isinstance(security, dict) and used_security:
            component_out["securitySchemes"] = {
                k: security[k] for k in sorted(used_security) if k in security
            }
        out[filename] = {
            "openapi": document.get("openapi"),
            "info": {"title": title, "version": API_VERSION, "description": description},
            "paths": selected,
            "components": component_out,
        }
    return out


def render(document: dict[str, JsonValue]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def exported() -> dict[str, str]:
    return {name: render(doc) for name, doc in split(full_document()).items()}
