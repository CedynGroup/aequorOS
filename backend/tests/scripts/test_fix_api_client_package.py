"""The generated-client patcher resolves every shape an inlined alias takes.

The TypeScript generator emits an empty ``export interface XInner {}`` whenever
a schema was inlined rather than referenced, and this script has to give each
one a real type. It can only do that by finding where the alias is USED, so a
use-shape it does not know about is a hard failure of the whole regeneration —
which is how a list of a primitive union (``BiFilter.values``) broke it: the
generator emitted ``values?: Array<ValuesInner>;`` and only the direct and map
shapes were recognised.

These tests exercise the resolver against small hand-built specs rather than a
real 1,100-schema document, so a failure names the shape that regressed. The
unresolvable case is deliberately last: the error path is the reason the script
is trustworthy and must never be softened into a silent default.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).parents[2]

#: The script's filename is hyphenated, so it cannot be imported as a module.
_spec = importlib.util.spec_from_file_location(
    "fix_api_client_package", BACKEND / "scripts" / "fix-api-client-package.py"
)
assert _spec is not None and _spec.loader is not None
fixpkg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixpkg)

PRIMITIVE_UNION: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "string", "format": "date"},
    ]
}


def _uses(
    alias: str,
    *,
    components: dict[str, Any],
    models: dict[str, str] | None = None,
    apis: dict[str, str] | None = None,
    request_properties: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    # Upstream's name for the resolver, taken as the base in the origin/main
    # merge; the signature is identical and the array shape below is the one
    # case ported onto it.
    return fixpkg._alias_use_schemas(  # noqa: SLF001 - the module has no public alias
        alias,
        components=components,
        model_text=models or {},
        api_text=apis or {},
        request_properties=request_properties or {},
    )


# --- the array shape (the regression) -------------------------------------------------------


def test_a_list_of_an_inline_union_resolves_through_its_array_use() -> None:
    """``values: list[str | int | float | bool | date]`` — the shape that failed.

    Pydantic inlines the union as ``items.anyOf`` instead of a ``$ref``, and the
    generator promotes inline OBJECT schemas to components but not primitive
    unions, so the alias is never a component: its use is the only thing that
    can name it.
    """
    components = {
        "BiFilter": {
            "properties": {
                "member": {"type": "string"},
                "values": {"type": "array", "items": PRIMITIVE_UNION, "maxItems": 500},
            }
        }
    }
    models = {"BiFilter": "export interface BiFilter {\n    values?: Array<ValuesInner>;\n}\n"}
    assert _uses("ValuesInner", components=components, models=models) == [PRIMITIVE_UNION]
    assert fixpkg.schema_type(PRIMITIVE_UNION, components) == "string | number | boolean"
    assert fixpkg.schema_guard(PRIMITIVE_UNION, components) == (
        '(typeof value === "string") || (typeof value === "number") || (typeof value === "boolean")'
    )


def test_a_nullable_list_resolves_through_the_array_use() -> None:
    """``list[X] | None`` wraps the array in an ``anyOf``, and the generated
    property carries its own ``| null``."""
    components = {
        "Holder": {
            "properties": {
                "values": {
                    "anyOf": [
                        {"type": "array", "items": PRIMITIVE_UNION},
                        {"type": "null"},
                    ]
                }
            }
        }
    }
    models = {"Holder": "export interface Holder {\n    values?: Array<ValuesInner> | null;\n}\n"}
    assert _uses("ValuesInner", components=components, models=models) == [PRIMITIVE_UNION]


def test_the_array_shape_is_recognised_in_a_request_interface_too() -> None:
    """Both loops learned the shape: a query body reaches the API loop, not the
    model loop, and the alias must resolve there as well."""
    components: dict[str, Any] = {}
    apis = {
        "BiApi": (
            "export interface BiQueryRequest {\n"
            "    bankId: string;\n"
            "    values?: Array<ValuesInner>;\n"
            "}\n"
        )
    }
    request_properties = {
        "BiQueryRequest": {
            "bankId": {"type": "string"},
            "values": {"type": "array", "items": PRIMITIVE_UNION},
        }
    }
    assert _uses(
        "ValuesInner",
        components=components,
        apis=apis,
        request_properties=request_properties,
    ) == [PRIMITIVE_UNION]


# --- the shapes that already worked ---------------------------------------------------------


@pytest.mark.parametrize("declaration", ["reason?: VoidReason;", "reason?: VoidReason | null;"])
def test_the_direct_shape_still_resolves(declaration: str) -> None:
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    components = {"Void": {"properties": {"reason": nullable_string}}}
    models = {"Void": f"export interface Void {{\n    {declaration}\n}}\n"}
    assert _uses("VoidReason", components=components, models=models) == [nullable_string]
    assert fixpkg.schema_type(nullable_string, components) == "string | null"


def test_the_map_shape_still_resolves() -> None:
    """No schema in the current spec uses it, so this test is its only cover."""
    value = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    components = {
        "Detail": {"properties": {"fields": {"type": "object", "additionalProperties": value}}}
    }
    models = {
        "Detail": "export interface Detail {\n    fields?: { [key: string]: FieldsValue; };\n}\n"
    }
    assert _uses("FieldsValue", components=components, models=models) == [value]


def test_a_registered_component_still_resolves_without_any_use() -> None:
    components = {"Standalone": {"type": "string"}}
    assert _uses("Standalone", components=components) == [{"type": "string"}]


def test_the_three_shapes_do_not_poach_each_other() -> None:
    """One model using all three at once resolves each alias to its own schema."""
    array_items = {"type": "integer"}
    map_value = {"type": "boolean"}
    direct = {"type": "string"}
    components = {
        "Mixed": {
            "properties": {
                "plain": direct,
                "listed": {"type": "array", "items": array_items},
                "mapped": {"type": "object", "additionalProperties": map_value},
            }
        }
    }
    models = {
        "Mixed": (
            "export interface Mixed {\n"
            "    plain?: Plain;\n"
            "    listed?: Array<ListedInner>;\n"
            "    mapped?: { [key: string]: MappedValue; };\n"
            "}\n"
        )
    }
    assert _uses("Plain", components=components, models=models) == [direct]
    assert _uses("ListedInner", components=components, models=models) == [array_items]
    assert _uses("MappedValue", components=components, models=models) == [map_value]


# --- the error paths, which must stay loud ---------------------------------------------------


def test_an_alias_nobody_uses_still_raises() -> None:
    models = {"Unrelated": "export interface Unrelated {\n    other?: string;\n}\n"}
    with pytest.raises(ValueError, match="Could not find a schema use for generated alias Ghost"):
        _uses("Ghost", components={}, models=models)


def test_an_array_use_whose_property_is_not_an_array_raises() -> None:
    """A shape mismatch is a tooling bug, not something to guess through."""
    components = {"Holder": {"properties": {"values": {"type": "string"}}}}
    models = {"Holder": "export interface Holder {\n    values?: Array<ValuesInner>;\n}\n"}
    with pytest.raises(ValueError, match="Could not resolve Holder.values for ValuesInner"):
        _uses("ValuesInner", components=components, models=models)


def test_an_array_use_in_a_request_interface_with_no_such_property_raises() -> None:
    apis = {"BiApi": "export interface BiQueryRequest {\n    values?: Array<ValuesInner>;\n}\n"}
    with pytest.raises(ValueError, match="Could not resolve BiQueryRequest.values"):
        _uses(
            "ValuesInner",
            components={},
            apis=apis,
            request_properties={"BiQueryRequest": {"other": {"type": "string"}}},
        )


def test_conflicting_uses_are_reported_rather_than_silently_picking_one() -> None:
    """Two array uses that disagree must reach the caller's conflict check, so
    the resolver returns EVERY use rather than the first."""
    components = {
        "One": {"properties": {"values": {"type": "array", "items": {"type": "string"}}}},
        "Two": {"properties": {"values": {"type": "array", "items": {"type": "integer"}}}},
    }
    models = {
        "One": "export interface One {\n    values?: Array<ValuesInner>;\n}\n",
        "Two": "export interface Two {\n    values?: Array<ValuesInner>;\n}\n",
    }
    schemas = _uses("ValuesInner", components=components, models=models)
    assert len(schemas) == 2
    assert len({fixpkg.schema_type(s, components) for s in schemas}) == 2


# --- end to end over a miniature package ------------------------------------------------------


def test_patch_primitive_aliases_writes_the_array_alias_and_its_guard(tmp_path: Path) -> None:
    """The emitted TypeScript is the contract: a real type and a real guard."""
    document = {
        "components": {
            "schemas": {
                "BiFilter": {
                    "properties": {
                        "values": {"type": "array", "items": PRIMITIVE_UNION, "maxItems": 500}
                    }
                }
            }
        },
        "paths": {},
    }
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(json.dumps(document), encoding="utf-8")
    models = tmp_path / "src" / "models"
    models.mkdir(parents=True)
    (tmp_path / "src" / "apis").mkdir()
    (models / "BiFilter.ts").write_text(
        "export interface BiFilter {\n    values?: Array<ValuesInner>;\n}\n", encoding="utf-8"
    )
    (models / "ValuesInner.ts").write_text(
        "import { mapValues } from '../runtime';\n"
        "export interface ValuesInner {\n}\n\n"
        "export function instanceOfValuesInner(value: object): value is ValuesInner {\n"
        "    return true;\n}\n",
        encoding="utf-8",
    )

    fixpkg.patch_primitive_aliases(tmp_path, schema_path)

    patched = (models / "ValuesInner.ts").read_text(encoding="utf-8")
    assert "export type ValuesInner = string | number | boolean;" in patched
    assert "export interface ValuesInner {" not in patched
    assert "instanceOfValuesInner(value: unknown): value is ValuesInner" in patched
    assert 'return (typeof value === "string")' in patched
    assert "return true;" not in patched
    assert "mapValues" not in patched
