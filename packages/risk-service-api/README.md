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
client files, derives a temporary generator-only schema for calculation models
with nullable dates, regenerates the `typescript-fetch` client, formats
generated TypeScript, and restores source-first package metadata. The committed
OpenAPI schema remains the canonical service contract.

## Verification

```bash
mise run risk-service:api-typecheck
pnpm --filter @aequoros/risk-service-api test
```

## Usage

```ts
import { CalculationsApi, Configuration } from "@aequoros/risk-service-api";

const calculations = new CalculationsApi(
  new Configuration({ basePath: "http://127.0.0.1:8003" }),
);

const run = await calculations.startCalculationRun({
  caseId,
  xOrgId,
  xUserId,
  calculationRunCreate: {
    scenarioId,
    forecastPeriods: 3,
  },
});

const history = await calculations.listCalculationRuns({
  caseId,
  xOrgId,
  limit: 25,
  offset: 0,
});
```

Calculation mutations require both tenant headers. List and detail calls
require `X-Org-Id` and accept `X-User-Id` when an actor is available.
