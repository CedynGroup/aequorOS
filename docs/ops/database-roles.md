# Database roles and deployment handoff

Engineering supplies the role foundation in migration `202610120096`, following
`202610110097`. Eric owns the deployment and credential changes. **Non-breaking;
deployment switch is a separate step.** Applying the foundation does not change
existing application URLs, ownership, credentials or BYPASSRLS roles.

## Target matrix

| Role                 | Connection purpose       | Allowed access                                                                                                                                            | Forbidden access                                                                                  |
| -------------------- | ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `aqs_owner`          | NOLOGIN object owner     | DDL and explicit migration work through SET ROLE                                                                                                          | Application connections                                                                           |
| `aqs_migrator`       | Deployment only          | SET ROLE `aqs_owner`; no automatic inheritance                                                                                                            | Runtime use, elevated attributes                                                                  |
| `aqs_api`            | Tenant API               | Tenant SELECT/INSERT/UPDATE/DELETE under forced RLS, audit append, filing lifecycle columns, enqueue/coalesce jobs and parse/assessment lifecycle updates | Schema DDL, ownership, audit or immutable evidence changes, job DELETE or worker-only job columns |
| `aqs_worker`         | Background jobs          | API tenant rights plus queue state changes under RLS                                                                                                      | BYPASSRLS, unrestricted cross-bank reads, audit or filed-content changes                          |
| `aqs_bi`             | Interactive BI           | SELECT on BI marts/dimensions and public jurisdiction/parameter reference tables under RLS; 10-second statement timeout                                   | Writes, operational queue or identity-table access                                                |
| `aqs_operator`       | Staff operations         | Dedicated cross-bank metadata views, introduced in the operator PR                                                                                        | Direct tenant-table access; financial inspection outside the governed #351 flow                   |
| `aqs_audit_verifier` | Independent audit checks | SELECT audit sources, chain entries and heads, including cross-bank verification                                                                          | Other tenant tables, writes or chain ownership                                                    |

No application role owns objects or holds SUPERUSER, CREATEDB, CREATEROLE,
REPLICATION or BYPASSRLS. There is no standing break-glass role. The historical
`aequoros_audit_owner` remains NOLOGIN and retains audit-chain custody; deployment
ownership has explicit membership in it. Its existing inheritance setting is
preserved. No application sequence grants are installed. The API's sole partition
function grant is `bi_ensure_month_partition(regclass,date)`: its definer pins the
search path and restricts API logins to the query-log parent in its own schema,
for the previous, current or next UTC month. It adds no table-level grants or
mart/drop-partition capability. Existing PUBLIC execution on non-definer trigger
functions remains unchanged.
Existing integrity triggers and restrictive policies remain authoritative for
completed runs, sealed registers and filed-content immutability (#511).

The API has column-scoped job UPDATE on `bank_id`, `payload`, `run_after`,
`status`, `started_at`, `completed_at`, `progress` and `error`: upload coalescing
and synchronous document parsing/assessment runs write these today. It cannot
delete jobs or change queue identity, retry counters or worker claimant fields.
Forced RLS checks the organization on both existing and new rows; the API's
restrictive bank-reference check also rejects INSERT/UPDATE targeting another
organization's bank. Banks within one organization share the tenant boundary.
Future functions created as `aqs_owner` have no PUBLIC EXECUTE default in any
schema; migrations grant execution explicitly.

The foundation provisions dormant roles; it does not authorize switching worker
or operator credentials. The worker PR adds tenant context to every job and
narrowly granted platform functions/views. The operator PR adds metadata views.
Both require the product owner's review. Bank-held encryption migrations
`202610220101` and `202610230103` now grant runtime bank-key registry access for
field/storage operations and administrator key setup. The exact privileges are
owned by the executable [grant manifest](../../backend/tests/db/database_role_grants.json);
the pre-tenant registry relies on explicit application scoping rather than RLS.
The separate, captain-reviewed pre-authentication follow-up remains required for
integration keys: a narrow SECURITY DEFINER function owned by `aqs_owner`, with a
pinned search_path and EXECUTE granted only to `aqs_api`, accepting a presented
key hash and returning only its organization, bank, scopes and status. That
follow-up must prove refusal of direct `integration_keys` access and other-bank
enumeration.

## Session timeout budgets

[Session cleanup and timeouts #518](https://github.com/CedynGroup/aequorOS/issues/518)
adds administrator configuration in `backend/scripts/database_session_timeouts.sql`.
Apply it after the role foundation and before switching credentials. It uses
`ALTER ROLE ... SET` only for dormant `aqs_*` roles; no historical deployment
role, password, grant or ownership changes. Reapplying it is safe. This is role
configuration rather than a schema migration, so the least-privilege migrator
needs no permission to administer runtime roles.

| Connection                               | Idle in an open transaction | Statement ceiling     |
| ---------------------------------------- | --------------------------- | --------------------- |
| API (`aqs_api`)                          | 10 minutes                  | 5 minutes             |
| Worker (`aqs_worker`)                    | 2 hours                     | 30 minutes            |
| BI (`aqs_bi`)                            | 5 minutes                   | 10 seconds by default |
| Migrations (`aqs_migrator`, `aqs_owner`) | Disabled                    | Disabled              |

The API idle limit releases locks from abandoned transactions. The
[backend API guide](../../backend/README.md#api-transactions-and-external-waits)
owns transaction boundaries and the remaining external-wait inventory.
Storage reads allow up to six 60-second attempts plus backoff, so the ten-minute
budget remains until those paths commit before external waits.
[Issue #559](https://github.com/CedynGroup/aequorOS/issues/559) tracks removing
transactions around external waits; review this budget when that lands or when
cross-service timeout/retry budgets increase. Active SQL is subject to the
statement ceiling, not the idle timer. The worker budget permits CPU, storage
and provider work between SQL calls: the default AI failover/retry reclaim
budget (`AISettings.stale_after_seconds`) is below two hours. Before raising
provider retry/timeouts or running a job with more than two hours between
database calls or a SQL statement exceeding 30 minutes, review and raise the
worker role budgets explicitly. Do not route background work through the API
pool. No total transaction or idle-outside-transaction timeout is added.

BI keeps its existing interactive ceiling. The query executor overrides it
inside a savepoint for the configured interactive/export budget (exports
default to 120 seconds), then restores the role default. The five-minute BI idle limit protects an
abandoned read transaction without changing those query budgets.

`backend/app/db/session.py` supplies connection startup options for legacy
usernames, separately for API/operator, worker and BI pools, including when all
URLs still share one login. This does not alter that role's defaults or other
clients. Recognized new role usernames use their administrator-configured role
defaults. Existing URL options, including `search_path`, are preserved. Alembic
explicitly disables both limits on its own connection even when it uses an old
runtime login, and retains its explicit `SET ROLE aqs_owner` handoff. SQLite
has no timeout configuration and retains its shared engine behavior.

After configuration or credential changes, drain/reconnect the affected pools;
role defaults apply to new logins. Eric owns the deployment switch. Record
`rolconfig` before applying the script so rollback can restore the exact prior
values through administrator tooling; restoring URL secrets alone does not
change new-role defaults. Do not change or reset legacy role defaults.

Validate fresh API, worker, BI and migration logins with `SHOW
idle_in_transaction_session_timeout` and `SHOW statement_timeout`. Observe the
pool identities in `pg_stat_activity`. Native PostgreSQL 17 tests in
`backend/tests/db/test_database_timeouts.py` prove fresh-login budgets using
disposable probe logins with the configured role settings and service privileges
(PostgreSQL role settings are not inherited via membership). They also prove
real API idle termination/lock release using a short
transaction-local probe limit, legacy connection budgets, a 65-second worker
statement and BI export overrides above ten seconds. Native regressions in
`backend/tests/db/test_bi_export_transactions.py` prove through `pg_stat_activity`
that slow BI statements and compiler probes hold no API transaction, and that
delivery audit/query-log/job writes commit atomically. Document deletion
concurrency and role-timeout coverage is linked from the backend API guide above.

## Migration and local development order

1. On deployment, an administrator prepares the new roles using
   `backend/scripts/database_roles_202610120096.sql`, without local password SQL.
2. For the existing deployment actor, prepare ADMIN, INHERIT and SET membership
   in `aqs_owner` and `aequoros_audit_owner`, and grant `aqs_owner` CREATE/USAGE on
   the application schema. This administrative preparation is distinct from
   `aqs_migrator`'s SET-only membership. On an existing database, preserve legacy
   ownership until Eric's planned handoff.
3. Apply the chain through `202610120096` using the existing deployment actor.
   Subsequent deployment connections as `aqs_migrator` explicitly SET ROLE
   `aqs_owner` in Alembic, so newly created objects belong to the NOLOGIN owner.
4. Validate grants and real-login refusal tests before changing any credentials.
5. Deploy the worker and operator compatibility changes before their respective
   credential switches. They support both existing and least-privilege roles.
6. Deploy the captain-reviewed API pre-authentication follow-up after the
   operator PR. **The API credential switch is blocked until that PR is live.**
   `aqs_api` cannot yet authenticate integration keys; bank-key registry grants
   follow the [current matrix and prerequisites](#target-matrix).

Native development uses `backend/scripts/local_services.py` to prepare the roles
on its owned PostgreSQL 17 cluster. Docker's `risk-migrate` runs
`python -m scripts.bootstrap_database` before starting services, including with
an existing local volume. `backend/scripts/bootstrap_db.sh` runs the same local
bootstrap against a running local service. No tenant financial data is seeded.
It requires `APP_ENV=local` (the default) and an administrator `DATABASE_URL`
whose host is `localhost`, `127.0.0.1` or `risk-postgres`. The shell wrapper defaults
to the bundled local PostgreSQL service at `127.0.0.1:15432`.
The bootstrap pins Alembic to the validated local URL, including when a shell
has a separate migration URL configured. The local SQL sets clearly identified
development passwords equal to role names;
never execute `database_roles_local.sql` on a deployment. Existing DATABASE_URL
and worker/operator fallbacks keep working. All runtime pools identify API,
worker, BI and operator connections separately through `application_name`.

For externally supplied test services, an administrator must prepare the versioned
foundation SQL, `backend/scripts/database_session_timeouts.sql`, and the test actor's
ADMIN, INHERIT and SET owner memberships before
running the migration suites. Real-login role proofs in
`backend/tests/db/test_database_roles.py` also require a superuser
`POSTGRES_ADMIN_URL` (or a superuser `TEST_DATABASE_URL`) on the same disposable
test database to create and remove probe logins. The local runner and CI prepare
these prerequisites; never use the primary database.

## Eric's deployment runbook

### Preparation and verification

Take the usual restorable backup and record current owners, role attributes,
ACLs and URL-secret versions. Use an administrator credential in the maintenance
session only. Apply the versioned foundation SQL, `backend/scripts/database_session_timeouts.sql`,
and the schema migration; do
not copy local development credentials. Verify the runtime attributes:

```sql
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
       rolbypassrls
FROM pg_roles WHERE rolname LIKE 'aqs_%' ORDER BY rolname;
SELECT pg_get_userbyid(relowner), relname
FROM pg_class WHERE relnamespace = 'public'::regnamespace;
SELECT rolname, rolconfig FROM pg_roles WHERE rolname IN ('aqs_api', 'aqs_worker', 'aqs_bi', 'aqs_migrator', 'aqs_owner');
```

Inspect PUBLIC and inherited grants before activation: PostgreSQL grants cannot
subtract a privilege inherited from PUBLIC or another role. Schema CREATE,
database CREATE/TEMPORARY and PUBLIC function execution must be reviewed in the
deployment maintenance plan, with explicit replacement grants for any existing
process that requires them. This foundation does not revoke deployment grants.
Move ordinary application objects to `aqs_owner` only after the deployment actor
can SET ROLE it; preserve `aequoros_audit_owner` on audit objects/functions.
Future migrations must grant new-table access explicitly; no automatic blanket
application grants are installed.

### Credentials, IAM and rotation

Create a distinct secrets-manager entry for each login role. Generate credentials
in the secrets manager; supply passwords to PostgreSQL through the deployment's
secure administrator tooling, without shell history or PR logs. The owner role
has no credential. Only the deploy job receives the migrator secret. Map API,
worker, BI, operator and audit verifier secrets to their own connections; never
share an API password with another process. If the managed database supports IAM
DB authentication, use separate workload identities and narrowly scoped connect
permissions for each DB username, TLS verification and fresh tokens on new
connections. IAM configuration and token renewal belong to deployment tooling.

Rotate each role separately: issue a new secret version, update the role's
credential, restart/drain its pools, verify fresh connections and disable the old
secret version. Schedule password rotation with the supported overlap/reconnect
mechanism; a pooled session may survive a password change. IAM tokens also do
not replace pool reconnection and workload-identity rotation.

### Worker and operator activation

After the reviewed compatibility PRs are deployed, switch their respective URL
secrets to `aqs_worker` and `aqs_operator`. Verify job completion in two tenants,
platform jobs, console metadata and refusal of direct financial access. Check:

```sql
SELECT application_name, usename, count(*) FROM pg_stat_activity
WHERE datname = current_database() GROUP BY application_name, usename;
SELECT rolname, rolbypassrls FROM pg_roles
WHERE rolname IN ('aqs_worker', 'aqs_operator');
```

After the new connections are healthy and old pools drained, Eric removes
BYPASSRLS from the old deployment roles or retires their credentials. No
engineering migration alters those existing role attributes. **Do not switch the
API credential yet:** its switch remains subject to the
[API pre-authentication prerequisite](#target-matrix). Verify bank-key lookup
under the current grant manifest as well. Keep the existing API URL secret until
that gate passes.

### Break-glass and rollback

Break-glass is issued just in time with two independent approvers, an incident
identifier, a narrowly scoped purpose and a short expiration. Record both
approvals and every privileged operation in the incident/audit system. Prefer
explicit grants for the incident's objects; any RLS exception requires that
same approval and documented scope. Eric creates the temporary login through
secure tooling, limits network access, sets expiry and timeouts, then terminates
its sessions and drops it at incident closure. Never install a standing
break-glass account or embed its credentials in application configuration.

For a failed credential switch, restore the recorded URL-secret version and
restart/drain only the affected service pools. Keep the compatibility code live.
Verify previous job/console behavior before retiring the unsuccessful secret.
Restore recorded ACLs/owners only under the maintenance plan; do not relax audit
or filing integrity guards. The foundation downgrade revokes its own runtime
schema/table grants and verifier policies, but never drops cluster-wide roles,
changes existing deployed credentials or restores BYPASSRLS. Disabling a new
credential is Eric's separate administrator action. Coordinate later migrations
before downgrading so their grants are not accidentally removed.
