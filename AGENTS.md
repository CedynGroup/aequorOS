# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Scenario resources live under `/api/v1/cases/{case_id}/scenarios`. Calculation
  readiness requires every active scenario to contain growth, expenses,
  cash-flow timing, credit-usage, and repayment-behavior assumptions, with each
  assumption explicitly reviewed after its latest edit.
- Regenerate scenario and other API contracts with
  `mise run risk-service:openapi-client`; validate the generated package with
  `pnpm --filter @aequoros/risk-service-api test`.
- Financial review UI code lives under `apps/aequoros-web/src/features/financial` and must call
  `FinancialDataApi` from `packages/risk-service-api`; do not duplicate OpenAPI payloads or
  hand-roll financial workspace requests.
- Canonical institution, account, reporting-period, balance, cash-flow, obligation, and covenant
  mutations require a non-empty reason and return the record plus refreshed validation. Their
  review forms support manual entry and correction through the generated contracts.
- Keep every financial mutation disabled while demo mode is active. Constrain account and
  obligation statuses to generated contract values; automatic covenant compliance recalculation
  must omit `complianceStatus` so the backend derives it from the covenant inputs.
- Validate web changes with `pnpm --filter @aequoros/aequoros-web typecheck`, `lint`, `test`, and
  `build`; deterministic financial review journeys are in `e2e/financial-review.spec.ts`.
