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
TypeScript, and restores source-first package metadata. It also derives a
deterministic package version from the OpenAPI schema so unchanged contracts
produce unchanged package metadata.

## Verification

```bash
mise run risk-service:api-typecheck
```

## Usage

```ts
import {
  Configuration,
  FinancialDataApi,
  type FinancialCovenantCreate,
} from "@aequoros/risk-service-api";

const api = new FinancialDataApi(
  new Configuration({
    basePath: "http://localhost:8000",
    headers: {
      "X-Org-Id": organizationId,
      "X-User-Id": actorUserId,
    },
  }),
);

const covenant: FinancialCovenantCreate = {
  reason: "Add covenant from signed facility agreement",
  name: "Minimum debt service coverage",
  metric: "debt_service_coverage_ratio",
  operator: "gte",
  threshold: 1.25,
};

const result = await api.createCaseFinancialCovenant({
  caseId,
  xOrgId: organizationId,
  xUserId: actorUserId,
  financialCovenantCreate: covenant,
});
```

The financial mutation methods require tenant and actor headers and return both
the canonical record and refreshed case validation. Generated operation names
and request parameter objects are the source of truth for exact call signatures.
