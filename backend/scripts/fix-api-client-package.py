#!/usr/bin/env python3
"""Restore generated API client metadata for source-first workspace imports."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

SOURCE_ENTRY = "./src/index.ts"
REPO_URL = "git@github.com:CedynGroup/aequorOS.git"
README = """# @aequoros/risk-service-api

TypeScript client generated from the AequorOS Risk Service OpenAPI schema.

Generated `src/` code is committed so frontend consumers do not need Python or
the OpenAPI generator installed.

Generated source is excluded centrally from routine style linting and formatting
through the repository `.eslintignore` and `.prettierignore`. TypeScript
type-checking, package tests, and deterministic regeneration freshness checks
remain enforced.

## Regenerating

From the repo root:

```bash
mise run risk-service:openapi-client
```

Or from `apps/risk-service`:

```bash
mise run risk-service:openapi-client
```

The generation target exports `openapi-schema.json`, clears stale generated
client files, derives a temporary generator-only schema for nullable union
models, regenerates the `typescript-fetch` client, formats generated TypeScript,
and restores source-first package metadata. The committed OpenAPI schema remains
the canonical service contract. Regeneration intentionally bypasses the repository
Prettier exclusion to normalize deterministic generated output.

Generated capital contracts preserve nullable projections, errors, comparison
diagnostics, and lifecycle timestamps. Capital timestamps decode as
`Date | null`.

## Verification

```bash
mise run risk-service:api-typecheck
pnpm --filter @aequoros/risk-service-api test
mise run risk-service:api-fresh
```

`risk-service:api-fresh` regenerates the schema and client, type-checks them, and
fails when regeneration changes committed generated files.

## Usage

```ts
import { CapitalApi, Configuration } from "@aequoros/risk-service-api";

const capital = new CapitalApi(
  new Configuration({ basePath: "http://127.0.0.1:8003" }),
);

const projection = await capital.createCapitalProjection({
  caseId,
  xOrgId,
  xUserId,
  capitalProjectionCreate: { calculationRunId },
});

const comparison = await capital.getCapitalComparison({ caseId, xOrgId });

if (projection.error !== null || comparison.diagnostic !== null) {
  // Render the persisted projection failure or comparison-basis diagnostic.
}
```

Capital mutations require both tenant headers. List, detail, summary, and
comparison calls require `X-Org-Id` and accept `X-User-Id` when an actor is
available.

Calculation mutations require both tenant headers. List and detail calls
require `X-Org-Id` and accept `X-User-Id` when an actor is available.

Liquidity analysis is exposed through the generated `LiquidityApi`:

```ts
import { Configuration, LiquidityApi } from "@aequoros/risk-service-api";

const liquidity = new LiquidityApi(
  new Configuration({ basePath: "http://127.0.0.1:8003" }),
);

const summary = await liquidity.getLiquiditySummary({
  caseId,
  xOrgId,
  scenarioId,
  runId,
});

const reviewed = await liquidity.reviewLiquidityFinding({
  caseId,
  findingId,
  xOrgId,
  xUserId,
  liquidityFindingReview: { action: "dismiss", reason: "Duplicate exposure." },
});
```

Summary reads can omit `scenarioId` and `runId` to select the newest successful
run. Finding review requires both tenant headers; dismissal also requires a
non-empty reason.
"""
GITIGNORE = """# Generated files - only exclude compiled output
dist/

# Build artifacts
node_modules/
wwwroot/*.js
typings
.turbo/
.openapi-generator/

# Keep in version control:
# - src/ (generated TypeScript source)
# - package.json (auto-fixed by mise for source-first imports)
# - .openapi-generator-ignore
# - tsconfig*.json
# - .gitignore
# - .npmignore
"""
GITATTRIBUTES = """# Auto-resolve conflicts in generated code
src/** merge=ours
"""
NPMIGNORE = """README.md
"""


def fix_package_json(data: dict[str, Any]) -> dict[str, Any]:
    data["main"] = SOURCE_ENTRY
    data["module"] = SOURCE_ENTRY
    data["types"] = SOURCE_ENTRY
    data["typings"] = SOURCE_ENTRY
    data["sideEffects"] = False
    data["repository"] = {"type": "git", "url": REPO_URL}
    data["exports"] = {
        ".": {
            "types": SOURCE_ENTRY,
            "default": SOURCE_ENTRY,
        }
    }
    scripts = data.setdefault("scripts", {})
    scripts["build"] = "echo 'No build needed - source consumed directly'"
    scripts["prepare"] = "echo 'No prepare needed - source consumed directly'"
    scripts["test"] = (
        "rm -rf dist-test && tsc --outDir dist-test "
        "&& node dist-test/tests/generated-contracts.test.js && rm -rf dist-test"
    )
    scripts["type-check"] = "tsc --noEmit"
    return data


def patch_error_body(package_root: Path) -> None:
    model_path = package_root / "src" / "models" / "ErrorBody.ts"
    text = model_path.read_text(encoding="utf-8")
    detail_type_variants = ("details?: null;", "details?:  | null;")
    if "details?: any | null;" not in text:
        for generated in detail_type_variants:
            if generated in text:
                text = text.replace(generated, "details?: any | null;")
                break
        else:
            raise ValueError("Expected ErrorBody.ts details type fragment not found")
    replacement_groups = (
        (
            (
                'details: json["details"] == null ? undefined : FromJSON(json["details"]),',
                "'details': json['details'] == null ? undefined : FromJSON(json['details']),",
            ),
            "'details': json['details'] == null ? undefined : json['details'],",
            'details: json["details"] == null ? undefined : json["details"],',
        ),
        (
            (
                'details: ToJSON(value["details"]),',
                "'details': ToJSON(value['details']),",
            ),
            "'details': value['details'],",
            'details: value["details"],',
        ),
    )
    for generated_variants, patched, formatted in replacement_groups:
        if patched in text or formatted in text:
            continue
        for generated in generated_variants:
            if generated in text:
                text = text.replace(generated, patched)
                break
        else:
            raise ValueError(f"Expected ErrorBody.ts fragment not found: {generated_variants[0]}")
    model_path.write_text(text, encoding="utf-8")


def patch_payload(package_root: Path) -> None:
    model_path = package_root / "src" / "models" / "Payload.ts"
    text = model_path.read_text(encoding="utf-8")
    imports = """import {
  CaseBulkArchiveCreate,
  CaseBulkArchiveCreateFromJSONTyped,
  CaseBulkArchiveCreateToJSON,
} from "./CaseBulkArchiveCreate";
import {
  CaseBulkAssignCreate,
  CaseBulkAssignCreateFromJSONTyped,
  CaseBulkAssignCreateToJSON,
} from "./CaseBulkAssignCreate";
import {
  CaseBulkUnassignCreate,
  CaseBulkUnassignCreateFromJSONTyped,
  CaseBulkUnassignCreateToJSON,
} from "./CaseBulkUnassignCreate";
import {
  CaseBulkUpdateStatusCreate,
  CaseBulkUpdateStatusCreateFromJSONTyped,
  CaseBulkUpdateStatusCreateToJSON,
} from "./CaseBulkUpdateStatusCreate";

"""
    if 'from "./CaseBulkArchiveCreate"' not in text:
        marker = " */\n\n"
        if marker not in text:
            raise ValueError("Expected Payload.ts header marker not found")
        text = text.replace(marker, f" */\n\n{imports}", 1)

    from_json, separator, to_json = text.partition("export function PayloadToJSON")
    if not separator:
        raise ValueError("Expected Payload.ts serializer function not found")
    from_json = re.sub(
        r"(default:\s+)return value;",
        r"\1return json;",
        from_json,
    )
    to_json = re.sub(
        r"(default:\s+)return json;",
        r"\1return value;",
        to_json,
    )
    if not re.search(r"default:\s+return json;", from_json) or not re.search(
        r"default:\s+return value;", to_json
    ):
        raise ValueError("Expected Payload.ts default serializer branches not found")
    text = from_json + separator + to_json
    model_path.write_text(text, encoding="utf-8")


def property_name(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


def schema_type(schema: dict[str, Any], components: dict[str, Any]) -> str:
    if "$ref" in schema:
        result = schema_type(components[schema["$ref"].rsplit("/", 1)[-1]], components)
    elif "const" in schema:
        result = json.dumps(schema["const"])
    elif "enum" in schema:
        result = " | ".join(json.dumps(value) for value in schema["enum"])
    elif "anyOf" in schema:
        types = list(dict.fromkeys(schema_type(item, components) for item in schema["anyOf"]))
        result = " | ".join(types)
    else:
        primitive = schema.get("type")
        primitive_types = {
            "integer": "number",
            "number": "number",
            "string": "string",
            "boolean": "boolean",
            "null": "null",
            "object": "{ [key: string]: any }",
        }
        if primitive == "array":
            result = f"Array<{schema_type(schema['items'], components)}>"
        elif primitive in primitive_types:
            result = primitive_types[primitive]
        else:
            raise ValueError(f"Unsupported inline schema: {schema}")
    return result


def schema_guard(schema: dict[str, Any], components: dict[str, Any]) -> str:  # noqa: PLR0911
    if "$ref" in schema:
        return schema_guard(components[schema["$ref"].rsplit("/", 1)[-1]], components)
    if "const" in schema:
        return f"value === {json.dumps(schema['const'])}"
    if "enum" in schema:
        values = ", ".join(json.dumps(value) for value in schema["enum"])
        return f"[{values}].indexOf(value as never) !== -1"
    if "anyOf" in schema:
        guards = list(dict.fromkeys(schema_guard(item, components) for item in schema["anyOf"]))
        return " || ".join(f"({guard})" for guard in guards)
    primitive = schema.get("type")
    if primitive in {"integer", "number", "string", "boolean"}:
        runtime_type = "number" if primitive in {"integer", "number"} else primitive
        return f'typeof value === "{runtime_type}"'
    if primitive == "null":
        return "value === null"
    if primitive == "array":
        return "Array.isArray(value)"
    if primitive == "object":
        return 'typeof value === "object" && value !== null && !Array.isArray(value)'
    raise ValueError(f"Unsupported inline schema guard: {schema}")


def map_value_schema(prop: dict[str, Any], components: dict[str, Any]) -> dict[str, Any] | None:
    """The additionalProperties schema of a map-typed property, if it is one."""
    resolved = components[prop["$ref"].rsplit("/", 1)[-1]] if "$ref" in prop else prop
    additional = resolved.get("additionalProperties")
    return additional if isinstance(additional, dict) else None


def patch_primitive_aliases(package_root: Path, schema_path: Path) -> None:
    document = json.loads(schema_path.read_text(encoding="utf-8"))
    components = document["components"]["schemas"]
    model_dir = package_root / "src" / "models"
    model_text = {path.stem: path.read_text(encoding="utf-8") for path in model_dir.glob("*.ts")}
    empty_models = {
        name
        for name, text in model_text.items()
        if re.search(rf"export interface {re.escape(name)} \{{\s*\}}", text)
    }
    for alias in sorted(empty_models):
        schemas: list[dict[str, Any]] = [components[alias]] if alias in components else []
        property_pattern = re.compile(
            rf"^\s+(\w+)\??: {re.escape(alias)}(?: \| null)?;$", re.MULTILINE
        )
        # The alias may also be generated for the value type of an inline map
        # (additionalProperties), e.g. `fields: { [key: string]: FieldsValue; }`.
        map_pattern = re.compile(
            rf"^\s+(\w+)\??: \{{ \[key: string\]: {re.escape(alias)}(?: \| null)?; \}}"
            rf"(?: \| null)?;$",
            re.MULTILINE,
        )
        for consumer, text in model_text.items():
            if consumer not in components:
                continue
            for match in property_pattern.finditer(text):
                generated_name = match.group(1)
                candidates = [
                    value
                    for name, value in components[consumer].get("properties", {}).items()
                    if property_name(name) == generated_name
                ]
                if len(candidates) != 1:
                    raise ValueError(f"Could not resolve {consumer}.{generated_name} for {alias}")
                schemas.append(candidates[0])
            for match in map_pattern.finditer(text):
                generated_name = match.group(1)
                candidates = [
                    value_schema
                    for name, value in components[consumer].get("properties", {}).items()
                    if property_name(name) == generated_name
                    and (value_schema := map_value_schema(value, components)) is not None
                ]
                if len(candidates) != 1:
                    raise ValueError(f"Could not resolve {consumer}.{generated_name} for {alias}")
                schemas.append(candidates[0])
        if not schemas:
            raise ValueError(f"Could not find a schema use for generated alias {alias}")
        types = {schema_type(schema, components) for schema in schemas}
        guards = {schema_guard(schema, components) for schema in schemas}
        if len(types) != 1:
            raise ValueError(f"Generated alias {alias} has conflicting schemas: {sorted(types)}")
        if len(guards) != 1:
            raise ValueError(f"Generated alias {alias} has conflicting guards: {sorted(guards)}")
        alias_path = model_dir / f"{alias}.ts"
        text = model_text[alias]
        text = re.sub(r"import \{ mapValues \} from ['\"]\.\./runtime['\"];\n", "", text)
        text = re.sub(
            rf"export interface {re.escape(alias)} \{{\s*\}}",
            f"export type {alias} = {types.pop()};",
            text,
        )
        instance_pattern = (
            rf"export function instanceOf{re.escape(alias)}"
            rf"\(\s*value: object,?\s*\): value is {re.escape(alias)}"
        )
        text = re.sub(
            instance_pattern,
            f"export function instanceOf{alias}(value: unknown): value is {alias}",
            text,
        )
        guard = guards.pop()
        text = re.sub(
            rf"(export function instanceOf{re.escape(alias)}[\s\S]*?\{{\s*)return true;",
            rf"\1return {guard};",
            text,
            count=1,
        )
        alias_path.write_text(text, encoding="utf-8")


def patch_closed_models(package_root: Path, schema_path: Path) -> None:
    document = json.loads(schema_path.read_text(encoding="utf-8"))
    components = document["components"]["schemas"]
    for model_path in (package_root / "src" / "models").glob("*.ts"):
        text = model_path.read_text(encoding="utf-8")
        schema = components.get(model_path.stem, {})
        if schema.get("additionalProperties") is not False:
            continue
        patched = re.sub(
            r"^\s+\[key: string\]: any \| any;\n",
            "",
            text,
            flags=re.MULTILINE,
        )
        patched = re.sub(r"^\s+\.\.\.value,\n", "", patched, flags=re.MULTILINE)
        if patched != text:
            model_path.write_text(patched, encoding="utf-8")


def remove_lint_suppression_headers(package_root: Path) -> None:
    suppression_header = "/* tslint:disable */\n/* eslint-disable */\n"
    for source_path in (package_root / "src").rglob("*.ts"):
        text = source_path.read_text(encoding="utf-8")
        if not text.startswith(suppression_header):
            raise ValueError(f"Expected lint suppression header in {source_path}")
        source_path.write_text(text.removeprefix(suppression_header), encoding="utf-8")


def patch_generated_source(package_root: Path, schema_path: Path) -> None:
    remove_lint_suppression_headers(package_root)
    patch_error_body(package_root)
    patch_payload(package_root)
    patch_primitive_aliases(package_root, schema_path)
    patch_closed_models(package_root, schema_path)


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: fix-api-client-package.py <package.json path> <OpenAPI schema path>",
            file=sys.stderr,
        )
        return 1

    package_path = Path(sys.argv[1])
    try:
        data = json.loads(package_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Error reading {package_path}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error parsing {package_path}: {exc}", file=sys.stderr)
        return 1

    package_path.write_text(
        json.dumps(fix_package_json(data), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    package_root = package_path.parent
    try:
        patch_generated_source(package_root, Path(sys.argv[2]))
    except (OSError, ValueError) as exc:
        print(f"Error patching generated source: {exc}", file=sys.stderr)
        return 1
    (package_root / "README.md").write_text(README, encoding="utf-8")
    (package_root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (package_root / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    (package_root / ".npmignore").write_text(NPMIGNORE, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
