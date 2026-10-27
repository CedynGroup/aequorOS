"""Exercise the test connection lint consumer, including its temporary ratchet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import test_database_guard as guard


def test_repository_connection_allowlist_is_current() -> None:
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
    ],
)
def test_guard_rejects_new_direct_constructors(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1


def test_guard_warns_for_legacy_calls_and_rejects_growth_or_stale_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    module = tests / "test_example.py"
    source = "import sqlalchemy as sa\nsa.create_engine('sqlite://')\n"
    module.write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps(guard.inventory(tmp_path)))
    args = ["--root", str(tmp_path), "--allowlist", str(allowlist)]
    assert guard.main(args) == 0
    assert "WARNING: 1 legacy DB constructor" in capsys.readouterr().out
    assert guard.main([*args, "--strict"]) == 1
    module.write_text(source + "sa.create_engine('sqlite://')\n")
    assert guard.main(args) == 1
    module.write_text("pass")
    assert guard.main(args) == 1


def test_guard_accepts_support_layer_and_unrelated_connect_calls(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    support = tests / "support"
    support.mkdir(parents=True)
    (support / "database.py").write_text("import psycopg\npsycopg.connect(url)")
    (tests / "test_example.py").write_text(
        "# create_engine('unused')\ntext = 'psycopg.connect(url)'\n"
        "client.connect()\nget_sessionmaker()\n"
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 0
