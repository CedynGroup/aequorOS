"""Real PostgreSQL termination and workload isolation under service budgets."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, literal, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.bi.service.execution import run_select
from app.db.session import database_timeout_connect_args, get_bi_engine, get_engine
from tests.db import test_database_roles as role_tests
from tests.support.helpers import ORG_1

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
administrator = role_tests.administrator
role_schema = role_tests.role_schema

TIMEOUTS = {
    "aqs_api": ("10min", "5min"),
    "aqs_worker": ("2h", "30min"),
    "aqs_bi": ("5min", "10s"),
    "aqs_migrator": ("0", "0"),
    "aqs_owner": ("0", "0"),
}


@pytest.fixture(scope="module")
def timeout_logins(administrator: Engine) -> Iterator[dict[str, Engine]]:
    """Probe logins retain real role privileges without changing deployed passwords.

    Role settings are not inherited, so copy each configured role's two budgets
    onto its disposable login, then SET ROLE to exercise that service's rights.
    """
    engines: dict[str, Engine] = {}
    names: list[str] = []
    preparation = Path(__file__).parents[2].joinpath("scripts/database_session_timeouts.sql")
    try:
        with administrator.begin() as connection:
            legacy_before = cast(
                list[str] | None,
                connection.scalar(
                    text("SELECT rolconfig FROM pg_roles WHERE rolname=session_user")
                ),
            )
            for _ in range(2):
                connection.execute(text(preparation.read_text()))
            assert (
                connection.scalar(text("SELECT rolconfig FROM pg_roles WHERE rolname=session_user"))
                == legacy_before
            )
            for role in TIMEOUTS:
                name, password = f"timeout_probe_{uuid4().hex}", uuid4().hex
                connection.execute(text(f"CREATE ROLE {name} LOGIN PASSWORD '{password}'"))
                names.append(name)
                connection.execute(text(f"GRANT {role} TO {name} WITH INHERIT TRUE, SET TRUE"))
                settings = cast(
                    list[str],
                    connection.scalar(
                        text("SELECT rolconfig FROM pg_roles WHERE rolname=:role"), {"role": role}
                    ),
                )
                for setting in settings:
                    key, value = setting.split("=", 1)
                    if key not in {"idle_in_transaction_session_timeout", "statement_timeout"}:
                        continue
                    sql = cast(
                        str,
                        connection.scalar(
                            text(
                                "SELECT format('ALTER ROLE %I SET %I TO %L', "
                                "CAST(:name AS text), CAST(:key AS text), CAST(:value AS text))"
                            ),
                            {"name": name, "key": key, "value": value},
                        ),
                    )
                    connection.execute(text(sql))
                engines[role] = create_engine(
                    make_url(administrator.url).set(username=name, password=password)
                )
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()
        with administrator.begin() as connection:
            for name in names:
                connection.execute(text(f"DROP ROLE IF EXISTS {name}"))


@pytest.mark.parametrize("role", TIMEOUTS)
def test_role_configuration_is_effective_on_fresh_connections(
    timeout_logins: dict[str, Engine], role: str
) -> None:
    with timeout_logins[role].connect() as connection:
        connection.execute(text(f"SET ROLE {role}"))
        assert connection.scalar(text("SELECT current_user")) == role
        assert (
            connection.scalar(text("SHOW idle_in_transaction_session_timeout")),
            connection.scalar(text("SHOW statement_timeout")),
        ) == TIMEOUTS[role]


def test_api_idle_transaction_is_terminated_while_worker_and_bi_complete(
    timeout_logins: dict[str, Engine], administrator: Engine
) -> None:
    with (
        timeout_logins["aqs_api"].connect() as api,
        timeout_logins["aqs_worker"].connect() as worker,
        timeout_logins["aqs_bi"].connect() as bi,
        get_engine(administrator.url.render_as_string(hide_password=False)).connect() as legacy_api,
        ThreadPoolExecutor(max_workers=1) as jobs,
    ):
        api.execute(text("SET ROLE aqs_api"))
        worker.execute(text("SET ROLE aqs_worker"))
        bi.execute(text("SET ROLE aqs_bi"))
        pid = cast(int, api.scalar(text("SELECT pg_backend_pid()")))
        legacy_pid = cast(int, legacy_api.scalar(text("SELECT pg_backend_pid()")))
        # Fresh-login defaults are proven separately; shorten only this probe
        # to exercise PostgreSQL termination without a ten-minute test wait.
        for connection in (api, legacy_api):
            connection.execute(text("SET LOCAL idle_in_transaction_session_timeout = '1s'"))
        # A transaction-scoped advisory lock proves that termination releases locks.
        lock_key = int(uuid4().hex[:14], 16)
        api.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        assert (
            worker.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key})
            is False
        )
        long_job = jobs.submit(worker.scalar, text("SELECT 42 FROM pg_sleep(65)"))
        deadline = time.monotonic() + 75
        with administrator.connect() as observer:
            while observer.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=ANY(:pids))"),
                {"pids": [pid, legacy_pid]},
            ):
                observer.rollback()
                assert time.monotonic() < deadline, (
                    "API idle transaction survived the short probe budget"
                )
                time.sleep(1)
        for api_connection in (api, legacy_api):
            with pytest.raises(DBAPIError) as terminated:
                api_connection.execute(text("SELECT 1"))
            assert getattr(terminated.value.orig, "sqlstate", None) == "25P03"
        assert long_job.result(timeout=75) == 42
        assert (
            worker.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}) is True
        )
        assert bi.scalar(text("SELECT 42 FROM pg_sleep(1)")) == 42
        worker.commit()
        bi.commit()


def test_legacy_pools_keep_distinct_budgets_and_migration_options(administrator: Engine) -> None:
    url = administrator.url.render_as_string(hide_password=False)
    engines = {
        "api": get_engine(url),
        "worker": get_engine(url, "aequoros-worker"),
        "bi": get_bi_engine(url),
        "migrator": create_engine(
            url, connect_args=database_timeout_connect_args(url, "aequoros-migrator")
        ),
    }
    try:
        with administrator.connect() as observer:
            schema = cast(str, observer.scalar(text("SELECT current_schema()")))
        for name, engine in engines.items():
            with engine.connect() as connection:
                role = {
                    "api": "aqs_api",
                    "worker": "aqs_worker",
                    "bi": "aqs_bi",
                    "migrator": "aqs_migrator",
                }[name]
                assert (
                    connection.scalar(text("SHOW idle_in_transaction_session_timeout")),
                    connection.scalar(text("SHOW statement_timeout")),
                ) == TIMEOUTS[role]
                assert connection.scalar(text("SELECT current_schema()")) == schema
    finally:
        for engine in engines.values():
            engine.dispose()
        get_engine.cache_clear()
        get_bi_engine.cache_clear()


def test_bi_export_budget_survives_the_interactive_role_default(
    timeout_logins: dict[str, Engine],
) -> None:
    with Session(timeout_logins["aqs_bi"]) as session:
        assert (
            run_select(
                session,
                select(literal(42)).select_from(func.pg_sleep(11)),
                organization_id=ORG_1,
                timeout_ms=120_000,
            )[0][0]
            == 42
        )
        assert session.scalar(text("SHOW statement_timeout")) == "10s"
