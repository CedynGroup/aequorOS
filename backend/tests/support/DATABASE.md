# Test database connections

`tests.support.database` owns test connection construction. The first migration
stage leaves existing application fixtures and area-specific tests in place;
`tests/architecture/test_database_connection_allowlist.json` records their direct
constructor calls. The architecture suite rejects new calls and stale entries.
Remove entries as each test area moves to this API. Once the inventory is empty,
remove the allowlist and make strict mode the default.

Use `test_db_connections` for an **empty**, per-test PostgreSQL schema when
`TEST_DATABASE_URL` is set, or a temporary SQLite file otherwise. It never seeds
financial data. Use `db_session` and `db_client` when testing the existing shared
application baseline. A session can bind to an outer transaction opened through
the same factory:

```python
with test_db_connections.connection() as connection:
    with connection.begin():
        with test_db_connections.session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            ...
```

Binds from another factory are rejected so a session cannot escape its owning
schema. The shared application fixture family remains on its existing lifecycle
until its migration stage.

The factory provides:

- `session()` and `session_factory()` for ORM work. Session factories propagate
  tenant info to the application's transaction-local RLS listener. Sessions are
  closed at the owning fixture's teardown, including sessions created directly
  from a factory.
- `connection()` for SQLAlchemy SQL. It rolls back unfinished work on exit.
- `raw_connection()` for psycopg SQL. It rolls back on exit and returns the
  underlying connection to the managed pool.
- `engine()` for an independent pool, including concurrent connections. These
  pools use the same database, schema and checkout hooks. Connections and pools
  are closed at teardown. Tests should still use context managers so resources
  are released as soon as their work completes.
- `migration_engine()` for direct migration-function tests. `database_url` also
  carries the schema in its startup options. Alembic consumers that create their
  own engines still need migration to the support layer; passing this URL alone
  does not install engine hooks.
- `async_engine()` as an async context manager. Its pool is disposed before the
  using event loop exits. Use `async with engine.connect()` for connections.

Set explicit tenant and role defaults without leaving the test's schema:

```python
from dataclasses import replace

tenant = test_db_connections.with_context(
    replace(
        test_db_connections.context,
        organization_id="OR-TEST",
        bank_id="BK-TEST",
        role="aqs_api",
    )
)
with tenant.connection() as connection:
    ...
```

The role must already exist and the login must be allowed to assume it.
Checkout restores role, schema and both tenant GUCs, including after a previous
user changed them and committed. Raw tenant defaults survive commit/rollback.
ORM transactions use `session.info` as the authoritative tenant, matching the
application. A missing tenant explicitly means unscoped access, not an inherited
tenant from an earlier pool user.

For a module-scoped or standalone fixture, use
`isolated_database(os.environ["TEST_DATABASE_URL"], ...)`. It owns a generated
schema and drops it after disposing all test engines. SQLite callers supply a
path in pytest's temporary directory. PostgreSQL factories reject construction
without an explicit disposable schema. Never pass a production database URL.

`hooks=(install_hook, ...)` on `isolated_database` or
`TestDatabaseConnections` installs each engine hook before its first checkout,
including extra pools, context variants, migration engines and async engines'
sync facades. The encryption stack's schema-scoped lock isolation is still a
pending integration; this foundation does not claim to fix that CI failure.
Its implementation must become a default shared hook when that stack lands.

Run the constructor guard with `uv run python scripts/test_database_guard.py`.
`--strict` rejects all legacy constructors. The temporary allowlist matches
counts by path and resolved constructor name, so unrelated line movements do
not change it. It covers imported and assigned aliases, SQLAlchemy sync/async
factories and psycopg sync/async connections. It does not attempt to interpret
runtime-generated imports or reflective calls.
