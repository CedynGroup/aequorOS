# @aequoros/risk-service-api

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
