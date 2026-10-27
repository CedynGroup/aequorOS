"""Exercise unconditional test connection enforcement and lexical binding resolution."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from scripts import test_database_guard as guard


def test_repository_uses_only_shared_database_construction() -> None:
    assert guard.main([]) == 0


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy import create_engine as make\nmake('sqlite://')",
        "import sqlalchemy as sa\nsa.create_engine('sqlite://')",
        "import sqlalchemy.orm\nsqlalchemy.orm.sessionmaker()",
        "from sqlalchemy.orm import sessionmaker as sessions\nsessions()",
        "from sqlalchemy.ext.asyncio import create_async_engine as make\nmake(url)",
        "from sqlalchemy.ext.asyncio import async_sessionmaker\nasync_sessionmaker()",
        "import psycopg as pg\npg.connect(url)",
        "from psycopg import connect as raw\nraw(url)",
        "from psycopg import AsyncConnection as C\nC.connect(url)",
        "import sqlalchemy as sa\nmake = sa.create_engine\nother = make\nother(url)",
        "from sqlalchemy.orm import sessionmaker, Session\nsessionmaker[Session]()",
        "from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession\n"
        "async_sessionmaker[AsyncSession]()",
        "import psycopg as pg\npg.Connection[tuple[int]].connect(url)",
        "from psycopg import AsyncConnection as C\nC[tuple[int]].connect(url)",
        "import sqlite3 as db\ndb.connect(':memory:')",
        "from sqlite3.dbapi2 import connect as make\nmake(':memory:')",
        "import sqlite3 as db\ndb.Connection(':memory:')",
        "from sqlite3.dbapi2 import Connection as C\nC(':memory:')",
    ],
)
def test_guard_rejects_new_direct_constructors(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    assert guard.main(["--root", str(tmp_path)]) == 1


def test_exception_file_cannot_suppress_a_direct_constructor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    module = tests / "test_example.py"
    source = "import sqlalchemy as sa\nsa.create_engine('sqlite://')\n"
    module.write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps(guard.inventory(tmp_path)))
    assert guard.main(["--root", str(tmp_path)]) == 1
    assert "sqlalchemy.create_engine" in capsys.readouterr().out
    with pytest.raises(SystemExit) as rejected:
        _ = guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)])
    assert rejected.value.code == 2


def test_guard_accepts_support_layer_and_unrelated_connect_calls(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    support = tests / "support"
    support.mkdir(parents=True)
    (support / "database.py").write_text("import psycopg\npsycopg.connect(url)")
    (tests / "test_example.py").write_text(
        "# create_engine('unused')\ntext = 'psycopg.connect(url)'\n"
        "client.connect()\nget_sessionmaker()\n"
    )
    assert guard.main(["--root", str(tmp_path)]) == 0


@pytest.mark.parametrize(
    ("source", "count"),
    [
        (
            """
            from sqlalchemy.orm import sessionmaker
            def application():
                sessionmaker = get_sessionmaker()
                sessionmaker()
                def nested():
                    sessionmaker()
                sessionmaker()
            def direct():
                sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            def shadowed(sessionmaker):
                sessionmaker()
            def local():
                from application import sessionmaker
                sessionmaker()
            def direct():
                sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker, Session
            def shadowed(sessionmaker):
                sessionmaker[Session]()
            """,
            0,
        ),
        (
            """
            def direct():
                from sqlalchemy.orm import sessionmaker as make
                other = make
                other()
                make = get_sessionmaker()
                make()
                other()
            def unrelated():
                make()
            """,
            2,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            sessionmaker()
            saved = sessionmaker
            sessionmaker = get_sessionmaker()
            sessionmaker()
            saved()
            saved = get_sessionmaker()
            saved()
            """,
            2,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            sessionmaker: object = get_sessionmaker()
            sessionmaker()
            """,
            0,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            other, sessionmaker = sessionmaker, get_sessionmaker()
            sessionmaker()
            other()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            def unbound():
                sessionmaker()
                sessionmaker = get_sessionmaker()
            def direct():
                sessionmaker()
            """,
            1,
        ),
        (
            """
            def direct():
                def nested():
                    make()
                from sqlalchemy.orm import sessionmaker as make
            from sqlalchemy.orm import sessionmaker
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            class UsesApplication:
                sessionmaker = get_sessionmaker()
                sessionmaker()
                def direct(self):
                    sessionmaker()
            sessionmaker()
            """,
            2,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            shadowed, direct = lambda sessionmaker: sessionmaker(), lambda: sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            [sessionmaker() for sessionmaker in factories]
            {sessionmaker() for sessionmaker in factories}
            {key: sessionmaker() for key, sessionmaker in factories}
            (sessionmaker() for sessionmaker in factories)
            sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            def direct():
                [sessionmaker() for sessionmaker in factories]
                sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            for sessionmaker in sessionmaker():
                sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            def outer():
                sessionmaker = get_sessionmaker()
                def direct():
                    global sessionmaker
                    sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            def direct[T](value: T) -> T:
                sessionmaker()
                return value
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            async def direct():
                sessionmaker()
            async def application():
                sessionmaker = get_sessionmaker()
                sessionmaker()
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            class Holder:
                sessionmaker = get_sessionmaker()
                direct = [sessionmaker() for item in items]
            """,
            1,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            sessionmaker += other
            sessionmaker()
            """,
            0,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            (sessionmaker := get_sessionmaker())
            sessionmaker()
            """,
            0,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            with factory() as sessionmaker:
                sessionmaker()
            """,
            0,
        ),
        (
            """
            from sqlalchemy.orm import sessionmaker
            try:
                work()
            except Exception as sessionmaker:
                sessionmaker()
            """,
            0,
        ),
    ],
)
def test_guard_counts_only_bound_constructors(tmp_path: Path, source: str, count: int) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    module = tests / "test_example.py"
    module.write_text(dedent(source))
    assert guard.main(["--root", str(tmp_path)]) == (1 if count else 0)


@pytest.mark.parametrize("filename", ["helpers.py", "test_database.py", "nested/database.py"])
def test_guard_rejects_constructors_outside_support_owner(tmp_path: Path, filename: str) -> None:
    support = tmp_path / "tests/support"
    module = support / filename
    module.parent.mkdir(parents=True)
    module.write_text("import psycopg\npsycopg.connect(url)\n")
    (support / "database.py").write_text("import psycopg\npsycopg.connect(url)\n")
    assert guard.main(["--root", str(tmp_path)]) == 1
