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

## Executable ownership guard

`scripts/database_contracts.json` records exact protected identities with grouped
reasons. Changes to that reviewed catalogue are deliberate changes to a database
contract, not a way to lower a CI count floor. A moved or renamed case must retain
its integration guarantee and update its protected identity in the same review.

The existing suite plugin rejects unit ownership of protected cases, direct or
indirect requests for the standard fake fixture, and database resource fixtures
in unit tests. The fake is also forbidden under database, storage, API and identity
test paths. Full CI collection rejects stale protected identities. Selected
protected cases require a PostgreSQL backend in CI (or with
`CI_REQUIRE_DATABASE_CONTRACTS=1`), and skipping one fails its execution.

The catalogue explicitly identifies the existing primary-data opt-in cases.
Their integration ownership and fake prohibition always apply; execution remains
conditional on their existing `REAL_DATA_DATABASE_URL` profile. This change never
supplies that URL or turns primary data into a CI prerequisite. When explicitly
enabled, those cases also require PostgreSQL and may not skip their guarantees.

The existing partition verifier checks actual JUnit results: every selected
protected case executes once, successfully and without skips, in its Postgres
owner. Existing schema/lock/suite/storage ownership and full-tree disjoint coverage
remain intact. Existing unit connection refusal also covers inherited Python
children; a parent monkeypatch alone does not establish subprocess isolation.

After changing tests, record observed ownership and refresh the exact-test
catalogue as described in [the test guide](../../../README.md#run-tests). Keep the
observation/JUnit artifacts and compare full collection with all execution owners.
