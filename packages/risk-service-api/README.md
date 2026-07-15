# @aequoros/risk-service-api

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
