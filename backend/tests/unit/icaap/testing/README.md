# Frozen report inputs and current annexes

Use `current_annex_repository` to construct `InMemoryCurrentAnnexRepository` only
when a test begins **after authorized report and annex acquisition**. Supply the
already-resolved current version for the complete organization, bank, return code,
reporting date and basis key. Missing keys mean no current annex; duplicate keys
are refused. Inputs are copied and returned versions are immutable.

Run the complete real validator against a coherent synthetic frozen report. A
clean, non-empty baseline must pass before changing the field under test. The fake
supplies domain answers; it does not prove that those answers were selected safely.
Never replace the rule or service being tested, or use mock calls as persistence
proof.

The real `PostgresCurrentAnnexRepository` receives the caller's managed SQLAlchemy
session. It composes above the shared connection API; it does not own connection
construction or bypass its tenant, role or encryption context. Unit tests construct
no connection resources at all. Do not add a fake mode to the connection API or
extend this fake into a SQLAlchemy session, unit of work or generic model store.

Keep PostgreSQL for filtering, ordering, joins, current-version selection, bank or
organization isolation, migrations, catalogs/backfills, roles/GUCs/grants,
concurrency/locks/timeouts, encryption/key persistence/rotation/recovery, atomic
side effects, jobs and immutable/audit/seal history. The complete freeze-to-validate
control and the adapter's real SQL/isolation matrix remain integration-owned.

The [backend test guide](../../../README.md#run-tests) owns the protected database
contract catalogue, fake-fixture restrictions, required backend and execution
checks, and census refresh procedure.
