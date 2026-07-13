# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Scenario resources live under `/api/v1/cases/{case_id}/scenarios`. Calculation
  readiness requires every active scenario to contain growth, expenses,
  cash-flow timing, credit-usage, and repayment-behavior assumptions, with each
  assumption explicitly reviewed after its latest edit.
- Regenerate scenario and other API contracts with
  `mise run risk-service:openapi-client`; validate the generated package with
  `pnpm --filter @aequoros/risk-service-api test`.
