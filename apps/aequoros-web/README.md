# AequorOS Web

Internal risk operations console for the risk-service API.

## Run

```bash
pnpm --filter @aequoros/aequoros-web dev
```

The app defaults to `http://127.0.0.1:8003/api/v1`. Override with:

```bash
VITE_RISK_API_BASE_URL=http://127.0.0.1:8003/api/v1 pnpm --filter @aequoros/aequoros-web dev
```

## Demo Data

Seed the demo org, user, cases, decisions, and findings with:

```bash
RISK_DEMO_DATABASE_URL=postgresql://postgres:postgres@localhost:15432/risk_service \
  pnpm --filter @aequoros/aequoros-web seed:demo
```

The seed runs direct inserts and needs a local admin database role because application roles are protected by row-level security.

## Checks

```bash
pnpm --filter @aequoros/aequoros-web typecheck
pnpm --filter @aequoros/aequoros-web lint
pnpm --filter @aequoros/aequoros-web test
pnpm --filter @aequoros/aequoros-web test:browser
pnpm --filter @aequoros/aequoros-web e2e
pnpm --filter @aequoros/aequoros-web build
```

## Playwright E2E

The E2E suite starts Vite automatically and expects a seeded risk-service API at
`http://127.0.0.1:8003/api/v1`. From the repository root:

```bash
cd apps/risk-service
docker compose up -d
mise run risk-service:bootstrap-db

cd ../aequoros-web
RISK_DEMO_DATABASE_URL=postgresql://postgres:postgres@localhost:15432/risk_service pnpm seed:demo

cd ../..
DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service \
  CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173 \
  RISK_STORAGE_BACKEND=s3 \
  RISK_S3_BUCKET=risk-local \
  RISK_S3_REGION=us-east-1 \
  RISK_S3_ENDPOINT_URL=http://localhost:9000 \
  RISK_S3_ACCESS_KEY_ID=minioadmin \
  RISK_S3_SECRET_ACCESS_KEY=minioadmin \
  RISK_S3_FORCE_PATH_STYLE=true \
  HOST=127.0.0.1 \
  PORT=8003 \
  mise run risk-service:dev
```

With the backend running:

```bash
pnpm --filter @aequoros/aequoros-web e2e
pnpm --filter @aequoros/aequoros-web e2e:headed
pnpm --filter @aequoros/aequoros-web e2e:ui
```

## Structure

```text
src/
  components/        shared local UI primitives
  features/          feature-owned UI, data helpers, and tests
  lib/               API, constants, persistence, utilities
  routes/            thin TanStack route entry points and search parsing
  shared/            cross-feature route UI helpers
  test/              Vitest setup and render helpers
```

The web app uses a feature-based split so each operational surface owns its UI and tests:

- `src/features/risk-console/risk-console.tsx`: route-level orchestration for tenant, selected case, filters, and queries
- `src/features/risk-console/shell.tsx`: sidebar and top-bar console chrome
- `src/features/risk-console/case-queue-panel.tsx`: queue filters, table, selection, and pagination
- `src/features/risk-console/bulk-actions.tsx`: bulk action dialog, mutation, and result rendering
- `src/features/risk-console/case-workspace.tsx`: detail summary, overview, financial, decisions, documents, findings, and report tabs
- `src/features/risk-console/format.tsx`: risk/status/decision badges and date formatting
- `src/features/risk-console/types.ts`: feature-local queue/search helper types
- `src/features/documents/documents-tab.tsx`: document upload-request, completion, parse, and download URL workflows
- `src/features/findings/findings-tab.tsx`: manual finding creation and finding status updates
- `src/features/financial/financial-sections.tsx`: financial workspace section/table rendering
- `src/features/demo-data/demo-data.ts`: frontend-only fallback/demo data helpers
- `src/shared/route-ui.tsx`: route-level empty, error, and data-list helpers
- `src/routes/risk-console.tsx`: thin route export for TanStack Router wiring

## Test Suite

Vitest tests are colocated with the module they protect:

- `src/lib/api.test.ts`: headers, error envelopes, API route/payload serialization
- `src/features/documents/documents-tab.test.tsx`: document controls, upload request payloads, lifecycle actions
- `src/features/findings/findings-tab.test.tsx`: finding create/update controls and payloads
- `src/features/financial/financial-sections.test.tsx`: all financial workspace sections render
- `src/features/demo-data/demo-data.test.ts`: fallback/demo data filtering and detail construction
- `src/routes/search.test.ts`: typed search-param parsing
- `src/features/risk-console/risk-console.test.tsx`: bulk action result grouping

Browser-mode Vitest tests use Playwright for DOM-heavy component interactions:

- `src/features/risk-console/risk-console.browser.test.tsx`: Radix select portal behavior and dialog rendering in Chromium
