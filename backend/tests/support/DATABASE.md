# Test database connections

`tests.support.database` owns test connection construction, including shared
application fixtures, area-specific tests and database tooling. The architecture
guard rejects direct construction everywhere else in tests.

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
on every managed PostgreSQL engine, including adopted application engines and async
engines' sync facades, and their native cursors. The transformation runs at native
cursor execution so raw SQL and ORM work use the same lock identity exactly once.
Extra pools and role/tenant variants in one schema retain the same lock identity;
these bank locks do not contend across independently owned schemas.
The hook is installed before checkout and does not depend on a schema prefix
or URL option ordering.

Namespacing matches only the exact native statements
`SELECT pg_advisory_xact_lock(%(key)s)` and
`SELECT pg_advisory_xact_lock_shared(%(key)s)`, including their compiled ORM
equivalents. Use another bind name, such as `probe_key`, for generic contention
probes that must retain the original lock identity. Other advisory-lock SQL is
unchanged.

Run the constructor guard with `uv run python scripts/test_database_guard.py`.
Direct constructors fail unconditionally. Only `tests/support/database.py` is
exempt; other support files are checked. The guard resolves imported and assigned
aliases with lexical scoping and reassignment, including generic factory calls.
It covers SQLAlchemy sync/async factories, psycopg sync/async connections and
native SQLite constructors. It does not attempt to interpret runtime-generated
imports or reflective calls.

## Prepared canonical books

The `canonical_book_baseline` marker opts a module into a separate synthetic
canonical-book database. It does not add a bank to `isolated_db`, the ordinary
shared application baseline, or any provisioning path. The seven measured
Markets, credit, ICAAP P2, ICAAP freeze/clone, capital-refusal and reporting-currency
modules select it explicitly.

`canonical_book_database` owns the ORM-created schema and an immutable book
summary for one pytest worker/session. Each case opens a fresh managed context,
connection, outer transaction and `create_savepoint` session factory. The API,
authentication and application session lookups all bind to that transaction;
clients, storage fakes and ORM objects stay function scoped. Every case rolls
back the outer transaction, closes its managed resources and compares row counts
and sorted row-value digests across every model table with the committed baseline.
The owner also checks the final case before dropping the schema.

Preparation helpers use `canonical_test_book(session)`: only the first request
in an opted-in test returns the saved summary (and reapplies the original tenant
context). Later requests rebuild through the real materializer. Calls outside
this fixture family also materialize normally. Tests of destructive reseeding can
always call `materialize_canonical_test_book` directly. `committing_db` continues
to select the existing real-commit fixture family; this marker never changes its
semantics or adds migration-installed RLS to an ORM-created schema.

`tests/support/test_canonical_book.py` exercises committed mutations followed by
an unchanged baseline, new connections and ORM objects, application-session
binding, real repeated materialization and value-based fresh-build equivalence.
Run the full affected modules in normal and reversed order and with
`-n 4 --dist loadfile`; keep the ordinary fixture-isolation, migrated RLS and
concurrency suites as their own gates.
