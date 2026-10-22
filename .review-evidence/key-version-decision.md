# Revised captain decision: bank key versions

R1 and review-1: remove encryption-time key-row/advisory locks; retain locking
only for version/rotation bookkeeping. Resolve one current version per database
unit of work, cache authenticated material only until that unit ends, and reuse
it across field, bulk and object access. Both download siblings use the request
session. Rotation publishes a new immutable version; existing versions remain
readable and separate maintenance performs mutable-field re-encryption.

R7: published ciphertext refers only to a committed version. Business rollback
must not invalidate an overwritten push manifest, including publication followed
by stat failure. No per-object envelope is inserted in the business transaction.

R8: unwrap once per required version per unit of work, including bounded bulk
loads; a fresh unit must contact KMS and fail closed on revocation.

Preserve R2–R6, historical audit bytes, immutable-history preconditions/guards,
and numeric-only connected-key BI (follow-up issue 595). Synthetic data/providers
and isolated PostgreSQL only. The design note was updated before implementation.

The outer executor owns the design/implementation stack and publication. Keep
the revised design note in its independently reviewable documentation layer;
implementation, migrations and executable evidence belong in the implementation
layer. No pipeline controls, pushes or PR writes are performed in this phase.

Cutover preservation: optional plaintext writers retain only the bank-row
registration guard. No key-row/advisory lock is acquired by encryption; stale
no-key sessions refuse instead of writing plaintext after registration. This
guard preserves the immutable-history precondition without serializing any
connected-key field, object or download operation with rotation.

## Focused verification

- Final source: 109 tests passed in 55.55 seconds using the isolated native
  PostgreSQL service and synthetic LocalKeyProvider/Moto storage. Scope:
  transactional encryption, key-rotation concurrency, bank storage encryption,
  preloaded envelopes, field encryption/backfill, migrated field guards, operator
  key management, and one schema upgrade/downgrade round trip.
- Final test-only simplification: transactional encryption rerun, 20 passed in
  16.51 seconds, including all eight ingestion/push/export/attachment rotation
  cases, both download siblings, published-manifest stat failure plus rollback,
  cache call counts/lifetime, uncommitted-version publication refusal, resumable
  re-encryption, and both stale optional-session cutover cases.
- Ruff passed for all 15 changed Python files; `git diff --check` passed.
- The wider focused run emitted two AWS SDK StreamDecryptor footer warnings
  during negative-path tests. No test failures occurred.
- No full repository test/lint/typecheck, push, PR, CI or pipeline control ran.
  Authoritative remaining gates and stack publication belong to the outer executor.

Focused command (backend working directory):

```sh
env -u TEST_DATABASE_URL -u REAL_DATA_DATABASE_URL uv run python scripts/local_services.py run --mode native --postgres --role-admin -- uv run pytest -q --tb=short tests/storage/test_transactional_encryption.py tests/storage/test_key_rotation_concurrency.py tests/storage/test_bank_encryption.py tests/db/test_bank_key_reader_lock.py tests/core/test_bank_field_encryption.py tests/db/test_bank_sensitive_fields.py tests/operator/test_bank_encryption.py tests/db/test_postgres_migrations.py::test_postgres_migrations_create_workflow_constraints_and_rls
```

The initial command selected a nonexistent round-trip test and collected no
tests; the corrected command above produced the reported passing result.
