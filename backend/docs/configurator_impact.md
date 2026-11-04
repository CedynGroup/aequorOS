# Configurator cutover inventory

Run this read-only sibling of `scripts/authorization_access_impact.py` before
each issue-184 module cutover. It does not change the dashboard report's JSON
shape or issue any grants.

```sh
cd backend
uv run python -m scripts.authorization_configure_impact \
  --organization OR-XXXXXXXX \
  --output-dir /outside/repository/2026-11-04-configure-impact
```

The connection defaults to `WORKER_DATABASE_URL`, then `DATABASE_URL`.
`--database-url` overrides it. Use an authorized read-only database role.
Omit `--organization` only with a role allowed to enumerate every tenant.
Each tenant is queried with its organization context; PostgreSQL assessment
transactions are explicitly read-only. No database URL or identity email is
exported. The output still contains principal and binding IDs, so keep it
private and outside the repository.

Without `--output-dir`, the script prints combined JSON. With it, the script
creates three files, refusing to overwrite earlier evidence:

- `configure-before.json`: eligibility under the reviewed pre-cutover dependency.
- `configure-after.json`: proposed `configure/restricted` eligibility and
  `newly_denied` for every principal, route and target.
- `configure-exact-grants.json`: existing complete binding rows, including
  lifecycle windows/status/revocation, and least-scope candidate rows for
  operator approval, associated with each route.

Files are created with mode `0600`, new output directories with `0700`.
Exit `0` means the assessment completed, **not** that everyone is authorized.
Exit `2` means an evaluation, database or output failure; never use incomplete
evidence to approve deployment. `evaluation_failed` marks evaluator failures.

## What the inventory means

The catalogue in `scripts/authorization_configure_routes.py` explicitly lists
31 issue-184 targets across LIQ, CREDIT, DATA, MARKETS, REG and BEH. Tests check
that every method/path still exists. Connection `/test`, schema discovery,
sync and pull are operational, not configure targets. Database-direct has no
separate stored-credential `/validate`; Temenos and Markets do.

Before/after are two **policy eligibility assessments of the same snapshot**,
not observed HTTP outcomes or historical database dumps. The baseline records
the pre-cutover Analyst/Approver scalar floors, except organization signing
policies and reusable placements, which already require a scoped Org Owner
ACCOUNT/restricted binding. Scalar roles are used only for that explicitly
labelled baseline, never to establish configure authority.

After uses the shared binding evaluator, its current request-wide vetoes, and
whole-institution matching. Each active row must match all dimensions.
Bank targets use an exact bank resource; existing explicit organization-wide
bindings can cover those resources under the evaluator's standing contract.
New candidate grants name only that bank, module and restricted sensitivity.
Signing policy and reusable template targets require explicit organization
scope; a bank grant never qualifies for them.

The inventory includes active and inactive humans and service-user machine
principals. Machines remain denied on these human-only routes and receive no
invalid Configurator recommendation. Their existing grants still appear.
An existing grant's `effective` flag describes its **lifecycle window**, not
principal activation or route authorization.

Candidate rows are **not automatic grants or instructions to authorize every
listed principal**. They describe the exact missing sentence *if* an operator
chooses that human for that task. Review necessity, assignment-time SoD,
grantor/reason and validity dates through normal grant administration.
Configurator conveys no VIEW or APPROVE; distinct behavioral review authority
remains a separate grant and workflow.

Eligibility is not an execution permit. Object visibility, institution-type
entitlements, demo mode on legacy baseline routes, impersonation, credential
lifecycle, payload validity, maker/checker, step-up and other workflow context
can still refuse execution. No workflow condition is fabricated by this report.
Keep separate real HTTP and persisted-state evidence with each enforcement PR.
Update the explicit catalogue if later slices add or replace route targets.
