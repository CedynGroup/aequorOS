# Security Review: CedynGroup/aequorOS authorization foundation diff

## Scope

Changed production source in the exact base-to-target commit range, with supporting auth/session call sites.

- Scan mode: branch_diff
- Target kind: git_diff
- Target ID: CedynGroup/aequorOS:80b72288..9e184597
- Revision range: 80b72288ceac42784f275fff617e4a4767b48425...9e184597b32467f076e2101bd3d1ea7666155dc1
- Snapshot digest: codex-security-snapshot/v1:sha256:8b0d4311903e28dc7a7f2f4cf2b62b10f0be78fe97155ab87b5972c6cc1898e2
- Inventory strategy: diff
- Included paths: backend/
- Excluded paths: none
- Runtime or test status: not run by explicit user instruction
- Artifacts reviewed: git diff, changed production files, focused tests, authorization documentation

Limitations and exclusions:
- No tests or application execution were permitted.
- The new binding evaluator is shadow-only and has no external grant endpoint in this diff.

### Scan Summary

| Field | Value |
| --- | --- |
| Scan outcome | completed |
| Reportable findings | 0 |
| Severity mix | none |
| Confidence mix | none |
| Coverage | complete |
| Validation mode | static source and transaction trace |

Canonical artifacts: `scan-manifest.json`, `findings.json`, and `coverage.json`. This report is a deterministic projection of those files.

## Threat Model

A shadow scoped-binding evaluator is added beside live legacy role enforcement; the immediate live boundary is JWT authorization-version invalidation.

### Assets

- tenant data
- binding integrity
- JWT authority generation
- refresh-token lineage

### Trust Boundaries

- bearer credential to tenant principal
- tenant principal to RLS session
- future grant caller to binding mutation service

### Attacker Capabilities

- remote login and bearer input
- authenticated tenant requests
- stolen-token replay without signing or database authority

### Security Objectives

- deny by default
- exact tenant and scope binding
- human/machine separation
- session invalidation on authority change

### Assumptions

- PostgreSQL migration is applied in production
- grant CRUD and endpoint enforcement are intentionally out of scope

## Findings

### No findings

No reportable findings survived the canonical discovery, validation, and reportability gates.

## Reviewed Surfaces

| Surface | Risk Area | Outcome | Notes |
| --- | --- | --- | --- |
| Binding schema, migration, and RLS | tenant and authorization integrity | No issue found | Reviewed schema constraints, composite ownership FKs, migration parity, and FORCE RLS. Two non-exploitable integrity hardening gaps remain code-review warnings. Evidence: artifacts/02_discovery/work_ledger.jsonl |
| Pure authorization evaluator | authorization bypass | No issue found | Exact AND-within-row and OR-across-row semantics, lifecycle, principal type, and global vetoes reviewed. Evidence: artifacts/02_discovery/work_ledger.jsonl |
| JWT authorization-version transition | session invalidation | No issue found | Required claims, tenant-session comparison, refresh comparison, and invalidation transaction reviewed. One fail-closed login race remains a correctness warning. Evidence: artifacts/02_discovery/work_ledger.jsonl |
