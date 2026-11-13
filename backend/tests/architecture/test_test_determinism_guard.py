"""Snippet table for the AQPY determinism guard and its shrinking allowlist."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from scripts import test_determinism_guard as guard

BACKEND = Path(__file__).resolve().parents[2]
INSTANT = '"2026-01-01T00:00:00+00:00"'

#: Reads the guard must reject: direct members, both datetime import styles and
#: aliased imports of the platform utc_now helpers.
UNFROZEN_READS = [
    "from datetime import datetime\ndef test_now():\n    datetime.now()\n",
    "from datetime import datetime, UTC\ndef test_now_tz():\n    datetime.now(tz=UTC)\n",
    "from datetime import datetime\ndef test_utcnow():\n    datetime.utcnow()\n",
    "from datetime import datetime\ndef test_today():\n    datetime.today()\n",
    "import datetime\ndef test_module_style():\n    datetime.datetime.now()\n",
    "from datetime import date\ndef test_date_today():\n    date.today()\n",
    "import time\ndef test_time():\n    time.time()\n",
    "from time import time as clock\ndef test_aliased():\n    clock()\n",
    "from app.db.base import utc_now\ndef test_platform_utc_now():\n    utc_now()\n",
    "from app.db.base import utc_now as now\ndef test_renamed():\n    now()\n",
    "from app.attestation.service import api as attestation_api\n"
    "def test_attestation():\n"
    "    attestation_api.utc_now()\n",
    "def test_rebinding():\n    from app.db.base import utc_now\n    read = utc_now\n    read()\n",
    # A ticking travel does not pin an instant, and travel(None, tick=False)
    # freezes at the host clock read on entry; neither accepts its reads.
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_ticking_travel():\n"
    "    with time_machine.travel(DESTINATION):\n"
    "        datetime.now()\n",
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_host_frozen_travel():\n"
    "    with time_machine.travel(None, tick=False):\n"
    "        datetime.now()\n",
    # A marker freezes test execution, not import time: a module-level read
    # still runs against the host clock.
    "import pytest\n"
    "from datetime import datetime, UTC\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
    "STAMP = datetime.now(UTC)\n",
]

#: The same reads are accepted once the clock is frozen at function, class or
#: module scope, inside ``time_machine.travel`` or through ``frozen_clock``.
FROZEN_READS = [
    f"import pytest\nfrom datetime import datetime\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "def test_marker():\n"
    "    datetime.now()\n",
    f"import pytest\nfrom datetime import datetime\n"
    f"@pytest.mark.freeze_time({INSTANT})\n"
    "class TestFrozen:\n"
    "    def test_marker_on_class(self):\n"
    "        datetime.now()\n",
    f"import pytest\nfrom app.db.base import utc_now\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n"
    "def test_pytestmark():\n"
    "    utc_now()\n",
    f"import pytest\nfrom datetime import datetime, UTC\n"
    f"pytestmark = [pytest.mark.freeze_time({INSTANT}), pytest.mark.usefixtures('db')]\n"
    "def test_pytestmark_list():\n"
    "    datetime.now(UTC)\n",
    f"import pytest\nfrom datetime import datetime\n"
    f"import time_machine\n"
    f"def test_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        datetime.now()\n",
    "from time_machine import travel as travel_to\n"
    "from app.db.base import utc_now\n"
    f"def test_travel_alias():\n"
    f"    with travel_to({INSTANT}, tick=False):\n"
    "        utc_now()\n",
    "from app.db.base import utc_now\ndef test_fixture_argument(frozen_clock):\n    utc_now()\n",
    "from app.db.base import utc_now\n"
    "import pytest\n"
    "@pytest.fixture\n"
    "def seeded(frozen_clock):\n"
    "    return utc_now()\n",
    "import time\n"
    "def test_elapsed_measures_are_not_clock_reads():\n"
    "    return time.monotonic(), time.perf_counter()\n",
    "import time_machine\n"
    "from datetime import datetime\n"
    f"def test_lambda_inside_travel():\n"
    f"    with time_machine.travel({INSTANT}, tick=False):\n"
    "        return lambda: datetime.now()\n",
    "import time\ndef test_shadowed_time():\n    time = load_time()\n    return time.time()\n",
    # Declaration order does not matter: the marker covers functions and
    # methods regardless of where pytestmark appears.
    "from app.db.base import utc_now\n"
    "import pytest\n"
    "def test_declared_before_the_marker():\n"
    "    utc_now()\n"
    f"pytestmark = pytest.mark.freeze_time({INSTANT})\n",
    "import pytest\n"
    "from datetime import datetime\n"
    "class Late:\n"
    "    def test_method(self):\n"
    "        datetime.now()\n"
    f"    pytestmark = pytest.mark.freeze_time({INSTANT})\n",
    # Context managers evaluate in entry order: an expression passed to a
    # later manager runs inside the earlier travel.
    "from contextlib import nullcontext\n"
    "import time_machine\n"
    "from datetime import datetime\n"
    "def test_manager_order():\n"
    "    with time_machine.travel(DESTINATION, tick=False), nullcontext(datetime.now()):\n"
    "        pass\n",
]


def test_the_determinism_allowlist_is_current() -> None:
    assert guard.main([]) == 0


@pytest.mark.parametrize("source", UNFROZEN_READS)
def test_the_guard_rejects_unfrozen_clock_reads(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1


@pytest.mark.parametrize("source", FROZEN_READS)
def test_the_guard_accepts_frozen_clock_reads(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 0
    assert guard.main(["--root", str(tmp_path), "--strict"]) == 0


def test_the_guard_scans_support_and_module_level_reads(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    support = tests / "support"
    support.mkdir(parents=True)
    (support / "clocks.py").write_text("from datetime import datetime\nNOW = datetime.now()\n")
    (tests / "conftest.py").write_text("import time\nSTART = time.time()\n")
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1


def test_growth_fails_stale_fails_and_lowering_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    module = tests / "test_example.py"
    source = "from datetime import datetime\ndef test_now():\n    datetime.now()\n"
    module.write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps(guard.inventory(tmp_path)))
    args = ["--root", str(tmp_path), "--allowlist", str(allowlist)]
    assert guard.main(args) == 0
    assert "WARNING: 1 unfrozen clock read" in capsys.readouterr().out
    module.write_text(source + "datetime.now()\n")
    assert guard.main(args) == 1
    module.write_text("import time\ndef test_later():\n    time.monotonic()\n")
    assert guard.main(args) == 1  # stale entry, the read is gone


def test_strict_rejects_even_allowlisted_reads(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "from app.db.base import utc_now\ndef test_now():\n    utc_now()\n"
    )
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps(guard.inventory(tmp_path)))
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 0
    assert guard.main(["--root", str(tmp_path), "--strict"]) == 1


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("x()  # noqa: AQPY001", 1),
        ("x()  # noqa: AQPY001, PLR0913", 1),
        ("x()  # noqa: TID251", 1),
        ("x()  # noqa: TID251 - deliberate stall past the timeout (#847)", 0),
        ("x()  # noqa: AQPY001 - frozen by the class marker (#848)", 0),
        ("x()  # noqa: PLR0913 - wide fixture set (#849)", 0),
        ('"""# noqa: AQPY001"""\nx()', 0),  # docstrings are not comments
    ],
)
def test_aqpy000_counts_only_unreasoned_guarded_suppressions(
    tmp_path: Path, comment: str, expected: int
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(comment + "\n")
    inventory = guard.inventory(tmp_path)
    assert inventory == ({"tests/test_example.py": {guard.AQPY000: expected}} if expected else {})


def test_a_valid_suppression_excludes_the_read_but_a_bare_one_does_not(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "from app.db.base import utc_now\n"
        "def test_reasoned():\n"
        "    utc_now()  # noqa: AQPY001 - compares against a service-side clock (#847)\n"
        "def test_bare():\n"
        "    utc_now()  # noqa: AQPY001\n"
    )
    inventory = guard.inventory(tmp_path)
    assert inventory == {"tests/test_example.py": {guard.AQPY001: 1, guard.AQPY000: 1}}


def test_the_ruff_config_declares_the_aqpy_prefix() -> None:
    config: dict[str, Any] = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert "AQPY" in config["tool"]["ruff"]["lint"]["external"]


def test_the_lint_task_runs_the_guard() -> None:
    tasks: dict[str, Any] = tomllib.loads((BACKEND / "mise.toml").read_text())["tasks"]
    lint = tasks["risk-service:lint"]["run"]
    assert "uv run ruff check ." in lint
    assert "uv run python scripts/lint_baseline.py check" in lint
    assert "uv run python scripts/test_determinism_guard.py" in lint


def test_the_root_lint_task_delegates_to_the_backend() -> None:
    tasks: dict[str, Any] = tomllib.loads((BACKEND / "../mise.toml").read_text())["tasks"]
    root = tasks["risk-service:lint"]
    assert root["run"] == "mise run risk-service:lint"
    assert root["dir"] == "backend"


@pytest.mark.parametrize(
    "source",
    [
        dedent(
            f"""
            import pytest
            from app.db.base import utc_now
            @pytest.mark.freeze_time({INSTANT})
            def test_frozen():
                utc_now()
            def test_unfrozen():
                utc_now()
            """
        ),
        dedent(
            """
            from datetime import datetime
            import time_machine
            def test_mixed():
                with time_machine.travel(START, tick=False):
                    datetime.now()
                datetime.now()
            """
        ),
    ],
)
def test_only_the_unfrozen_part_of_a_file_is_counted(tmp_path: Path, source: str) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(source)
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("{}")
    assert guard.main(["--root", str(tmp_path), "--allowlist", str(allowlist)]) == 1
