"""Exports release API transactions before BI waits and commit delivery atomically."""

from __future__ import annotations

import os
import time
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as clock_time
from typing import Annotated, Any, cast

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api import deps
from app.bi.api import export_bi, read_bi
from app.bi.models.marts import BiQueryLog
from app.bi.service import query_log
from app.core.config import get_settings
from app.db.session import get_bi_engine
from app.models import AuditEvent, Bank, Job, User
from tests.bi.api.test_bi_exports import SUMMARY_QUERY
from tests.bi.api.test_bi_routes import (
    AGGREGATE_ONLY,
    BANK_ID,
    BASE,
    grant_only,
    seed_bi_mart,
)
from tests.db import test_database_roles as role_tests
from tests.support.helpers import ORG_1, USER_1

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
role_schema = role_tests.role_schema
administrator = role_tests.administrator
role_engines = role_tests.role_engines

PIVOT_QUERY: dict[str, Any] = {
    **SUMMARY_QUERY,
    "pivot": {"dimension": "loan.sector"},
}


@pytest.fixture(scope="module")
def export_authority(administrator: Engine) -> int:
    with Session(administrator) as db:
        db.add(User(id=USER_1, organization_id=ORG_1, email="export@example.test"))
        seed_bi_mart(db)
        db.commit()
        return grant_only(db, AGGREGATE_ONLY)


def delivery_counts(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as db:
        return (
            cast(int, db.scalar(select(func.count()).select_from(BiQueryLog))),
            cast(
                int,
                db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.event_type.like("bi.export.%"))
                ),
            ),
            cast(
                int,
                db.scalar(select(func.count()).select_from(Job).where(Job.job_type == "bi_export")),
            ),
        )


@dataclass
class ExportScenario:
    client: TestClient
    administrator: Engine
    bi_login: str
    api_pids: list[int] = field(default_factory=list)
    idle_observations: list[tuple[str, bool]] = field(default_factory=list)

    def request(self, query: dict[str, Any] = SUMMARY_QUERY):
        return self.client.post(f"{BASE}/export", json={"query": query, "format": "csv"})

    def _api_state(self, observer: Session) -> tuple[str, bool]:
        observer.rollback()
        row = observer.execute(
            text("SELECT state, xact_start IS NULL FROM pg_stat_activity WHERE pid=:pid"),
            {"pid": self.api_pids[-1]},
        ).one()
        return cast(tuple[str, bool], tuple(row))

    def wait_for_export(self, query: dict[str, Any]):
        with (
            self.administrator.connect() as blocker,
            Session(self.administrator) as observer,
            ThreadPoolExecutor() as jobs,
        ):
            blocker.execute(
                text(
                    "LOCK TABLE bi_agg_position_daily, bi_fact_position_daily "
                    "IN ACCESS EXCLUSIVE MODE"
                )
            )
            future = jobs.submit(self.request, query)
            try:
                deadline = time.monotonic() + 15
                while not observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE usename=:login AND application_name='aequoros-bi' "
                        "AND wait_event_type='Lock')"
                    ),
                    {"login": self.bi_login},
                ):
                    observer.rollback()
                    assert not future.done(), "Export did not reach the BI pool"
                    assert time.monotonic() < deadline, "BI read did not wait on the mart lock"
                    time.sleep(0.05)
                self.idle_observations.append(self._api_state(observer))
                # Keep BI blocked while verifying the API stays outside a transaction.
                time.sleep(1.5)
                self.idle_observations.append(self._api_state(observer))
            finally:
                blocker.rollback()
            return future.result(timeout=15)

    @contextmanager
    def reject_delivery_write(self, table: str) -> Generator[None]:
        predicate = (
            "NEW.surface = 'export'"
            if table == "bi_query_log"
            else "NEW.event_type LIKE 'bi.export.%'"
        )
        with self.administrator.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION reject_export_delivery() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF {predicate} THEN RAISE EXCEPTION 'delivery rejected'; END IF; "
                    "RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    f"CREATE TRIGGER reject_export_delivery BEFORE INSERT ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION reject_export_delivery()"
                )
            )
        try:
            yield
        finally:
            with self.administrator.begin() as connection:
                connection.execute(text(f"DROP TRIGGER reject_export_delivery ON {table}"))
                connection.execute(text("DROP FUNCTION reject_export_delivery()"))


@pytest.fixture
def export_scenario(
    administrator: Engine,
    role_engines: dict[str, Engine],
    export_authority: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ExportScenario]:
    url = role_engines["aqs_api"].url
    options = " ".join(url.normalized_query.get("options", ()))
    api_engine = create_engine(
        url,
        connect_args={"options": options + " -c idle_in_transaction_session_timeout=1000"},
    )
    bi_url = role_engines["aqs_bi"].url.render_as_string(hide_password=False)
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("BI_DATABASE_URL", bi_url)
    get_settings.cache_clear()
    get_bi_engine.cache_clear()
    app = FastAPI()
    app.include_router(export_bi.router, prefix="/api/v1")
    scenario = ExportScenario(
        TestClient(app),
        administrator,
        cast(str, role_engines["aqs_bi"].url.username),
    )

    def session_dependency() -> Iterator[Session]:
        with Session(api_engine, expire_on_commit=True) as db:
            db.info["organization_id"] = ORG_1
            assert db.scalar(text("SHOW idle_in_transaction_session_timeout")) == "1s"
            scenario.api_pids.append(cast(int, db.scalar(text("SELECT pg_backend_pid()"))))
            try:
                yield db
            except Exception:
                db.rollback()
                raise

    def admitted(
        db: Annotated[Session, Depends(deps.get_tenant_db_session)],
    ) -> read_bi.BiReadAccess:
        bank = db.get(Bank, BANK_ID)
        assert bank is not None
        return read_bi.BiReadAccess(
            ctx=deps.TenantContext(
                organization_id=ORG_1, actor_user_id=USER_1, authorization_version=export_authority
            ),
            bank=bank,
            principal_user_id=USER_1,
            authorization_version=export_authority,
        )

    app.dependency_overrides[deps.get_tenant_db_session] = session_dependency
    app.dependency_overrides[read_bi.require_bi_read] = admitted
    try:
        with scenario.client:
            yield scenario
    finally:
        api_engine.dispose()
        get_bi_engine(bi_url).dispose()
        get_bi_engine.cache_clear()
        get_settings.cache_clear()


@pytest.mark.parametrize("query", [SUMMARY_QUERY, PIVOT_QUERY], ids=["final-query", "pivot-probe"])
def test_slow_export_holds_no_api_transaction(
    export_scenario: ExportScenario, query: dict[str, Any]
) -> None:
    response = export_scenario.wait_for_export(query)
    assert response.status_code == 200, response.text
    assert export_scenario.idle_observations == [("idle", True), ("idle", True)]
    assert "Exported by,export@example.test" in response.text
    with Session(export_scenario.administrator) as db:
        log = db.scalar(select(BiQueryLog).order_by(BiQueryLog.queried_at.desc()))
        audit = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "bi.export.delivered")
            .order_by(AuditEvent.created_at.desc())
        )
        assert log is not None and audit is not None
        assert log.row_count == audit.details["row_count"] == 2
        assert log.build_fingerprint == audit.details["build_fingerprint"]
        assert log.build_fingerprint is not None
        assert log.build_fingerprint in response.text


@pytest.mark.parametrize("table", ["audit_events", "bi_query_log"])
@pytest.mark.parametrize("threshold", [1, 10_000], ids=["queued", "inline"])
def test_delivery_failure_rolls_back_audit_log_and_job(
    export_scenario: ExportScenario, monkeypatch: pytest.MonkeyPatch, table: str, threshold: int
) -> None:
    monkeypatch.setenv("BI_EXPORT_ASYNC_THRESHOLD_ROWS", str(threshold))
    get_settings.cache_clear()
    before = delivery_counts(export_scenario.administrator)
    with (
        export_scenario.reject_delivery_write(table),
        pytest.raises(DBAPIError, match="delivery rejected"),
    ):
        export_scenario.request()
    assert delivery_counts(export_scenario.administrator) == before


def test_api_login_export_creates_fresh_query_log_month(
    export_scenario: ExportScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    with export_scenario.administrator.connect() as connection:
        month = cast(
            date,
            connection.scalar(
                text("SELECT (date_trunc('month', CURRENT_DATE) + interval '1 month')::date")
            ),
        )
        assert month is not None
        child = f"bi_query_log_y{month.year}m{month.month:02d}"
        assert connection.scalar(text("SELECT to_regclass(:child)"), {"child": child}) is None
    queried_at = datetime.combine(month, clock_time(12), tzinfo=UTC)
    monkeypatch.setattr(query_log, "utc_now", lambda: queried_at)

    response = export_scenario.request()

    assert response.status_code == 200, response.text
    with export_scenario.administrator.connect() as connection:
        row = connection.execute(
            text(
                "SELECT tableoid::regclass::text, build_fingerprint FROM bi_query_log "
                "WHERE queried_at=:queried_at"
            ),
            {"queried_at": queried_at},
        ).one()
        assert row[0] == child
        assert row[1] in response.text
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid=CAST(:child AS regclass)"
            ),
            {"child": child},
        )


@pytest.mark.parametrize("offset", [-2, 2])
def test_api_login_refuses_out_of_range_query_log_month(
    role_engines: dict[str, Engine], offset: int
) -> None:
    with role_engines["aqs_api"].connect() as connection, pytest.raises(DBAPIError) as error:
        connection.execute(
            text(
                "SELECT bi_ensure_month_partition('bi_query_log'::regclass, "
                "(date_trunc('month', CURRENT_DATE) + make_interval(months => :offset))::date)"
            ),
            {"offset": offset},
        )
    assert getattr(error.value.orig, "sqlstate", None) == "22023"


@pytest.mark.parametrize(
    "parent", ["bi_fact_position_daily", "bi_agg_position_daily", "bi_fact_loan_event"]
)
def test_api_login_cannot_create_mart_partitions(
    role_engines: dict[str, Engine], parent: str
) -> None:
    with role_engines["aqs_api"].connect() as connection, pytest.raises(DBAPIError) as error:
        connection.execute(
            text("SELECT bi_ensure_month_partition(CAST(:parent AS regclass), CURRENT_DATE)"),
            {"parent": parent},
        )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize(
    "function", ["bi_drop_month_partition", "bi_ensure_year_partition", "bi_drop_year_partition"]
)
def test_api_login_has_no_other_partition_capability(
    role_engines: dict[str, Engine], function: str
) -> None:
    with role_engines["aqs_api"].connect() as connection, pytest.raises(DBAPIError) as error:
        connection.execute(text(f"SELECT {function}('bi_query_log'::regclass, CURRENT_DATE)"))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


def test_api_partition_definer_pins_search_path(administrator: Engine) -> None:
    with administrator.connect() as connection:
        row = connection.execute(
            text(
                "SELECT prosecdef, proconfig FROM pg_proc "
                "WHERE oid='bi_ensure_month_partition(regclass,date)'::regprocedure"
            )
        ).one()
        assert row[0]
        assert "search_path=pg_catalog, pg_temp" in row[1]
        assert "TimeZone=UTC" in row[1]
