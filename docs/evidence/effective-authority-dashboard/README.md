# Effective authority dashboard evidence

Captured from the isolated capability fixture on dedicated local backend and
dashboard ports.

- `liquidity-monitoring-unbound.png`: an active user whose token carries a
  scalar Viewer role but has no effective binding receives a 404 on the direct
  product route. The browser test also asserts that no product query mounts.
- `liquidity-monitoring-bound.png`: an exact active
  LIQ/confidential/view binding exposes the Monitoring Tools tab and permits the
  matching route.

Reproduce without object storage:

```bash
E2E_CAPABILITY_ONLY=1 \
E2E_EVIDENCE_DIR=../../docs/evidence/effective-authority-dashboard \
pnpm exec playwright test e2e/liquidity-monitoring-authorization.spec.ts
```
