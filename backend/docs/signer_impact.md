# Signer cutover impact

`scripts/authorization_signoff_impact.py` inventories the people who need the
new signing authority before enabling or expanding Signer enforcement. It does
not create grants, approve grant requests, change routes, or exercise a signing
ceremony.

Run from `backend/` with an explicitly authorized, read-only connection:

```sh
uv run python -m scripts.authorization_signoff_impact \
  --organization OR-XXXXXXXX \
  --output-dir /access-controlled/path/YYYYMMDD-signoff-impact
```

The connection comes from `--database-url`, then `WORKER_DATABASE_URL`, then
`DATABASE_URL`. Do not paste its credentials into a shell command or shared
log. Omitting `--organization` inventories every tenant visible to the reader.
Without `--output-dir`, the command prints the combined private report to
standard output. Each PostgreSQL tenant assessment runs in one read-only,
repeatable-read transaction and sets the tenant RLS context.

The directory contains `signoff-before.json`, `signoff-after.json`, and
`signoff-exact-grants.json`. Files are created with mode `0600`, new directories
use `0700`, and existing evidence is never overwritten. Keep the principal and
binding IDs private. The report never exports email addresses, credentials,
raw grant reasons, secrets, or tokens. Exit status 2 means input failed or an
evaluate decision failed; do not treat partial results as a complete assessment.

## Inventory and assessment boundary

For every visible human and machine principal at every bank, the report compares
legacy signing eligibility with the proposed exact-bank Signer binding. The REG
cohorts include ordinary return preparers, preparer/witness slots, approver or
executive/Board slots, and ICAAP-specific CAP/confidential authority. Pending
current-cycle named `PackageSignatureRecipient` rows are inventoried separately;
the actual named signing role distinguishes preparer, approver, and Board slots.
Pending `EnterpriseStressSignoff.attestation_recipient_user_id` rows identify
stress attesters. Synthetic `e2e.<profile>` and `journey.<profile>` identities
are explicitly tagged rather than inferred from grants.

Legacy REG eligibility uses the former source-family VIEW plus EDIT (preparer),
APPROVE (approver/Board), or VIEW (witness) permissions. Stress eligibility uses
the former scalar Approver role plus RISK/confidential VIEW. This is an impact
assessment, not proof of HTTP success or of a complete historical obligation:
named-but-unauthorized recipients therefore remain visible. Routing,
maker-checker separation, source visibility, key custody, fresh step-up, and
unchanged digests still execute independently.

After cutover, a signer requires one effective **exact institution**, whole-book,
module/family-sensitivity REG/restricted or RISK/restricted `sign_off` binding.
Organization-wide, wrong-bank, ineffective, paused, suspended, and expired
bindings do not qualify. Machines and inactive humans cannot sign. Signer does
not imply source READ or checker APPROVE.

`signoff-exact-grants.json` lists operator-review candidates only. It separates:

- the exact-bank Signer grant;
- source-family VIEW (ordinary REG uses REG/restricted; ICAAP uses
  CAP/confidential; stress uses RISK/confidential with the Restricted Signer
  grant); and
- distinct REG/restricted exact-bank APPROVE for checker slots where missing.

The relevant routes are the package attestation preview, step-up, certify, and
certify-and-send routes, plus enterprise-stress signoff step-up and attest.
The existing `authorization_access_impact.py` dashboard projection remains the
full-access report; this sibling adds the signing-route deny-impact and grant
inventory without changing that response contract.
