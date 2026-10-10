"""The BI performance gate (``docs/bi.md`` §Verification "Benchmark", X-9).

What it does, in four phases, against a DISPOSABLE Postgres you name on the
command line:

1. ``generate`` — the existing 10-year history simulator (``data/simulator``)
   writes monthly parquet panels. Nothing is invented here: the panels are the
   same ones ``app/services/history_loader.py`` reads.
2. ``ingest`` — every panel month is pushed through the **Data Engine's own
   push path** (``POST /banks/{id}/push-batches`` → ``/records`` → ``/commit``,
   the three calls ``scripts/ingest_push.py`` makes) with a real bank-scoped
   ``aeq_live_…`` integration key. No canonical row and no ``bi_*`` row is ever
   written directly: a benchmark that seeds tables measures a shape production
   never has, and this repository's standing order is that every data point
   enters through the Data Engine.
3. ``build`` — ``mart_builder.refresh_bank_as_of`` per date, timed per scope
   from the builder's own ``bi_mart_builds.row_counts['elapsed_ms']``, then one
   re-run of the last date to time the unchanged-fingerprint skip.
4. ``query`` — three workload classes through the product's own HTTP routes
   (``POST /bi/query``, ``POST /bi/grid``, ``POST /bi/drill``), so what is timed
   includes authorization, the compiler, the executor's read-only/timeout
   savepoint and the query log — not a hand-built select.

Why the tenant is scaffolded and not seeded
-------------------------------------------
Ingestion requires the institution to exist (``_get_bank_or_404``) and there is
no bank-creation route in the product, so the bank, its organization, its
principal and its governed registers come from the hermetic fixture
``tests/fixtures/canonical_bank_fixture.py`` — the same seam
``scripts/e2e_bootstrap.py`` uses. That fixture writes **no canonical position,
GL or counterparty row**: the whole book under measurement arrives through the
push API.

Safety
------
``DATABASE_URL`` is blanked at import and the target URL must be passed with
``--database-url``; a URL matching the deployment's ``DATABASE_URL``,
``WORKER_DATABASE_URL``, ``REAL_DATA_DATABASE_URL`` or ``LIVE_DATA_DATABASE_URL``
is refused. The connecting role must be ``NOSUPERUSER NOBYPASSRLS``: a benchmark
run under a BYPASSRLS role measures queries that skip the tenant policy and is
optimistic in exactly the wrong direction, so the run refuses to start, and
:func:`prove_rls_enforced` re-proves it empirically against the built marts
before any number is reported.

Usage
-----
::

    # a disposable instance (see .ai/BI_TEST_MATRIX.md for the recipe)
    initdb -D /tmp/bi_bench/data -U postgres --auth=trust
    pg_ctl -D /tmp/bi_bench/data -o "-p 5498" start
    psql -p 5498 -U postgres \
      -c "CREATE ROLE bi_owner LOGIN PASSWORD 'bi' NOSUPERUSER NOBYPASSRLS CREATEDB CREATEROLE;"
    psql -p 5498 -U postgres -c "CREATE DATABASE bi_bench OWNER bi_owner;"
    DATABASE_URL=postgresql+psycopg://bi_owner:bi@127.0.0.1:5498/bi_bench \
      uv run alembic upgrade head

    uv run python scripts/bi_benchmark.py \
      --database-url postgresql+psycopg://bi_owner:bi@127.0.0.1:5498/bi_bench \
      --dates 60 --repeats 7 --report ../.ai/bi_recon/t11_measurements.md

    pg_ctl -D /tmp/bi_bench/data stop     # and free the port

``--phase`` runs one stage at a time (the phases are cumulative and each one
leaves its state in the database), which is what makes a re-run of the query
workload cheap after a long ingestion.
"""

from __future__ import annotations

import os

# IMPORT-TIME guards, before any ``app.*`` import: ``backend/.env`` points at
# the primary database and sets ``RUN_INPROCESS_WORKER=1``, so an unguarded
# import would start a worker thread that polls the shared ``jobs`` table. Same
# reasoning, same three variables, as ``tests/conftest.py``.
os.environ["RUN_INPROCESS_WORKER"] = "0"
os.environ.setdefault("_BI_BENCHMARK_INHERITED_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ["DATABASE_URL"] = ""
os.environ["WORKER_DATABASE_URL"] = ""

import argparse  # noqa: E402
import hashlib  # noqa: E402
import importlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import statistics  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator, Sequence  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

_ONE_DAY = timedelta(days=1)

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
for _path in (str(_BACKEND), str(_REPO)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# --- pure helpers (unit-tested in tests/services/bi/test_benchmark_helpers.py) ------------

#: Query classes, and the target each is judged against. The numbers are
#: ``docs/bi.md`` §Verification "Benchmark"; they are printed beside every
#: measurement so a reader never has to do the arithmetic.
TARGET_AGGREGATE_P95_MS = 200
TARGET_FACT_P95_LOW_MS = 1_000
TARGET_FACT_P95_HIGH_MS = 4_000

#: The three thresholds that would move the architecture to a columnar fallback
#: (DuckDB over Parquet on a local volume). Evidence-gated: the fallback is
#: recommended only when a measurement crosses one.
COLUMNAR_EXPLORE_P95_MS = 5_000
COLUMNAR_FACT_ROWS = 250_000_000
COLUMNAR_BUILD_SECONDS = 15 * 60

CLASS_AGGREGATE = "aggregate"
CLASS_FACT = "fact_grain"
CLASS_EXPLORE = "explore"


def percentile(values: Sequence[float], fraction: float) -> float:
    """The ``fraction`` percentile by nearest-rank, on an unsorted sequence.

    Nearest-rank rather than an interpolating estimator on purpose: with the
    handful of repeats a benchmark run can afford, an interpolated p95 reports a
    latency that was never observed. This returns one of the measurements.
    """

    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * fraction // 1))  # ceil, without float error
    return ordered[int(rank) - 1]


def verdict(measured_ms: float, *, query_class: str) -> str:
    """``pass`` / ``fail`` for one class's p95, or ``no target`` where there is none."""

    if query_class == CLASS_AGGREGATE:
        return "pass" if measured_ms < TARGET_AGGREGATE_P95_MS else "FAIL"
    if query_class == CLASS_FACT:
        # The spec states a BAND, not a ceiling: below it is better than the
        # target, not a failure, so only the upper bound can fail.
        return "pass" if measured_ms <= TARGET_FACT_P95_HIGH_MS else "FAIL"
    if query_class == CLASS_EXPLORE:
        return "pass" if measured_ms <= COLUMNAR_EXPLORE_P95_MS else "FAIL"
    return "no target"


def target_text(query_class: str) -> str:
    """The target for ``query_class``, in the words the spec uses."""

    if query_class == CLASS_AGGREGATE:
        return f"p95 < {TARGET_AGGREGATE_P95_MS} ms"
    if query_class == CLASS_FACT:
        return f"p95 {TARGET_FACT_P95_LOW_MS / 1000:g}–{TARGET_FACT_P95_HIGH_MS / 1000:g} s"
    if query_class == CLASS_EXPLORE:
        return f"p95 ≤ {COLUMNAR_EXPLORE_P95_MS / 1000:g} s (columnar threshold)"
    return "—"


def columnar_thresholds_crossed(
    *, explore_p95_ms: float | None, fact_rows: int, longest_daily_build_s: float
) -> list[str]:
    """Which columnar-fallback thresholds the measurements actually cross.

    Returns one sentence per crossed threshold and an EMPTY list otherwise —
    the caller must not infer a recommendation from anything else.
    """

    crossed: list[str] = []
    if explore_p95_ms is not None and explore_p95_ms > COLUMNAR_EXPLORE_P95_MS:
        crossed.append(
            f"Explore p95 {explore_p95_ms:,.0f} ms exceeds {COLUMNAR_EXPLORE_P95_MS:,} ms"
        )
    if fact_rows > COLUMNAR_FACT_ROWS:
        crossed.append(f"fact rows {fact_rows:,} exceed {COLUMNAR_FACT_ROWS:,}")
    if longest_daily_build_s > COLUMNAR_BUILD_SECONDS:
        crossed.append(
            f"one daily build took {longest_daily_build_s:,.0f} s, "
            f"beyond {COLUMNAR_BUILD_SECONDS:,} s"
        )
    return crossed


#: Buckets the roster sample is drawn from. A stable digest of the account
#: reference, so the SAME accounts are kept at every date and each sampled
#: position keeps an unbroken 60-point history — which is what makes a
#: month-over-month or vintage query measure the shape it would in production.
SAMPLE_BUCKETS = 10_000


def keeps_reference(reference: str, fraction: float) -> bool:
    """Whether ``reference`` is in a ``fraction`` sample, deterministically."""

    if fraction >= 1.0:
        return True
    digest = hashlib.blake2b(reference.encode("utf-8"), digest_size=8).digest()
    bucket = int.from_bytes(digest, "big") % SAMPLE_BUCKETS
    return bucket < fraction * SAMPLE_BUCKETS


def chunked(rows: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    """``rows`` in slices of at most ``size`` (the push API's per-page cap)."""

    if size < 1:
        raise ValueError("size must be at least one row")
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def remap_dates(source: Sequence[date], last: date) -> dict[date, date]:
    """Map simulated month-ends onto the ``len(source)`` month-ends ending at ``last``.

    The simulator's calendar starts a decade ago; a benchmark wants the book to
    sit where a live one does (recent partitions, a ``bi_dim_date`` spine the
    retention window and the month-end rule behave normally over). Order is
    preserved and the spacing stays monthly, so nothing about the shape changes
    — only the labels.
    """

    ordered = sorted(source)
    months: list[date] = [last]
    while len(months) < len(ordered):
        first_of_month = months[-1].replace(day=1)
        months.append(first_of_month - _ONE_DAY)
    months.reverse()
    return dict(zip(ordered, months, strict=True))


# --- configuration --------------------------------------------------------------------------

#: Records per staged push page. The API's cap is 5,000 (``MAX_RECORDS_PER_PAGE``).
PUSH_PAGE_SIZE = 4_000

#: Variables that name a database no benchmark may ever touch, in the process
#: environment or in the untracked ``backend/.env``.
FORBIDDEN_URL_VARS = (
    "_BI_BENCHMARK_INHERITED_DATABASE_URL",
    "DATABASE_URL",
    "WORKER_DATABASE_URL",
    "REAL_DATA_DATABASE_URL",
    "LIVE_DATA_DATABASE_URL",
    "BI_DATABASE_URL",
)

#: A tenant that owns nothing, used to prove the marts are not readable across
#: the tenant boundary under the benchmark's own role.
DECOY_ORG_ID = "OR-BENCHDEC"


@dataclass(frozen=True, slots=True)
class Options:
    database_url: str
    panels_dir: Path
    dates: int
    repeats: int
    warmups: int
    last_date: date | None
    report: Path | None
    phases: tuple[str, ...]
    explain: bool
    simulator_seed: int | None
    #: Keep this fraction of the simulator's roster (1.0 = the whole book).
    position_sample: float
    #: Push the chart of accounts without balances.
    gl_balances: bool


# --- measurements ---------------------------------------------------------------------------


@dataclass(slots=True)
class BuildMeasurement:
    as_of: date
    outcome: str
    wall_seconds: float
    scope_seconds: dict[str, float] = field(default_factory=dict)
    row_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class QueryMeasurement:
    name: str
    query_class: str
    surface: str
    route_ms: list[float] = field(default_factory=list)
    db_ms: list[float] = field(default_factory=list)
    rows: int = 0
    used_aggregate: bool | None = None
    fact_table: str | None = None
    note: str = ""

    @property
    def route_p50(self) -> float:
        return percentile(self.route_ms, 0.50)

    @property
    def route_p95(self) -> float:
        return percentile(self.route_ms, 0.95)

    @property
    def db_p50(self) -> float:
        return percentile(self.db_ms, 0.50)

    @property
    def db_p95(self) -> float:
        return percentile(self.db_ms, 0.95)


@dataclass(slots=True)
class Report:
    started_at: str
    options: Options
    database: dict[str, Any] = field(default_factory=dict)
    role: dict[str, Any] = field(default_factory=dict)
    rls_proof: dict[str, Any] = field(default_factory=dict)
    dataset: dict[str, Any] = field(default_factory=dict)
    ingest_seconds: float = 0.0
    builds: list[BuildMeasurement] = field(default_factory=list)
    skip_build: BuildMeasurement | None = None
    queries: list[QueryMeasurement] = field(default_factory=list)
    table_rows: dict[str, int] = field(default_factory=dict)
    explains: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --- safety ---------------------------------------------------------------------------------


def _normalised(url: str) -> str:
    return url.strip().rstrip("/")


def dotenv_urls(env_file: Path) -> dict[str, str]:
    """The database URLs an untracked ``.env`` names, without importing settings.

    ``DATABASE_URL`` is blanked at import (so no code path can silently reach the
    primary), which also hides it from ``get_settings()`` — so the refusal below
    reads the file itself. A missing file is not an error: CI has none.
    """

    found: dict[str, str] = {}
    if not env_file.is_file():
        return found
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if name in FORBIDDEN_URL_VARS:
            found[name] = _normalised(value.strip().strip("'\""))
    return found


def require_disposable_target(database_url: str) -> None:
    """Refuse a URL that names any database the deployment itself uses."""

    target = _normalised(database_url)
    if not target:
        raise SystemExit("--database-url is required (the benchmark never infers its database)")
    candidates: dict[str, str] = {
        variable: _normalised(os.environ.get(variable, "")) for variable in FORBIDDEN_URL_VARS
    }
    candidates.update(dotenv_urls(_BACKEND / ".env"))
    for variable, configured in candidates.items():
        if configured and configured == target:
            label = variable.replace("_BI_BENCHMARK_INHERITED_", "")
            raise SystemExit(
                f"refusing to run: --database-url is this environment's {label}. "
                "The benchmark runs only against a disposable instance."
            )


def verify_role(session: Any) -> dict[str, Any]:
    """The connecting role's privileges; refuses anything that can bypass RLS."""

    from sqlalchemy import text  # noqa: PLC0415

    row = session.execute(
        text(
            "SELECT current_user AS role, rolsuper, rolbypassrls, rolcreaterole "
            "FROM pg_roles WHERE rolname = current_user"
        )
    ).one()
    facts = {
        "role": row.role,
        "rolsuper": bool(row.rolsuper),
        "rolbypassrls": bool(row.rolbypassrls),
        "rolcreaterole": bool(row.rolcreaterole),
    }
    if facts["rolsuper"] or facts["rolbypassrls"]:
        raise SystemExit(
            f"refusing to run: role {facts['role']} is "
            f"{'SUPERUSER' if facts['rolsuper'] else 'BYPASSRLS'}. "
            "Every measured query would skip the tenant policy."
        )
    return facts


def prove_rls_enforced(session: Any, *, organization_id: str, table: str) -> dict[str, Any]:
    """Empirical proof that the measured reads went through the tenant policy.

    Counts one mart under three tenant GUC values: the benchmark's tenant (must
    see rows), a tenant that owns nothing, and no GUC at all. A role that
    bypassed RLS would return the same non-zero count all three times.
    """

    from sqlalchemy import text  # noqa: PLC0415

    counted: dict[str, Any] = {"table": table}
    statement = text(f"SELECT count(*) FROM {table}")  # noqa: S608 - a mapped table name
    setting = text("SELECT set_config('app.organization_id', :org, true)")
    for label, tenant in (
        ("no_tenant_guc", None),
        ("decoy_tenant", DECOY_ORG_ID),
        ("own_tenant", organization_id),
    ):
        session.rollback()
        if tenant is not None:
            session.execute(setting, {"org": tenant})
        counted[label] = session.execute(statement).scalar_one()
    session.rollback()
    counted["force_row_security"] = session.execute(
        text(
            "SELECT relforcerowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = current_schema() AND c.relname = :t"
        ),
        {"t": table},
    ).scalar_one()
    counted["enforced"] = bool(
        counted["own_tenant"] > 0
        and counted["decoy_tenant"] == 0
        and counted["no_tenant_guc"] == 0
        and counted["force_row_security"]
    )
    session.rollback()
    return counted


# --- phase 1: the synthetic book -------------------------------------------------------------


def generate_panels(options: Options) -> dict[str, Any]:
    """Run the existing simulator unless its panels are already on disk."""

    panels = options.panels_dir
    snapshots = panels / "position_snapshots"
    existing = sorted(snapshots.glob("month=*")) if snapshots.exists() else []
    if len(existing) >= options.dates:
        return {
            "source": "existing panels",
            "panels_dir": str(panels),
            "months_available": len(existing),
        }
    command = [
        sys.executable,
        "-m",
        "data.simulator.run",
        "--out",
        str(panels.parent),
        "--months",
        str(options.dates),
    ]
    if options.simulator_seed is not None:
        command += ["--seed", str(options.simulator_seed)]
    environment = {**os.environ, "PYTHONPATH": str(_REPO)}
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        command, cwd=_REPO, env=environment, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise SystemExit(f"simulator failed:\n{completed.stdout}\n{completed.stderr}")
    return {
        "source": "data/simulator (this run)",
        "panels_dir": str(panels),
        "seconds": round(time.monotonic() - started, 1),
        "months_available": len(sorted(snapshots.glob("month=*"))),
        "stdout_tail": completed.stdout.strip().splitlines()[-1:],
    }


def _panel_months(panels_dir: Path, panel: str) -> list[date]:
    root = panels_dir / panel
    months: list[date] = []
    for child in sorted(root.glob("month=*")):
        months.append(date.fromisoformat(child.name.removeprefix("month=")))
    return months


def _read_month(panels_dir: Path, panel: str, month: date) -> Any:
    import pandas as pd  # noqa: PLC0415

    return pd.read_parquet(panels_dir / panel / f"month={month.isoformat()}" / "part.parquet")


def _read_single(panels_dir: Path, panel: str) -> Any:
    import pandas as pd  # noqa: PLC0415

    return pd.read_parquet(panels_dir / panel / "part.parquet")


def _clean(value: Any) -> Any:  # noqa: PLR0911 - one return per panel cell type
    """A panel cell as JSON: NaN/NaT become omitted, dates ISO, numpy scalars native."""

    import pandas as pd  # noqa: PLC0415

    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, str):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())[:10]
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    return value


# --- phase 2: ingestion through the Data Engine -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Tenant:
    organization_id: str
    bank_id: str
    user_id: Any
    machine_headers: dict[str, str]
    human_headers: dict[str, str]


def scaffold_tenant(client: Any) -> Tenant:
    """The institution, its principal and its BI authority — no book.

    The fixture writes the bank, the organization, one user, the reporting-period
    spine and the governed registers a deployment gets from its migrations plus
    onboarding. It writes NO canonical position, counterparty, product or GL
    row: every one of those arrives through the push API below.
    """

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from tests.fixtures.canonical_bank_fixture import (  # noqa: PLC0415
        materialize_canonical_test_book,
    )
    from tests.support.helpers import ORG_1  # noqa: PLC0415

    _ = client  # the app's engine is resolved by the client's construction
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    return _tenant_with_reader()


def _grant_bi_reader(session: Any) -> None:
    """The one sentence a BI reader needs, created only if it is not there.

    An organization-wide ``viewer`` over every module at every sensitivity — the
    same sentence ``ensure_owner_read_access`` writes for an Org Owner. It is the
    heaviest honest workload and the only one that can reach the record-grain
    members the fact-grain class needs. ``SensitivityScope`` is not a ladder: a
    ``restricted``-only binding covers restricted members and nothing else, so
    ``ALL`` is required, not merely widest. A branch-scoped reader would carry an
    extra injected filter; that is noted in the report, not measured as the
    default.
    """

    from sqlalchemy import select  # noqa: PLC0415

    from app.core.authorization import (  # noqa: PLC0415
        GrantorType,
        InstitutionScope,
        ModuleScope,
        PrincipalType,
        RoleBundle,
        SensitivityScope,
    )
    from app.identity.service import authorization  # noqa: PLC0415
    from app.models import AuthorizationBinding  # noqa: PLC0415
    from tests.support.helpers import ORG_1, USER_1  # noqa: PLC0415

    existing = session.execute(
        select(AuthorizationBinding.id).where(
            AuthorizationBinding.organization_id == ORG_1,
            AuthorizationBinding.principal_user_id == USER_1,
            AuthorizationBinding.role_bundle == RoleBundle.VIEWER.value,
            AuthorizationBinding.module_scope == ModuleScope.ALL.value,
            AuthorizationBinding.sensitivity_scope == SensitivityScope.ALL.value,
            AuthorizationBinding.status == "active",
        )
    ).first()
    if existing is not None:
        return
    authorization.create_role_binding(
        session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.ALL,
            SensitivityScope.ALL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "bi-benchmark"),
        reason="Read every BI member for the performance benchmark.",
    )
    session.commit()


def mint_human_headers() -> dict[str, str]:
    """A fresh tenant access token for the benchmark's reader.

    An access token has a short life and a full run takes well over an hour, so
    the token minted before ingestion is expired by the time the query phase
    starts. Re-minting is the honest fix: raising the token's lifetime for a
    benchmark would measure a deployment nobody runs.
    """

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415
    from tests.support.helpers import ORG_1, USER_1, headers  # noqa: PLC0415

    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        user = session.get(User, USER_1)
        if user is None:
            raise SystemExit("no scaffolded tenant in this database; run --phase ingest first")
        return headers(ORG_1, user_id=USER_1, authorization_version=user.authorization_version)
    finally:
        session.close()


def _tenant_with_reader() -> Tenant:
    """The scaffolded tenant, its reader sentence and its two credentials."""

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.models import User  # noqa: PLC0415
    from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID  # noqa: PLC0415
    from tests.support.helpers import (  # noqa: PLC0415
        ORG_1,
        USER_1,
        headers,
        integration_key_headers,
    )

    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = ORG_1
        user = session.get(User, USER_1)
        if user is None:
            raise SystemExit("no scaffolded tenant in this database; run --phase ingest first")
        _grant_bi_reader(session)
        session.refresh(user)
        version = user.authorization_version
    finally:
        session.close()
    return Tenant(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        user_id=USER_1,
        machine_headers=integration_key_headers(SAMPLE_BANK_ID, ORG_1),
        human_headers=headers(ORG_1, user_id=USER_1, authorization_version=version),
    )


#: Panel date columns that must travel with the as-of remap. The book is
#: translated RIGIDLY in time — shifting the as-of without shifting the terms
#: would mature the whole loan book at once, collapse every maturity bucket into
#: one and make the ingestion pipeline report a five-figure warning list.
_DATE_COLUMNS = ("origination_date", "contractual_maturity", "next_repricing_date")


def _reporting_balance_columns(
    columns: set[str], attribute_columns: Sequence[str]
) -> tuple[str | None, str | None]:
    """The panel's reporting-currency balance and notional column names.

    Read off the simulator's own ``ATTRIBUTE_COLUMNS`` rather than named here:
    those column names carry the book's unit as a suffix, and no unit belongs in
    this script.
    """

    balance = next(
        (c for c in attribute_columns if c.startswith("balance_") and c in columns), None
    )
    notional = next(
        (c for c in attribute_columns if c.startswith("notional_") and c in columns), None
    )
    return balance, notional


def _position_records(
    frame: Any, attribute_columns: Sequence[str], *, offset_days: int, sample: float = 1.0
) -> list[dict[str, Any]]:
    """Panel rows as push-API ``position`` records.

    The typed contract fields are taken by name; everything in the panel that
    the canonical snapshot carries as free-form attributes (the simulator's own
    ``ATTRIBUTE_COLUMNS``, which is also what ``history_loader`` packs) is sent
    under ``attributes`` — that is where ``app/domain/bi/extract.py`` reads the
    reporting-currency balance, the branch and the provision from.

    ``balance`` is the REPORTING-currency amount, not the native one, because
    the simulator keeps its whole general ledger in the reporting currency
    (every GL row's ``currency`` says so) and ``gl_subledger_reconciliation``
    compares the two raw numbers with no conversion. Stating the sub-ledger in
    the ledger's own unit is the consistent reading of this book; pushing native
    amounts against a converted ledger is what a BLOCKER is for, and disabling
    the control to get a benchmark through would measure a shape the product
    refuses. The consequence is stated in the report: for a foreign-currency
    position ``balance_native`` equals ``balance_rc``, which no measured
    workload reads.
    """

    typed = (
        "source_reference",
        "position_type",
        "currency",
        "counterparty_id",
        "product_code",
        "gl_code",
        "interest_rate",
        "rate_type",
        "rate_index",
        "rate_spread",
        "ifrs9_stage",
    )
    renames = {
        "counterparty_id": "counterparty_reference",
        "gl_code": "gl_account_code",
    }
    columns = set(frame.columns)
    typed_present = [name for name in typed if name in columns]
    dates_present = [name for name in _DATE_COLUMNS if name in columns]
    attributes_present = [name for name in attribute_columns if name in columns]
    balance_column, notional_column = _reporting_balance_columns(columns, attribute_columns)
    shift = timedelta(days=offset_days)
    records: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        raw = row._asdict()
        if not keeps_reference(str(raw.get("source_reference")), sample):
            continue
        record: dict[str, Any] = {}
        for name in typed_present:
            value = _clean(raw.get(name))
            if value is None or value == "":
                continue
            record[renames.get(name, name)] = value
        for name in dates_present:
            value = _clean(raw.get(name))
            if value is None or value == "":
                continue
            record[name] = (date.fromisoformat(str(value)) + shift).isoformat()
        balance = _clean(raw.get(balance_column)) if balance_column else None
        if balance is None:
            continue
        record["balance"] = balance
        notional = _clean(raw.get(notional_column)) if notional_column else None
        if notional is not None:
            record["notional"] = notional
        attributes = {
            name: _clean(raw.get(name))
            for name in attributes_present
            if _clean(raw.get(name)) is not None
        }
        if attributes:
            record["attributes"] = attributes
        records.append(record)
    return records


def _gl_records(frame: Any, *, with_balances: bool = True) -> list[dict[str, Any]]:
    balance_column = (
        next((name for name in frame.columns if name.startswith("balance_")), None)
        if with_balances
        else None
    )
    records: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        raw = row._asdict()
        record = {
            "source_reference": str(_clean(raw.get("gl_code"))),
            "account_code": str(_clean(raw.get("gl_code"))),
            "name": _clean(raw.get("gl_name")),
            "account_class": _clean(raw.get("account_class")),
            "currency": _clean(raw.get("currency")),
        }
        if balance_column is not None:
            balance = _clean(raw.get(balance_column))
            if balance is not None:
                record["balance"] = balance
        records.append({k: v for k, v in record.items() if v is not None})
    return records


def _simulator_attribute_columns() -> tuple[str, ...]:
    """The simulator's own ``ATTRIBUTE_COLUMNS`` — the panel columns the canonical
    snapshot carries as free-form attributes, and where ``app/domain/bi/extract.py``
    reads the reporting-currency balance, the branch and the provision from.

    Imported dynamically because ``data/`` sits outside the backend package root
    (gitignored generated-data territory, not on the type checker's path), and
    because reading the list from the simulator is what keeps this script free of
    the unit-suffixed column names the panels use.
    """

    module = importlib.import_module("data.simulator.schemas")
    return tuple(module.ATTRIBUTE_COLUMNS)


def _counterparty_records(frame: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        raw = row._asdict()
        record = {
            "source_reference": str(_clean(raw.get("counterparty_id"))),
            "name": _clean(raw.get("counterparty_name")),
            "counterparty_type": _clean(raw.get("counterparty_type")),
            "country_code": _clean(raw.get("country")),
            "rating": _clean(raw.get("credit_rating")),
        }
        records.append({k: v for k, v in record.items() if v is not None})
    return records


def _product_records(frame: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        raw = row._asdict()
        record = {
            "source_reference": str(_clean(raw.get("product_code"))),
            "product_code": str(_clean(raw.get("product_code"))),
            "name": _clean(raw.get("product_name")),
            "regulatory_category": _clean(raw.get("regulatory_category")),
        }
        records.append({k: v for k, v in record.items() if v is not None})
    return records


def push_one_date(  # noqa: PLR0913 - one push carries its whole identity
    client: Any,
    tenant: Tenant,
    *,
    as_of: date,
    entities: dict[str, list[dict[str, Any]]],
    reason: str,
) -> dict[str, Any]:
    """The push API's three calls for one business date."""

    opened = client.post(
        f"/api/v1/banks/{tenant.bank_id}/push-batches",
        headers=tenant.machine_headers,
        json={
            "as_of_date": as_of.isoformat(),
            "idempotency_key": f"bi-benchmark:{as_of.isoformat()}",
            "reason": reason,
        },
    )
    if opened.status_code != 201:
        raise SystemExit(f"open push batch failed ({opened.status_code}): {opened.text[:500]}")
    push_id = opened.json()["push_batch_id"]
    items: list[tuple[str, dict[str, Any]]] = [
        (kind, record) for kind, records in entities.items() for record in records
    ]
    for page in chunked(items, PUSH_PAGE_SIZE):
        body: dict[str, dict[str, list[dict[str, Any]]]] = {"entities": {}}
        for kind, record in page:
            body["entities"].setdefault(kind, []).append(record)
        staged = client.post(
            f"/api/v1/banks/{tenant.bank_id}/push-batches/{push_id}/records",
            headers=tenant.machine_headers,
            json=body,
        )
        if staged.status_code != 200:
            raise SystemExit(f"stage page failed ({staged.status_code}): {staged.text[:500]}")
    committed = client.post(
        f"/api/v1/banks/{tenant.bank_id}/push-batches/{push_id}/commit",
        headers=tenant.machine_headers,
    )
    if committed.status_code != 201:
        raise SystemExit(f"commit failed ({committed.status_code}): {committed.text[:500]}")
    payload = committed.json()
    batch = payload.get("batch", payload)
    status = batch.get("status")
    if status not in ("accepted", "accepted_with_warnings"):
        # A rejected book measures nothing, and the whole point of pushing
        # through the Data Engine is that its controls apply. Name the blockers.
        report = batch.get("validation_report") or {}
        blockers = [
            f"{f.get('rule')}: {f.get('detail')}"
            for f in (report.get("failures") or [])
            if f.get("severity") not in ("WARNING", "INFO")
        ]
        summary = json.dumps(report.get("summary"), default=str)
        detail = "\n  ".join(dict.fromkeys(blockers))[:2000] or "(no blocking finding recorded)"
        raise SystemExit(
            f"the push for {as_of} was {status}; the benchmark will not measure a "
            f"refused book.\n  summary: {summary}\n  {detail}"
        )
    return {
        "status": status,
        "extracted": batch.get("records_extracted"),
        "accepted": batch.get("records_accepted"),
    }


def ingest(client: Any, tenant: Tenant, options: Options, report: Report) -> list[date]:
    """Push every panel month through the Data Engine; returns the as-of dates."""

    attribute_columns = _simulator_attribute_columns()
    panels = options.panels_dir
    months = _panel_months(panels, "position_snapshots")[: options.dates]
    if not months:
        raise SystemExit(f"no position_snapshots partitions under {panels}")
    last = options.last_date or _last_month_end_on_or_before(date.today())
    mapping = remap_dates(months, last)

    dimensions = {
        "counterparty": _counterparty_records(_read_single(panels, "dim_counterparties")),
        "product": _product_records(_read_single(panels, "dim_products")),
    }
    total_positions = 0
    started = time.monotonic()
    as_of_dates: list[date] = []
    for index, month in enumerate(months):
        as_of = mapping[month]
        frame = _read_month(panels, "position_snapshots", month)
        entities: dict[str, list[dict[str, Any]]] = {}
        if index == 0:
            entities.update(dimensions)
        entities["gl_account"] = _gl_records(
            _read_month(panels, "gl_accounts", month), with_balances=options.gl_balances
        )
        entities["position"] = _position_records(
            frame,
            attribute_columns,
            offset_days=(as_of - month).days,
            sample=options.position_sample,
        )
        total_positions += len(entities["position"])
        outcome = push_one_date(
            client,
            tenant,
            as_of=as_of,
            entities=entities,
            reason="BI performance benchmark: synthetic book from the history simulator.",
        )
        print(
            f"  push [{index + 1:>3}/{len(months)}] {as_of} "
            f"positions={len(entities['position']):>7,} "
            f"status={outcome['status']} accepted={outcome['accepted']:,}",
            flush=True,
        )
        as_of_dates.append(as_of)
    report.ingest_seconds = time.monotonic() - started
    report.dataset.update(
        {
            "dates": len(as_of_dates),
            "position_records_pushed": total_positions,
            "counterparties": len(dimensions["counterparty"]),
            "products": len(dimensions["product"]),
            "first_as_of": as_of_dates[0].isoformat(),
            "last_as_of": as_of_dates[-1].isoformat(),
            "ingestion_path": "POST /banks/{id}/push-batches → /records → /commit (API_PUSH)",
            "roster_sample": options.position_sample,
            "general_ledger_balances_pushed": options.gl_balances,
        }
    )
    return as_of_dates


def _last_month_end_on_or_before(day: date) -> date:
    return day.replace(day=1) - _ONE_DAY


# --- phase 3: the mart build ------------------------------------------------------------------


def snapshot_dates_for(tenant: Tenant) -> list[date]:
    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.services.bi import mart_builder  # noqa: PLC0415

    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        return mart_builder.snapshot_dates(session, tenant.organization_id, tenant.bank_id)
    finally:
        session.close()


def build_marts(tenant: Tenant, as_of_dates: Sequence[date], report: Report) -> None:
    """``refresh_bank_as_of`` per date, timed; then the fingerprint skip."""

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.services.bi import mart_builder  # noqa: PLC0415

    for index, as_of in enumerate(as_of_dates):
        session = get_sessionmaker()()
        try:
            session.info["organization_id"] = tenant.organization_id
            started = time.monotonic()
            outcome = mart_builder.refresh_bank_as_of(
                session,
                organization_id=tenant.organization_id,
                bank_id=tenant.bank_id,
                as_of=as_of,
                reason="benchmark",
            )
            session.commit()
            elapsed = time.monotonic() - started
        finally:
            session.close()
        measurement = BuildMeasurement(
            as_of=as_of,
            outcome=outcome.status,
            wall_seconds=elapsed,
            scope_seconds=_scope_seconds(tenant, as_of),
            row_counts=dict(outcome.row_counts),
        )
        report.builds.append(measurement)
        print(
            f"  build [{index + 1:>3}/{len(as_of_dates)}] {as_of} "
            f"{outcome.status} {elapsed:6.2f}s "
            f"positions={measurement.row_counts.get('bi_fact_position_daily', 0):>7,} "
            f"agg={measurement.row_counts.get('bi_agg_position_daily', 0):>6,}",
            flush=True,
        )

    if not as_of_dates:
        return
    as_of = as_of_dates[-1]
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        started = time.monotonic()
        outcome = mart_builder.refresh_bank_as_of(
            session,
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            as_of=as_of,
            reason="benchmark-skip",
        )
        session.commit()
        elapsed = time.monotonic() - started
    finally:
        session.close()
    report.skip_build = BuildMeasurement(as_of=as_of, outcome=outcome.status, wall_seconds=elapsed)
    print(f"  build skip  {as_of} {outcome.status} {elapsed:6.3f}s", flush=True)


def recorded_builds(tenant: Tenant) -> list[BuildMeasurement]:
    """Rebuild the build measurements from ``bi_mart_builds`` for a query-only run.

    The builder records its own ``started_at`` / ``finished_at`` per scope and its
    own ``elapsed_ms`` inside ``row_counts``, so a run that skips the build phase
    can still report what the last build of each date cost — read from the
    product's records rather than remembered, and labelled as such in the report.
    """

    from sqlalchemy import select  # noqa: PLC0415

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.models.bi import BiMartBuild  # noqa: PLC0415

    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        rows = session.execute(
            select(
                BiMartBuild.as_of_date,
                BiMartBuild.scope,
                BiMartBuild.status,
                BiMartBuild.started_at,
                BiMartBuild.finished_at,
                BiMartBuild.row_counts,
            )
            .where(
                BiMartBuild.organization_id == tenant.organization_id,
                BiMartBuild.bank_id == tenant.bank_id,
            )
            .order_by(BiMartBuild.as_of_date)
        ).all()
    finally:
        session.close()
    by_date: dict[date, list[Any]] = {}
    for row in rows:
        by_date.setdefault(row.as_of_date, []).append(row)
    measurements: list[BuildMeasurement] = []
    for as_of in sorted(by_date):
        scoped = by_date[as_of]
        starts = [r.started_at for r in scoped if r.started_at is not None]
        ends = [r.finished_at for r in scoped if r.finished_at is not None]
        wall = (max(ends) - min(starts)).total_seconds() if starts and ends else 0.0
        counts: dict[str, int] = {}
        scope_seconds: dict[str, float] = {}
        for r in scoped:
            payload = r.row_counts or {}
            scope_seconds[r.scope] = round(payload.get("elapsed_ms", 0) / 1000, 3)
            for table, count in payload.items():
                if table != "elapsed_ms":
                    counts[table] = count
        statuses = {r.status for r in scoped}
        outcome = "succeeded" if statuses == {"succeeded"} else "/".join(sorted(statuses))
        measurements.append(
            BuildMeasurement(
                as_of=as_of,
                outcome=outcome,
                wall_seconds=wall,
                scope_seconds=scope_seconds,
                row_counts=counts,
            )
        )
    return measurements


def _scope_seconds(tenant: Tenant, as_of: date) -> dict[str, float]:
    """The builder's own per-scope ``elapsed_ms``, as it recorded them."""

    from sqlalchemy import select  # noqa: PLC0415

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.models.bi import BiMartBuild  # noqa: PLC0415

    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        rows = session.execute(
            select(BiMartBuild.scope, BiMartBuild.row_counts).where(
                BiMartBuild.organization_id == tenant.organization_id,
                BiMartBuild.bank_id == tenant.bank_id,
                BiMartBuild.as_of_date == as_of,
            )
        ).all()
    finally:
        session.close()
    return {scope: round((counts or {}).get("elapsed_ms", 0) / 1000, 3) for scope, counts in rows}


# --- phase 4: the query workloads --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Workload:
    name: str
    query_class: str
    surface: str
    body: dict[str, Any]
    note: str = ""


def workloads(
    *, as_of: date, window_start: date, full_start: date, currencies: Sequence[str]
) -> tuple[Workload, ...]:
    """The measured workload: aggregate, fact-grain and Explore-shaped.

    Every member id is the catalogue's own. The classes are asserted, not
    assumed: the run fails if an "aggregate" workload did not compile to
    ``bi_agg_position_daily`` or a "fact-grain" one did. ``currencies`` are read
    out of the built mart — no filter value is a literal here, because the book
    under measurement states its own units and a benchmark must not assert a
    jurisdiction.
    """

    single = {"as_of": as_of.isoformat()}
    year = {"range": {"start": window_start.isoformat(), "end": as_of.isoformat()}}
    whole = {"range": {"start": full_start.isoformat(), "end": as_of.isoformat()}}
    return (
        # --- aggregate table ---------------------------------------------------
        Workload(
            "A1 gross loans, one date, no dimension",
            CLASS_AGGREGATE,
            "query",
            {"measures": ["loans.balance_rc"], "time": single},
        ),
        Workload(
            "A2 loans and non-performing exposure by product family",
            CLASS_AGGREGATE,
            "query",
            {
                "measures": ["loans.balance_rc", "loans.npl_exposure_rc"],
                "dimensions": ["product.family"],
                "time": single,
            },
        ),
        Workload(
            "A3 deposits by branch and currency",
            CLASS_AGGREGATE,
            "query",
            {
                "measures": ["deposits.balance_rc", "deposits.count"],
                "dimensions": ["branch.code", "position.currency"],
                "time": single,
            },
        ),
        Workload(
            "A4 NPL ratio by classification grade",
            CLASS_AGGREGATE,
            "query",
            {
                "measures": ["loans.npl_ratio_pct", "loans.balance_rc"],
                "dimensions": ["loan.grade"],
                "time": single,
            },
        ),
        Workload(
            "A5 loans by IFRS 9 stage, twelve months",
            CLASS_AGGREGATE,
            "query",
            {
                "measures": ["loans.balance_rc"],
                "dimensions": ["time.calendar_month", "loan.ifrs9_stage"],
                "time": year,
            },
        ),
        Workload(
            "A6 loans and deposits by month, whole history",
            CLASS_AGGREGATE,
            "query",
            {
                "measures": ["loans.balance_rc", "deposits.balance_rc"],
                "dimensions": ["time.calendar_month"],
                "time": whole,
            },
        ),
        Workload(
            "A7 loans by branch, filtered and sorted, top twenty",
            CLASS_AGGREGATE,
            "query",
            {
                "measures": ["loans.balance_rc"],
                "dimensions": ["branch.code"],
                "filters": [{"member": "loan.ifrs9_stage", "op": "in", "values": [1, 2]}],
                "sort": [{"member": "loans.balance_rc", "direction": "desc"}],
                "top_n": {"dimension": "branch.code", "n": 20},
                "time": single,
            },
        ),
        # --- fact grain --------------------------------------------------------
        Workload(
            "F1 weighted average loan rate by product family",
            CLASS_FACT,
            "query",
            {
                "measures": ["loans.weighted_average_rate"],
                "dimensions": ["product.family"],
                "time": single,
            },
            note="a weighted average needs the row grain — the aggregate cannot serve it",
        ),
        Workload(
            "F2 largest single-name share of the loan book",
            CLASS_FACT,
            "query",
            {"measures": ["loans.largest_single_name_share_pct"], "time": single},
            note="a concentration measure needs the obligor grain",
        ),
        Workload(
            "F2b sector concentration of the loan book",
            CLASS_FACT,
            "query",
            {"measures": ["loans.sector_hhi"], "time": single},
            note="one query per concentration dimension — the compiler refuses two",
        ),
        Workload(
            "F3 loans by contractual maturity bucket",
            CLASS_FACT,
            "query",
            {
                "measures": ["loans.balance_rc", "loans.count"],
                "dimensions": ["position.maturity_bucket"],
                "time": single,
            },
            note="the maturity bucket is not in the aggregate's grain",
        ),
        Workload(
            "F4 loans by counterparty, top fifty",
            CLASS_FACT,
            "query",
            {
                "measures": ["loans.balance_rc"],
                "dimensions": ["counterparty.name", "counterparty.type"],
                "sort": [{"member": "loans.balance_rc", "direction": "desc"}],
                "top_n": {"dimension": "counterparty.name", "n": 50},
                "time": single,
            },
        ),
        Workload(
            "F5 restructured exposure and interest in suspense by product",
            CLASS_FACT,
            "query",
            {
                "measures": ["loans.restructured_exposure_rc", "loans.interest_in_suspense_rc"],
                "dimensions": ["product.name"],
                "time": single,
            },
        ),
        Workload(
            "F6 loans by origination month, twelve months of book",
            CLASS_FACT,
            "query",
            {
                "measures": ["loans.balance_rc", "loans.count"],
                "dimensions": ["time.calendar_month", "loan.vintage_month"],
                "time": year,
            },
        ),
        Workload(
            "F8 weighted average rate by month and product, WHOLE history",
            CLASS_FACT,
            "query",
            {
                "measures": ["loans.weighted_average_rate", "loans.balance_rc"],
                "dimensions": ["time.calendar_month", "product.family"],
                "time": whole,
            },
            note=(
                "the widest fact-grain scan the book allows — it touches every date's "
                "rows, which is how the top of the volume range is MEASURED rather "
                "than projected"
            ),
        ),
        Workload(
            "F7 record grain: one page of positions",
            CLASS_FACT,
            "drill",
            {
                "query": {
                    "measures": ["positions.balance_rc"],
                    "dimensions": [
                        "position.source_reference",
                        "position.type",
                        "position.currency",
                    ],
                    "sort": [{"member": "positions.balance_rc", "direction": "desc"}],
                    "time": single,
                },
                "start_row": 0,
                "end_row": 500,
            },
        ),
        # --- Explore-shaped, through the grid ----------------------------------
        Workload(
            "E1 group by product family and branch, with subtotals",
            CLASS_EXPLORE,
            "grid",
            {
                "query": {
                    "measures": ["loans.balance_rc", "loans.npl_exposure_rc"],
                    "dimensions": ["product.family", "branch.code"],
                    "subtotals": True,
                    "time": single,
                },
                "start_row": 0,
                "end_row": 500,
            },
        ),
        Workload(
            "E2 pivot balance by currency across branches",
            CLASS_EXPLORE,
            "grid",
            {
                "query": {
                    "measures": ["positions.balance_rc"],
                    "dimensions": ["branch.code"],
                    "pivot": {"dimension": "position.currency"},
                    "time": single,
                },
                "start_row": 0,
                "end_row": 500,
            },
        ),
        Workload(
            "E3 filter, sort and page the loan book by grade and stage",
            CLASS_EXPLORE,
            "grid",
            {
                "query": {
                    "measures": ["loans.balance_rc", "loans.provision_held_rc"],
                    "dimensions": ["loan.grade", "loan.ifrs9_stage", "product.family"],
                    "filters": [
                        {
                            "member": "position.currency",
                            "op": "in",
                            "values": list(currencies),
                        },
                        {"member": "loan.ifrs9_stage", "op": "in", "values": [1, 2, 3]},
                    ],
                    "sort": [{"member": "loans.balance_rc", "direction": "desc"}],
                    "time": single,
                },
                "start_row": 0,
                "end_row": 500,
            },
            note=f"filtered to the {len(currencies)} units the book itself reports in",
        ),
        Workload(
            "E4 period comparison by product family",
            CLASS_EXPLORE,
            "grid",
            {
                "query": {
                    "measures": ["loans.balance_rc"],
                    "dimensions": ["product.family"],
                    "time": {
                        "as_of": as_of.isoformat(),
                        "compare_to": window_start.isoformat(),
                    },
                },
                "start_row": 0,
                "end_row": 500,
            },
        ),
        Workload(
            "E5 twelve-month trend by product family, fact grain",
            CLASS_EXPLORE,
            "grid",
            {
                "query": {
                    "measures": ["loans.weighted_average_rate", "loans.balance_rc"],
                    "dimensions": ["time.calendar_month", "product.family"],
                    "time": year,
                },
                "start_row": 0,
                "end_row": 500,
            },
        ),
    )


#: The product's own per-principal read budget (``query_log.RATE_LIMIT_*``) is
#: 120 reads a minute, which a repeat-driven benchmark WILL reach. A 429 is the
#: platform working; the run honours ``Retry-After`` and re-measures rather than
#: raising the limit, and counts every wait so the report can say so.
_BACKOFF_CEILING_SECONDS = 70


def run_workloads(
    client: Any, tenant: Tenant, options: Options, plan: Sequence[Workload], report: Report
) -> None:
    base = f"/api/v1/banks/{tenant.bank_id}/bi"
    backoffs = 0
    reader_headers = dict(tenant.human_headers)
    for workload in plan:
        url = f"{base}/{workload.surface}"
        measurement = QueryMeasurement(
            name=workload.name,
            query_class=workload.query_class,
            surface=workload.surface,
            note=workload.note,
        )
        # Warm-ups are discarded: the first call of a shape pays for the plan
        # cache and the buffer cache, which a dashboard's second reader does not.
        attempt = 0
        while len(measurement.route_ms) < options.repeats:
            started = time.perf_counter()
            response = client.post(url, headers=reader_headers, json=workload.body)
            route_ms = (time.perf_counter() - started) * 1000
            if response.status_code == 401:
                # The access token expired mid-run. Mint another and re-measure
                # this attempt; nothing about the query changed.
                reader_headers = mint_human_headers()
                continue
            if response.status_code == 429:
                backoffs += 1
                wait = min(int(response.headers.get("Retry-After", "5")), _BACKOFF_CEILING_SECONDS)
                print(f"    read budget reached; waiting {wait}s", flush=True)
                time.sleep(wait + 1)
                continue
            if response.status_code != 200:
                raise SystemExit(
                    f"{workload.name} failed ({response.status_code}): {response.text[:800]}"
                )
            payload = response.json()
            attempt += 1
            if attempt <= options.warmups:
                continue
            measurement.route_ms.append(route_ms)
            measurement.db_ms.append(float(payload.get("elapsed_ms", 0)))
            measurement.rows = len(payload.get("rows", []))
            measurement.used_aggregate = payload.get("used_aggregate")
        _assert_class(workload, measurement)
        report.queries.append(measurement)
        print(
            f"  {workload.query_class:<9} {workload.name[:58]:<58} "
            f"p50={measurement.route_p50:8.1f}ms p95={measurement.route_p95:8.1f}ms "
            f"rows={measurement.rows:>5} agg={measurement.used_aggregate}",
            flush=True,
        )
    if backoffs:
        report.notes.append(
            f"The product's per-principal read budget (120 reads / 60 s) was reached "
            f"{backoffs} time(s); the run waited out ``Retry-After`` and re-measured. "
            "No limit was raised."
        )


def _assert_class(workload: Workload, measurement: QueryMeasurement) -> None:
    """A class claim is checked against the compiler's own answer."""

    if workload.query_class == CLASS_AGGREGATE and measurement.used_aggregate is not True:
        raise SystemExit(
            f"{workload.name} is declared an aggregate-table query but the compiler "
            "did not select the aggregate table; the classification is wrong."
        )
    if workload.query_class == CLASS_FACT and measurement.used_aggregate is not False:
        raise SystemExit(
            f"{workload.name} is declared a fact-grain query but the compiler served it "
            "from the aggregate table; the classification is wrong."
        )


# --- diagnosis ----------------------------------------------------------------------------------


def explain_workload(tenant: Tenant, workload: Workload) -> str:
    """``EXPLAIN (ANALYZE, BUFFERS)`` for one workload's compiled statement.

    The statement is the compiler's own — compiled once for the real engine's
    dialect with ``render_postcompile`` so the expanding ``IN`` lists become
    real placeholders, then handed to the driver with the compiler's parameter
    dictionary. Nothing about the query is rewritten for the plan, or the plan
    would describe a different statement from the one that was timed.
    """

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.domain.bi.catalogue import catalogue  # noqa: PLC0415
    from app.schemas.bi import BiQuery  # noqa: PLC0415
    from app.services.bi.compiler import compile_query  # noqa: PLC0415

    body = workload.body.get("query", workload.body)
    query = BiQuery.model_validate(body)
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        compiled = compile_query(
            session,
            catalogue(),
            query,
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
        )
        statement, _ = compiled.with_row_cap(5_000)
        connection = session.connection()
        rendered = statement.compile(
            dialect=connection.dialect,
            compile_kwargs={"render_postcompile": True},
        )
        plan = connection.exec_driver_sql(
            f"EXPLAIN (ANALYZE, BUFFERS) {rendered!s}", dict(rendered.params)
        ).all()
        return "\n".join(str(row[0]) for row in plan)
    finally:
        session.close()


def reported_currencies(tenant: Tenant, *, as_of: date, limit: int = 4) -> list[str]:
    """The units the built book actually reports in, largest first.

    Read out of the mart rather than named in code: a benchmark filter must not
    assert what currency an institution reports in (``docs/rbac.md`` neutrality
    rule), and the simulator's book states its own.
    """

    from sqlalchemy import desc, func, select  # noqa: PLC0415

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.models.bi import BiAggPositionDaily  # noqa: PLC0415

    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        rows = session.execute(
            select(BiAggPositionDaily.currency, func.sum(BiAggPositionDaily.row_count))
            .where(
                BiAggPositionDaily.organization_id == tenant.organization_id,
                BiAggPositionDaily.bank_id == tenant.bank_id,
                BiAggPositionDaily.as_of_date == as_of,
            )
            .group_by(BiAggPositionDaily.currency)
            .order_by(desc(func.sum(BiAggPositionDaily.row_count)))
            .limit(limit)
        ).all()
    finally:
        session.close()
    return [str(row[0]) for row in rows]


#: How a plan names one child of a partitioned parent (``…_y2026m08`` / ``…_y2026``).
_PARTITION_IN_PLAN = re.compile(r"Scan (?:using \S+ )?on ([a-z_]+_y\d{4}(?:m\d{2})?)\b")


def partitions_touched(plan: str) -> int:
    """How many partition children the plan actually scanned.

    The single most useful number in a BI plan: a query asking for one date that
    scans every month is reading the whole history to answer a question about a
    day, and the latency says nothing about why.
    """

    return len(set(_PARTITION_IN_PLAN.findall(plan)))


def _explain_subjects(report: Report) -> list[QueryMeasurement]:
    """Which queries get a plan: every miss, plus the slowest of each class.

    The requirement is a diagnosis for each MISS. The slowest of each class is
    added because a plan is the only evidence that a pass was earned by the
    index, the aggregate-table selection and partition pruning rather than by a
    small table — and because a report with no plan at all cannot be audited.
    """

    subjects: dict[str, QueryMeasurement] = {}
    for measurement in report.queries:
        if verdict(measurement.route_p95, query_class=measurement.query_class) == "FAIL":
            subjects[measurement.name] = measurement
    for query_class in (CLASS_AGGREGATE, CLASS_FACT, CLASS_EXPLORE):
        of_class = [m for m in report.queries if m.query_class == query_class]
        if of_class:
            slowest = max(of_class, key=lambda m: m.route_p95)
            subjects[slowest.name] = slowest
    return list(subjects.values())


def table_row_counts(tenant: Tenant) -> dict[str, int]:
    from sqlalchemy import func, select  # noqa: PLC0415

    from app.db.session import get_sessionmaker  # noqa: PLC0415
    from app.models import bi as bi_models  # noqa: PLC0415

    tables = (
        bi_models.BiFactPositionDaily,
        bi_models.BiFactPositionEom,
        bi_models.BiAggPositionDaily,
        bi_models.BiFactLoanEvent,
        bi_models.BiFactGlMonthly,
        bi_models.BiFactEngineMetric,
        bi_models.BiFactTarget,
        bi_models.BiDimBranch,
        bi_models.BiDimProduct,
        bi_models.BiDimCounterparty,
        bi_models.BiDimGlAccount,
        bi_models.BiDimDate,
    )
    session = get_sessionmaker()()
    try:
        session.info["organization_id"] = tenant.organization_id
        counts: dict[str, int] = {}
        for model in tables:
            counts[model.__tablename__] = session.execute(
                select(func.count()).select_from(model)
            ).scalar_one()
        return counts
    finally:
        session.close()


# --- the report ----------------------------------------------------------------------------------


def _class_p95(report: Report, query_class: str) -> float | None:
    values = [m.route_p95 for m in report.queries if m.query_class == query_class]
    return max(values) if values else None


def render(report: Report) -> str:  # noqa: PLR0912, PLR0915 - one linear document
    """The measurements, with every target printed beside its measurement."""

    lines: list[str] = []
    out = lines.append
    out(f"# BI benchmark — {report.started_at}")
    out("")
    out("## Method")
    out("")
    out(f"- Database: `{report.database.get('version', '?')}`")
    out(
        f"- Role: `{report.role.get('role')}` "
        f"(rolsuper={report.role.get('rolsuper')}, "
        f"rolbypassrls={report.role.get('rolbypassrls')})"
    )
    proof = report.rls_proof
    out(
        f"- RLS proof on `{proof.get('table')}`: own tenant "
        f"{proof.get('own_tenant', 0):,} rows / decoy tenant "
        f"{proof.get('decoy_tenant')} / no tenant GUC {proof.get('no_tenant_guc')}; "
        f"FORCE ROW LEVEL SECURITY={proof.get('force_row_security')} → "
        f"enforced={proof.get('enforced')}"
    )
    for key, value in sorted(report.dataset.items()):
        out(f"- {key.replace('_', ' ')}: {value}")
    if report.ingest_seconds:
        out(f"- Ingestion wall time: {report.ingest_seconds / 60:,.1f} min")
    else:
        out("- Ingestion wall time: not measured in this run (the book was already ingested)")
    out(f"- Repeats per query: {report.options.repeats} (after {report.options.warmups} warm-up)")
    out("")
    out("## Mart build")
    out("")
    succeeded = [b for b in report.builds if b.outcome == "succeeded"]
    if succeeded:
        walls = [b.wall_seconds for b in succeeded]
        out("| Build | Value | Target |")
        out("|---|---|---|")
        out(f"| dates built | {len(succeeded)} | — |")
        out(f"| total | {sum(walls) / 60:,.1f} min | — |")
        out(f"| median date | {statistics.median(walls):,.2f} s | — |")
        out(
            f"| slowest date | {max(walls):,.2f} s | "
            f"< {COLUMNAR_BUILD_SECONDS:,} s per daily build |"
        )
        if report.skip_build is not None:
            out(
                f"| unchanged fingerprint (re-run) | "
                f"{report.skip_build.wall_seconds * 1000:,.0f} ms "
                f"({report.skip_build.outcome}) | — |"
            )
        out("")
        scopes: dict[str, list[float]] = {}
        for build in succeeded:
            for scope, seconds in build.scope_seconds.items():
                scopes.setdefault(scope, []).append(seconds)
        out("| Scope | median s | p95 s | max s | share of median build |")
        out("|---|---|---|---|---|")
        median_total = statistics.median(walls)
        for scope, values in sorted(scopes.items(), key=lambda kv: -statistics.median(kv[1])):
            median = statistics.median(values)
            share = median / median_total * 100 if median_total else 0
            out(
                f"| {scope} | {median:,.2f} | {percentile(values, 0.95):,.2f} | "
                f"{max(values):,.2f} | {share:,.0f} % |"
            )
        out("")
    out("## Mart size")
    out("")
    out("| Table | Rows |")
    out("|---|---|")
    for table, count in report.table_rows.items():
        out(f"| `{table}` | {count:,} |")
    out("")
    out("## Query latency")
    out("")
    out(
        "Route wall time is what a reader waits for (authorization, compile, execute, log); "
        "db is the executor's own `elapsed_ms`."
    )
    out("")
    out("| Class | Query | rows | agg | route p50 | route p95 | db p50 | db p95 | Target | |")
    out("|---|---|---|---|---|---|---|---|---|---|")
    for m in report.queries:
        out(
            f"| {m.query_class} | {m.name} | {m.rows:,} | "
            f"{'yes' if m.used_aggregate else 'no'} | "
            f"{m.route_p50:,.0f} ms | {m.route_p95:,.0f} ms | "
            f"{m.db_p50:,.0f} ms | {m.db_p95:,.0f} ms | "
            f"{target_text(m.query_class)} | {verdict(m.route_p95, query_class=m.query_class)} |"
        )
    out("")
    out("## Targets")
    out("")
    out("| Measurement | Target | Worst measured | Verdict |")
    out("|---|---|---|---|")
    for query_class in (CLASS_AGGREGATE, CLASS_FACT, CLASS_EXPLORE):
        worst = _class_p95(report, query_class)
        if worst is None:
            continue
        out(
            f"| {query_class} queries (worst p95 of the class) | {target_text(query_class)} | "
            f"{worst:,.0f} ms | {verdict(worst, query_class=query_class)} |"
        )
    out("")
    out("## Columnar-fallback thresholds")
    out("")
    fact_rows = report.table_rows.get("bi_fact_position_daily", 0) + report.table_rows.get(
        "bi_fact_position_eom", 0
    )
    longest = max((b.wall_seconds for b in report.builds), default=0.0)
    crossed = columnar_thresholds_crossed(
        explore_p95_ms=_class_p95(report, CLASS_EXPLORE),
        fact_rows=fact_rows,
        longest_daily_build_s=longest,
    )
    out(
        f"- Explore p95: {_class_p95(report, CLASS_EXPLORE) or 0:,.0f} ms "
        f"(threshold > {COLUMNAR_EXPLORE_P95_MS:,} ms)"
    )
    out(f"- Fact rows: {fact_rows:,} (threshold > {COLUMNAR_FACT_ROWS:,})")
    out(f"- Longest single build: {longest:,.1f} s (threshold > {COLUMNAR_BUILD_SECONDS:,} s)")
    out("")
    if crossed:
        out("**Crossed:**")
        for sentence in crossed:
            out(f"- {sentence}")
    else:
        out("**No threshold crossed** — the columnar fallback is not indicated by these numbers.")
    if report.explains:
        out("")
        out("## Plans (EXPLAIN ANALYZE, BUFFERS) — every miss, and the slowest of each class")
        for name, plan in report.explains.items():
            out("")
            out(f"### {name}")
            out("")
            out(f"Partition children scanned: **{partitions_touched(plan)}**.")
            out("")
            out("```")
            out(plan)
            out("```")
    if report.notes:
        out("")
        out("## Notes")
        out("")
        for note in report.notes:
            out(f"- {note}")
    return "\n".join(lines) + "\n"


# --- orchestration --------------------------------------------------------------------------------


def _parse(argv: Sequence[str] | None) -> Options:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database-url", required=True, help="the DISPOSABLE Postgres URL")
    parser.add_argument("--panels-dir", default="/tmp/bi_benchmark/panels")  # noqa: S108
    parser.add_argument("--dates", type=int, default=60, help="business dates to build")
    parser.add_argument("--repeats", type=int, default=7, help="measured repeats per query")
    parser.add_argument("--warmups", type=int, default=2, help="discarded repeats per query")
    parser.add_argument("--last-date", default=None, help="ISO date the book's last as-of maps to")
    parser.add_argument("--report", default=None, help="write the markdown report here")
    parser.add_argument(
        "--phase",
        action="append",
        choices=("generate", "ingest", "build", "query", "all"),
        default=None,
    )
    parser.add_argument("--no-explain", action="store_true", help="skip EXPLAIN on misses")
    parser.add_argument("--simulator-seed", type=int, default=None)
    parser.add_argument(
        "--position-sample",
        type=float,
        default=1.0,
        help=(
            "keep this fraction of the roster per date (default 1.0). The sample is a "
            "stable hash of the account reference, so the SAME positions persist across "
            "every date and a position's history is unbroken."
        ),
    )
    parser.add_argument(
        "--no-gl-balances",
        action="store_true",
        help=(
            "push the chart of accounts without balances. Required with "
            "--position-sample below 1.0: a sampled sub-ledger cannot tie to the whole "
            "book's ledger, and gl_subledger_reconciliation would BLOCK the push — "
            "correctly. With no balance on either side the control has nothing to "
            "reconcile; it is not switched off."
        ),
    )
    args = parser.parse_args(argv)
    phases = tuple(args.phase or ("all",))
    if "all" in phases:
        phases = ("generate", "ingest", "build", "query")
    if not 0.0 < args.position_sample <= 1.0:
        raise SystemExit("--position-sample must be in (0, 1]")
    if args.position_sample < 1.0 and not args.no_gl_balances:
        raise SystemExit(
            "--position-sample below 1.0 needs --no-gl-balances: a sampled sub-ledger "
            "cannot tie to the whole book's general ledger, and the push would be "
            "BLOCKED by gl_subledger_reconciliation. That control is not bypassed here."
        )
    return Options(
        database_url=args.database_url,
        panels_dir=Path(args.panels_dir),
        dates=args.dates,
        repeats=args.repeats,
        warmups=args.warmups,
        last_date=date.fromisoformat(args.last_date) if args.last_date else None,
        report=Path(args.report) if args.report else None,
        phases=phases,
        explain=not args.no_explain,
        simulator_seed=args.simulator_seed,
        position_sample=args.position_sample,
        gl_balances=not args.no_gl_balances,
    )


def _configure(options: Options) -> None:
    """Point the application at the disposable database and nothing else."""

    os.environ["DATABASE_URL"] = options.database_url
    os.environ["APP_ENV"] = "test"
    os.environ["BI_ENABLED"] = "1"
    os.environ["BI_MART_ENQUEUE_ENABLED"] = "0"
    os.environ["BI_SCHEDULER_ENABLED"] = "0"
    os.environ["RUN_INPROCESS_WORKER"] = "0"
    os.environ.setdefault("AUTH_JWT_SECRET", "bi-benchmark-jwt-secret-not-for-production-0")
    os.environ.setdefault(
        "IMPERSONATION_JWT_SECRET", "bi-benchmark-impersonation-secret-not-for-prod"
    )
    os.environ["CREDENTIAL_VAULT_MASTER_KEY"] = ""
    os.environ["SSO_INTERNAL_KEY"] = ""
    for flag in (
        "OFFICIAL_RUN_ENABLED",
        "MARKET_DATA_PULL_ENABLED",
        "TEMENOS_PULL_ENABLED",
        "DATABASE_DIRECT_HEALTH_ENABLED",
        "LIVE_REFRESH_ENABLED",
        "DESK_CAPTURE_ENABLED",
    ):
        os.environ[flag] = "0"

    from app.core.config import get_settings  # noqa: PLC0415
    from app.db.session import get_engine  # noqa: PLC0415

    get_settings.cache_clear()
    get_engine.cache_clear()


def _client() -> Any:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.features.ingest_data import get_ingestion_storage  # noqa: PLC0415
    from app.integrations.storage.s3 import get_object_storage  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415
    from tests.support.inmemory_storage import InMemoryStorageClient  # noqa: PLC0415

    # Object storage is a real dependency of the push flow (staged pages live in
    # the bank's temp tier). The in-memory client is the contract-tested
    # implementation; a benchmark must not push a synthetic book at a shared
    # MinIO, and the storage round-trip is not what is being measured.
    storage = InMemoryStorageClient()
    app = create_app()
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_ingestion_storage] = lambda: storage
    return TestClient(app, raise_server_exceptions=True)


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0912, PLR0915 - one linear run
    options = _parse(argv)
    require_disposable_target(options.database_url)
    _configure(options)

    from sqlalchemy import text  # noqa: PLC0415

    from app.db.session import get_sessionmaker  # noqa: PLC0415

    report = Report(
        started_at=time.strftime("%Y-%m-%d %H:%M:%S %z"),
        options=options,
    )
    session = get_sessionmaker()()
    try:
        report.role = verify_role(session)
        report.database["version"] = session.execute(text("SELECT version()")).scalar_one()
    finally:
        session.close()
    print(f"database: {report.database['version']}")
    print(f"role: {json.dumps(report.role)}")

    if "generate" in options.phases:
        print("\n== generate ==")
        report.dataset.update(generate_panels(options))
        print(json.dumps({k: v for k, v in report.dataset.items()}, default=str))

    with _client() as client:
        if "ingest" in options.phases:
            print("\n== ingest (Data Engine push API) ==")
            tenant = scaffold_tenant(client)
            as_of_dates = ingest(client, tenant, options, report)
        else:
            tenant = _existing_tenant(client)
            as_of_dates = snapshot_dates_for(tenant)[-options.dates :]
            report.dataset.setdefault("dates", len(as_of_dates))

        if "build" in options.phases:
            print("\n== build ==")
            build_marts(tenant, as_of_dates, report)
        else:
            # A query-only re-run still reports the build, read from the
            # builder's own records rather than omitted.
            report.builds = recorded_builds(tenant)
            if report.builds:
                report.notes.append(
                    "Build timings were read from `bi_mart_builds` (the builder's own "
                    "`started_at`/`finished_at` and per-scope `elapsed_ms`), not measured "
                    "again in this run."
                )

        report.table_rows = table_row_counts(tenant)
        session = get_sessionmaker()()
        try:
            report.rls_proof = prove_rls_enforced(
                session,
                organization_id=tenant.organization_id,
                table="bi_fact_position_daily",
            )
        finally:
            session.close()
        if not report.rls_proof.get("own_tenant"):
            raise SystemExit(
                f"nothing to measure: the marts hold no rows for {tenant.bank_id} "
                f"({report.rls_proof}). Run --phase ingest --phase build first."
            )
        if not report.rls_proof.get("enforced"):
            raise SystemExit(
                "refusing to report: the marts were readable outside the tenant policy "
                f"({report.rls_proof}). The measurements would be optimistic."
            )

        if "query" in options.phases:
            print("\n== query ==")
            if not as_of_dates:
                raise SystemExit("no built dates to query")
            as_of = as_of_dates[-1]
            window_start = as_of_dates[max(0, len(as_of_dates) - 12)]
            currencies = reported_currencies(tenant, as_of=as_of)
            if not currencies:
                raise SystemExit(f"the mart holds no position rows at {as_of}")
            report.dataset["reporting_units_in_book"] = len(currencies)
            plan = workloads(
                as_of=as_of,
                window_start=window_start,
                full_start=as_of_dates[0],
                currencies=currencies,
            )
            run_workloads(client, tenant, options, plan, report)
            if options.explain:
                by_name = {w.name: w for w in plan}
                for measurement in _explain_subjects(report):
                    print(f"  EXPLAIN {measurement.name}")
                    try:
                        report.explains[measurement.name] = explain_workload(
                            tenant, by_name[measurement.name]
                        )
                    except Exception as exc:  # noqa: BLE001 - never lose the measurements
                        # A plan is diagnosis, not measurement. Losing the whole
                        # report because a plan could not be rendered would throw
                        # away the run it took an hour to produce.
                        report.explains[measurement.name] = f"plan unavailable: {exc!r}"
                        report.notes.append(
                            f"The plan for {measurement.name} could not be rendered; "
                            "its measurements above are unaffected."
                        )

    rendered = render(report)
    print("\n" + rendered)
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(rendered, encoding="utf-8")
        print(f"report written to {options.report}")
    return 0


def _existing_tenant(client: Any) -> Tenant:
    """The tenant a previous phase left in the database."""

    _ = client  # the app's engine is resolved by the client's construction
    return _tenant_with_reader()


__all__ = [
    "CLASS_AGGREGATE",
    "CLASS_EXPLORE",
    "CLASS_FACT",
    "COLUMNAR_BUILD_SECONDS",
    "COLUMNAR_EXPLORE_P95_MS",
    "COLUMNAR_FACT_ROWS",
    "TARGET_AGGREGATE_P95_MS",
    "TARGET_FACT_P95_HIGH_MS",
    "TARGET_FACT_P95_LOW_MS",
    "chunked",
    "keeps_reference",
    "mint_human_headers",
    "recorded_builds",
    "columnar_thresholds_crossed",
    "main",
    "partitions_touched",
    "percentile",
    "remap_dates",
    "target_text",
    "verdict",
]


if __name__ == "__main__":
    raise SystemExit(main())
