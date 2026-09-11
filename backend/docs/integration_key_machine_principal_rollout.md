# Bank-scoped integration-key rollout

This cutover makes every API Push credential a machine principal for one exact
institution. It is intentionally immediate default deny: every existing key has
`bank_id IS NULL`, no exact Integration Writer binding, and stops working when
the enforcing application is deployed. Do not infer a bank, backfill a binding,
or create a compatibility grant. Store dated inventory output and rotation
evidence with the deployment record, never in this repository.

## Affected routes

All four routes require one independently complete active binding for the
authenticated machine principal:

- `POST /api/v1/banks/{bank_id}/push-batches`
- `POST /api/v1/banks/{bank_id}/push-batches/{push_batch_id}/records`
- `POST /api/v1/banks/{bank_id}/push-batches/{push_batch_id}/commit`
- `GET /api/v1/banks/{bank_id}/push-batches/{push_batch_id}`

The binding must match the key's organization, exact institution,
`principal_type = 'machine'`, `role_bundle = 'integration_writer'`,
`module_scope = 'data'`, `sensitivity_scope = 'restricted'`, active lifecycle,
and `ingest` permission. Separate partial rows never compose. Human Analyst
grants do not satisfy machine ingest. A wrong-bank request returns 404.

## Pre-deployment inventory

Run this read-only query with the operator database role. It emits one row per
key and identifies the required action without inventing a production identity
or institution target.

```sql
WITH key_state AS (
    SELECT
        o.id AS organization_id,
        o.name AS organization_name,
        k.id AS integration_key_id,
        k.label,
        k.key_prefix,
        k.bank_id,
        k.service_user_id,
        u.email AS service_user_email,
        u.is_active AS service_user_active,
        u.authorization_version,
        k.created_at,
        k.last_used_at,
        k.revoked_at,
        EXISTS (
            SELECT 1
            FROM authorization_bindings AS b
            WHERE b.organization_id = k.organization_id
              AND b.principal_user_id = k.service_user_id
              AND b.principal_type = 'machine'
              AND b.role_bundle = 'integration_writer'
              AND b.institution_scope = 'institution'
              AND b.institution_id = k.bank_id
              AND b.module_scope = 'data'
              AND b.sensitivity_scope = 'restricted'
              AND b.status = 'active'
              AND b.revoked_at IS NULL
              AND b.valid_from <= CURRENT_TIMESTAMP
              AND (b.valid_until IS NULL OR b.valid_until > CURRENT_TIMESTAMP)
        ) AS has_exact_active_binding
    FROM integration_keys AS k
    JOIN organizations AS o ON o.id = k.organization_id
    LEFT JOIN users AS u
      ON u.id = k.service_user_id
     AND u.organization_id = k.organization_id
)
SELECT
    *,
    CASE
        WHEN revoked_at IS NOT NULL THEN
            'no feed action: key already revoked'
        WHEN bank_id IS NULL THEN
            'rotate: confirm exact bank, issue a new key, switch feed, verify, revoke old key'
        WHEN service_user_active IS NOT TRUE THEN
            'rotate: machine identity inactive'
        WHEN has_exact_active_binding IS NOT TRUE THEN
            'rotate: exact active machine binding missing or mismatched'
        ELSE
            'verify: key is structurally ready for bank-scoped enforcement'
    END AS operator_action
FROM key_state
ORDER BY organization_id, revoked_at NULLS FIRST, created_at, integration_key_id;
```

For every active row, record:

1. Organization and key ID/prefix/label.
2. Middleware owner and operator-confirmed target `BK-*`.
3. Newly issued key prefix and exact target bank.
4. Time the middleware switched and a push completed successfully.
5. Time the old key was revoked.

Never record the raw new key in the release record. Never repair a legacy key
by assigning a guessed bank. The supported remediation is new issuance, which
atomically creates the service identity, key, and exact machine binding.

## Deployment sequence

1. Run the inventory and obtain an explicit target bank plus middleware owner
   for every active key.
2. Schedule a feed maintenance window. There is no safe zero-downtime backfill:
   old credentials cannot be pre-authorized without inventing authority.
3. Apply migration `202609110051` first. It only adds nullable metadata, a
   tenant-safe foreign key, and an index; old application code continues to run.
4. Deploy the enforcing application. At this point every null-bank legacy key
   is denied before storage, enqueue, network, or database mutation.
5. For each confirmed bank, use an authorized Account administrator to issue a
   new key, switch that feed, and verify all required push calls.
6. Revoke the old key only after the replacement feed succeeds. Revocation
   atomically ends the credential, machine binding, and service identity.
7. Re-run the inventory and attach the before/after rows and verification times
   to the release record.

## Rollback position

This rollout has a schema change and does not have the simple image-only
rollback profile of the preceding Account-administration cutover.

### Reverting application code only

The `bank_id` column and machine bindings remain. Older application code ignores
both and hardcodes Analyst authority for every active integration key. A
code-only rollback therefore restores organization-wide push access to every
still-active legacy key **and** makes newly issued scoped keys organization-wide.
That may be useful during an incident, but it widens authority and must be an
explicit security decision. Freeze issuance during the rollback and inventory
all active keys before and after it.

Revoked keys remain revoked and inactive service identities remain inactive.
Reverting code does not recover a revoked raw secret.

### Migration downgrade

The migration downgrade is safe only before any bank-scoped key has been
issued. Once any row has a non-null `bank_id`, the downgrade refuses because
dropping the column would erase the institution target while older code accepts
active credentials organization-wide. There is no automatic safe downgrade
after issuance. Prefer a forward fix or a deliberate code-only rollback while
retaining the schema and inventory evidence.

### State that rollback cannot undo

- Raw keys already disclosed at issuance cannot be undisclosed or recovered.
- A revoked old key cannot be restored because only its hash remains.
- Machine-binding revocations and append-only audit evidence remain historical
  facts.
- Authorization-version increments and refresh-family invalidations are not
  reversed; affected identities must authenticate again where applicable.
- A code-only rollback does not narrow newly issued keys. It broadens them until
  enforcement is restored or they are revoked.
