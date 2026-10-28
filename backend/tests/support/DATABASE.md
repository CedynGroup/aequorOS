# Test database connections

`tests.support.database` owns test connection construction. Application fixtures,
area-specific tests and database tooling use this API. The constructor allowlist
is empty; the final stack layer removes it and makes strict enforcement the default.

Use `isolated_db` for an **empty**, per-test PostgreSQL schema when
`TEST_DATABASE_URL` is set, or a temporary SQLite file otherwise. It never seeds
financial data. Use `db_session` and `db_client` when testing the existing shared
application baseline. A session can bind to an outer transaction opened through
the same factory:

```python
with isolated_db.connection() as connection:
    with connection.begin():
        with isolated_db.session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            ...
```

Binds from another factory are rejected so a session cannot escape its owning
schema, including per-call bind overrides and reconfigured session factories.
Multiple-bind sessions are outside this API. The shared application fixtures adopt the cached application engine
before its first checkout. They retain engine identity, the outer rollback
transaction, savepoints and the separate resettable committing database.

SQLite connections enable foreign-key enforcement on every checkout, so local
migration fixtures must define the referenced primary or unique keys.

The factory provides:

- `session()` and `session_factory()` for ORM work. Session factories propagate
  tenant info to the application's transaction-local RLS listener. Sessions are
  closed at the owning fixture's teardown, including sessions created directly
  from a factory.
- `connection()` for SQLAlchemy SQL. It rolls back unfinished work on exit.
- `raw_connection()` for psycopg SQL. It rolls back on exit and returns the
  underlying connection to the managed pool. `autocommit=True` is explicit and
  restored on return. Plain `postgresql://` URLs use psycopg 3 for raw borrowing.
- `sqlite_connection()` for native SQLite SQL and error types. It uses the same
  managed pool and checkout hooks, and rolls back unfinished work on return.
- `engine()` for an independent pool, including concurrent connections. These
  pools use the same database, schema and checkout hooks. Connections and pools
  are closed at teardown. Tests should still use context managers so resources
  are released as soon as their work completes.
- `migration_engine()` for direct migration-function tests. `database_url` also
  carries the schema in its startup options. Use `install_migration_engines`
  below for engines created by Alembic's environment; a URL alone does not
  install engine hooks.
- `async_engine()` as an async context manager. Its pool is disposed before the
  using event loop exits. Use `async with engine.connect()` for connections.

Existing schema fixtures can register construction with their own pytest scope:

```python
engine = managed_engine(request, database_url, schema=schema_name)
factory = managed_session_factory(request, engine, expire_on_commit=False)
```

These functions register cleanup on the supplied `request`, whether its fixture
is function-, module- or session-scoped. URL and driver startup options supply
schema, role and tenant defaults. An explicit `schema` can select a namespace
before bootstrap creates it. `administrative=True` supports schema bootstrap and
cluster-role probes without a tenant schema; absent an explicit or startup
schema, their search path is pinned to `pg_catalog`. It does not grant any
database privileges.

For an existing schema outside a pytest request's lifecycle, use
`database_connections(database_url, schema=schema_name)` as a context manager.
It owns connection resources without creating or dropping the schema.

The legacy read-only and outer-rollback data fixtures use an explicit
`default_schema="public"` fallback only when their URL does not name a schema.
URL startup options take precedence, so synthetic callers keep their disposable
schema. Their read-only and rollback safeguards still apply. Validate these paths
with synthetic databases; do not run the opt-in primary-data suites for fixture changes.

`managed_session_factory` preserves SQLAlchemy's autoflush and expiry defaults;
`TestDatabaseConnections.session_factory` defaults both to false for application
sessions. Both accept explicit options (including a custom `Session` subclass via `class_`),
preserve explicit tenant info, and close
sessions at their owner's teardown. Factories require an engine created by this
support layer, or explicitly adopted before its first checkout. An owner can
`adopt_engine(engine)` when the application must construct the engine itself.
For a cached application engine returned by a real production factory, use
`adopt_application_engine(request, engine)` before its first checkout. Existing
managed engines keep their original fixture lifetime; an unowned pool receives
shared hooks and request-owned teardown. This preserves factory and cache behavior.

Use `managed_session_factory(None, engine)` only within an explicit owner
lifetime; pytest fixtures normally supply their request for earlier cleanup.
The database owner weakly tracks connections, sessions and raw pool borrowers.
When supplied, the pytest request also retains each session's close method as a
finalizer until that request's teardown. Still-live resources close before pool
disposal, including raw contexts left active at owner teardown.

Before calling Alembic in a schema fixture, install its engine factory:

```python
install_migration_engines(monkeypatch, engine)
command.upgrade(config, "head")
```

The patch belongs to the fixture's existing `MonkeyPatch` lifetime. Alembic still
resolves its real migration URL and driver settings, including migration role
and timeout options. Every pool it opens receives the shared hooks and is owned
by the supplied engine's factory. A different database or schema is refused
before connecting.

Tests that exercise real application factories can call
`install_application_engines(monkeypatch, engine)` before invoking them. This
routes the real factory's constructor through ownership before an internal first
checkout, preserving its URL, driver options, timeout budgets and cache identity.
It inherits the supplied engine's hooks and lifetime and refuses another database
or schema. Role suites can use `managed_application_engines` with their
`administrator` fixture. The shared committing application fixture installs this
bridge automatically. Tests that only inspect a never-connected pool can adopt
it explicitly with `administrative=True` without changing its original URL.

Set explicit tenant and role defaults without leaving the test's schema:

```python
from dataclasses import replace

tenant = isolated_db.with_context(
    replace(
        isolated_db.context,
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
path in pytest's temporary directory. PostgreSQL factories require an explicit
disposable schema, except for the administrative probes described above. Never
pass a production database URL.

Backup/restore tests need database-level isolation. Use
`isolated_postgres_database(explicit_test_cluster_url)` with an isolated cluster
login that can create databases. It generates a unique database, owns its public
namespace, closes all source pools and drops the database on exit. The drill test
retains the tooling's protected-cluster check before provisioning. Its optional
cluster URL must point at a disposable test cluster and all source rows are synthetic.

`hooks=(install_hook, ...)` on `isolated_database`, `database_connections` or
`TestDatabaseConnections` installs each engine hook before its first checkout,
including extra pools, context variants, migration engines and async engines'
sync facades. Encryption advisory locks receive the owning schema's namespace
on every PostgreSQL engine, including adopted application engines and async
engines' sync facades. Extra pools and role/tenant variants in one schema retain
the same lock identity; independently owned schemas cannot block each other.
The hook is installed before checkout and does not depend on a schema prefix
or URL option ordering.

Run the constructor guard with `uv run python scripts/test_database_guard.py`.
`--strict` rejects all legacy constructors. The temporary allowlist matches
counts by path and resolved constructor name, so unrelated line movements do
not change it. Only `tests/support/database.py` is exempt, including in strict
mode; other support files are checked. It covers imported and assigned aliases
with lexical scoping and reassignment, SQLAlchemy sync/async factories and
psycopg sync/async connections. It does not attempt to interpret runtime-generated
imports or reflective calls.
