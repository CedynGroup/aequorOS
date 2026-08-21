# QA Audit: SDI and Stress Implementation

**Date:** 2026-08-20

**Scope:** Implementation compared with [docs/sdi.md](docs/sdi.md) and [docs/stress.md](docs/stress.md). This is a code and automated-test audit, not a regulatory legal opinion. The worktree was already dirty and received concurrent edits during the review; no source changes were made by this audit.

## Verdict

The claim that both specifications are **fully implemented** is not supported.

The repository contains a substantial implementation: institution types, module scoping, a parameter control plane, simplified SDI capital logic, loan classification, macro scenarios, enterprise stress projection, Appendix II tables, management actions, sign-off, and a complete stress workbench. Focused acceptance suites and the full hermetic backend suite pass.

However, three regulatory correctness/release blockers remain:

1. SDI enterprise stress still uses Basel LCR/NSFR as its liquidity outcome and UI headline, rather than the required LMTD Table-1/reserve/maturity/concentration measures.
2. Enterprise-stress paid-up-capital minima bypass the new regulatory-parameter control plane and can silently resolve to zero.
3. Macro scenarios can be approved with only one year and one driver, despite the required three-year, Table-6-grade scenario contract.

The local database is also one migration behind source head, so the new regulatory-parameter table is not deployable/runnable in the inspected environment until migration `202608200025` is applied.

## Findings

### P0 — SDI enterprise stress measures the wrong liquidity regime

**Evidence:** [backend/app/services/enterprise_stress.py](backend/app/services/enterprise_stress.py) always invokes the common enterprise orchestrator. [backend/app/domain/stress/orchestrator.py](backend/app/domain/stress/orchestrator.py) always computes `LcrResult` and `NsfrResult`, and the response model requires `stressed_lcr_pct`/`baseline_lcr_pct` in [backend/app/schemas/enterprise_stress.py](backend/app/schemas/enterprise_stress.py). The workbench renders LCR and its floor as a leading KPI and chart in [backend/dashboard/components/stress/EnterpriseStressWorkbench.tsx](backend/dashboard/components/stress/EnterpriseStressWorkbench.tsx).

The current SDI handling only suppresses LCR/NSFR in the forward projection and omits Basel Table 2; it does not replace the live enterprise-stress liquidity outcome with Table-1 ratios, primary/secondary reserve checks, maturity mismatch, funding concentration, counterbalancing capacity, or survival horizon.

**Impact:** An SDI can receive an enterprise-stress result presented as compliant/non-compliant against Basel measures that [docs/sdi.md](docs/sdi.md) explicitly excludes for SDIs. This conflicts with the stated SDI binding liquidity regime.

**Recommendation:** Add an SDI branch to enterprise-stress inputs/outcome/UI that computes and persists the LMTD Table-1 stress metrics plus reserve, maturity-ladder, funding-concentration, counterbalancing-capacity and survival-horizon results. Do not emit LCR/NSFR or their breach coupling for SDI runs.

### P0 — Enterprise stress bypasses the paid-up-capital regulatory parameter

**Evidence:** [backend/app/services/enterprise_stress.py](backend/app/services/enterprise_stress.py) resolves `paid_up_min` from the request payload or tenant `ParamCapitalThreshold`; otherwise it returns zero. It does not call [backend/app/services/regulatory_parameters.py](backend/app/services/regulatory_parameters.py), which is the documented source for licence-specific `paid_up_min`. In contrast, [backend/app/services/sdi_capital_checks.py](backend/app/services/sdi_capital_checks.py) correctly resolves it through that control plane.

**Impact:** A savings-and-loans enterprise stress run can omit the GH¢15m paid-up-capital minimum unless a tenant-specific board threshold is separately configured. This makes the Appendix II minima/gap and management-action sizing non-compliant with the intended SDI control plane.

**Recommendation:** Resolve paid-up capital through `regulatory_parameters.resolve(db, bank, "paid_up_min", as_of=...)`, then apply a tenant override only when it is at least as strict. Include the resolved parameter provenance in the immutable stress input snapshot.

### P0 — Macro scenario approval permits incomplete, non-compliant scenarios

**Evidence:** [backend/app/schemas/stress.py](backend/app/schemas/stress.py) allows `horizon_years >= 1` and requires only one path. The service approval flow in [backend/app/services/macro_scenarios.py](backend/app/services/macro_scenarios.py) approves without checking a three-year horizon, full yearly coverage, required Table-6 drivers, adverse severity, or applicability. Direct schema validation during this audit accepted:

```text
horizon_years=1, paths=[gdp_growth for year 1]
```

`EnterpriseStressRunCreate` requires a three-year projection, but [backend/app/services/enterprise_stress.py](backend/app/services/enterprise_stress.py) does not verify that the approved scenario covers every requested projection year. Missing macro points degrade to neutral translation values.

**Impact:** An officially persisted enterprise-stress run can appear to satisfy the 3-year framework while year 2/3 and required macro drivers are unstressed or absent.

**Recommendation:** Before submission/approval, validate a scenario against its intended use: horizon at least three; all required driver/year pairs; base/adverse semantics; severe-downturn requirement for annual ICAAP scenarios; and source/narrative. Before execution, reject a scenario that does not cover the requested run horizon.

### P1 — Local schema is behind the source migration head

**Evidence:** `uv run alembic heads` returned `202608200025`; `uv run alembic current` returned `202608190024`. Migration [backend/alembic/versions/202608200025_regulatory_parameter_control_plane.py](backend/alembic/versions/202608200025_regulatory_parameter_control_plane.py) creates and seeds `regulatory_parameter`.

**Impact:** The current local runtime/database does not have the claimed control-plane table. Any service path that needs this table will fail or remain unexercised against the actual schema.

**Recommendation:** Apply migration `202608200025` to the local/test deployment database, then run the control-plane and SDI end-to-end checks against the migrated schema. Ensure the deployment artifact includes this migration before release.

### P1 — Tenant overrides are not constrained to tighten a regulatory requirement

**Evidence:** [backend/app/services/regulatory_parameters.py](backend/app/services/regulatory_parameters.py) documents that a hard clamp is “not yet” enforced. [backend/app/services/regulatory_capital.py](backend/app/services/regulatory_capital.py) accepts a tenant `car_min` whenever present, before the control-plane fallback.

**Impact:** A tenant board register can weaken a global regulatory floor, contrary to the documented “tenant may only tighten” rule.

**Recommendation:** Add a shared unit-aware comparison guard for floor/limit overrides. Reject a lower minimum, higher maximum, or otherwise less-conservative tenant value. Cover CAR, liquidity floors, exposure limits, reserve requirements, and provisioning minimums.

### P1 — SDI capital checks are diagnostic-only, not live risk findings

**Evidence:** [backend/app/services/sdi_capital_checks.py](backend/app/services/sdi_capital_checks.py) produces read-only checks. [backend/app/features/read_sdi_diagnostics.py](backend/app/features/read_sdi_diagnostics.py) exposes them only via standalone diagnostic endpoints. The simplified capital view in [backend/dashboard/components/basel/SdiCapitalView.tsx](backend/dashboard/components/basel/SdiCapitalView.tsx) displays them, but the check service has no path to `LiveFinding`, capital run status, alerts, or the enterprise-stress minima outcome.

**Impact:** A paid-up-capital or statutory-reserve failure can be visible on one page but absent from Alerts, Risk & Limits, and live governance signals.

**Recommendation:** Reconcile these checks into capital live findings and official reporting validation. Make the same resolved thresholds drive the simplified capital status, alert feed, and stress minima.

### P2 — Module scoping is client-side presentation control, not backend entitlement enforcement

**Evidence:** [backend/dashboard/components/shell/ModuleGuard.tsx](backend/dashboard/components/shell/ModuleGuard.tsx) is a client component. [backend/dashboard/lib/modules.ts](backend/dashboard/lib/modules.ts) maps routes to visible modules, but the inspected tenant APIs do not enforce institution-type module scope.

**Impact:** Direct API consumers can still call bank-only module endpoints for an SDI. The UI route guard is appropriate navigation control but should not be represented as server-side module authorization.

**Recommendation:** Decide whether module scope is UX-only or an authorization boundary. If it is an entitlement boundary, enforce it in backend dependencies for module endpoints and add API-level denial tests. Otherwise document it explicitly as UI scoping.

## Verified Coverage

The following implementation areas were present and exercised:

- Institution-type registry, resolved class/type payload, onboarding type selection, client nav/tab/route scoping.
- Regulatory-parameter model, effective dating, operator maker-checker API, console surface, seed catalogue, and observability.
- SDI CAR parameter regime, loan-classification/provisioning, exposure-limit findings, LMTD class floors, reserve calculations, readiness diagnostics, and bank-only return rejection.
- Governed macro scenarios, translation, immutable enterprise runs, three-year projection, Appendix II structures, bottom-up credit/concentration/operational/contingent-leverage methods, management-action plans, run registry, and sign-off flow.
- The legacy-named `ScenarioWorkbench` is a compatibility wrapper around the new enterprise stress workbench, so the capital/liquidity/IRR/FX stress routes reach the new UI.

## Test Evidence

- Focused SDI/stress suites: `192 passed`.
- Full hermetic backend suite: `3199 passed, 304 skipped`.
- Dashboard typecheck: passed.
- Console typecheck: passed.
- `git diff --check`: passed before this audit document was added.

The suite establishes strong regression coverage for implemented paths. It does not test the three P0 findings above; in particular, existing SDI stress tests only verify omitted Basel projection fields and Table 2, not the required SDI liquidity-stress replacement.

## Release Assessment

**Do not certify the SDI/stress work as fully implemented or release-ready** until all P0 items are resolved and migration `202608200025` is applied and verified in the target database. The P1 items should be resolved before production use because they affect regulatory control enforcement and exception visibility.
