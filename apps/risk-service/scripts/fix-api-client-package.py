#!/usr/bin/env python3
"""Restore generated API client metadata for source-first workspace imports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SOURCE_ENTRY = "./src/index.ts"
REPO_URL = "git@github.com:CedynGroup/aequorOS.git"
README = """# @aequoros/risk-service-api

TypeScript client generated from the AequorOS Risk Service OpenAPI schema.

Generated `src/` code is committed so frontend consumers do not need Python or
the OpenAPI generator installed.

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
client files, regenerates the `typescript-fetch` client, formats generated
TypeScript, and restores source-first package metadata.

## Verification

```bash
mise run risk-service:api-typecheck
```

## Usage

```ts
import { Configuration, HealthApi } from "@aequoros/risk-service-api";
```
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
        ),
        (
            (
                'details: ToJSON(value["details"]),',
                "'details': ToJSON(value['details']),",
            ),
            "'details': value['details'],",
        ),
    )
    for generated_variants, patched in replacement_groups:
        if patched in text:
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

    from_json_patched = (
        "        default:\n            return value;\n    }\n}\n\nexport function PayloadToJSON"
    )
    from_json_expected = (
        "        default:\n            return json;\n    }\n}\n\nexport function PayloadToJSON"
    )
    text = text.replace(from_json_patched, from_json_expected)

    to_json_expected = "        default:\n            return json;\n    }\n}\n"
    to_json_patched = "        default:\n            return value;\n    }\n}\n"
    if to_json_expected not in text and to_json_patched not in text:
        raise ValueError("Expected Payload.ts default serializer branch not found")
    if to_json_expected in text:
        head, separator, tail = text.rpartition(to_json_expected)
        text = head + to_json_patched + tail if separator else text
    model_path.write_text(text, encoding="utf-8")


def patch_generated_source(package_root: Path) -> None:
    patch_error_body(package_root)
    patch_payload(package_root)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: fix-api-client-package.py <package.json path>", file=sys.stderr)
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
        patch_generated_source(package_root)
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
