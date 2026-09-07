# Effective authority dashboard evidence

Captured from the isolated capability fixture on dedicated local backend and
dashboard ports.

- `liquidity-monitoring-unbound.png`: an active user whose token carries a
  scalar Viewer role but has no effective binding receives a 404 on the direct
  product route. The browser test also asserts that no product query mounts.
- `liquidity-monitoring-bound.png`: an exact active
  LIQ/confidential/view binding exposes the Monitoring Tools tab and permits the
  matching route.
- `zero-binding-shell.png`: an active user with no effective binding sees the
  authoritative no-institutions state. There is no institution selector,
  sidebar, product navigation, or operational module entry.
- `account-only-owner-shell.png`: an Org Owner whose only binding is
  organization-wide Account authority can open Members and organization
  administration. The sidebar contains Settings only, with no Liquidity, IRRBB,
  FX, capital, or other operational navigation.

The bound and unbound direct-route states reproduce without object storage:

```bash
E2E_CAPABILITY_ONLY=1 \
E2E_EVIDENCE_DIR=../../docs/evidence/effective-authority-dashboard \
pnpm exec playwright test e2e/liquidity-monitoring-authorization.spec.ts
```
