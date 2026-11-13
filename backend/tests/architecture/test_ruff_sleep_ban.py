"""The Ruff sleep ban covers tests only and every suppression states a reason.

TID251 bans ``time.sleep`` and ``asyncio.sleep`` through Ruff's ``banned-api``
table (CODEBASE_CONVENTIONS.md §1, "Lint baselines"). The rule is selected repo
wide but per-file-ignored outside ``tests/`` so app retry backoff keeps its
sleeps; these hermetic guards pin that scoping and require every surviving test
suppression to carry a reason and a tracking issue, the same contract the AQPY
suppression guard enforces for clock reads. The surviving-suppression format is
``# noqa: TID251 - <reason> (#<issue>)``; it is spelled out here in the
docstring rather than a comment so the AQPY000 tokenizer does not read the
documented example as a bare suppression.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]

SLEEP_BAN = "TID251"
BANNED_MEMBERS = ("time.sleep", "asyncio.sleep")
TESTS_EXEMPT_KEY = "!tests/**"

#: A surviving suppression must name its reason and its tracking issue; see the
#: module docstring for the exact format.
SUPERSEDED = re.compile(r"#\s*noqa:\s*TID251\s*-\s*(?P<reason>[^#(]+?)\s*\(#(?P<issue>\d+)\)")


def _lint_config() -> dict[str, Any]:
    with (BACKEND / "pyproject.toml").open("rb") as handle:
        config: dict[str, Any] = tomllib.load(handle)
    lint: dict[str, Any] = config["tool"]["ruff"]["lint"]
    return lint


def test_sleep_ban_is_selected() -> None:
    config = _lint_config()
    assert SLEEP_BAN in config["select"]


def test_banned_api_covers_time_and_asyncio_sleep() -> None:
    config = _lint_config()
    banned = config["flake8-tidy-imports"]["banned-api"]
    for member in BANNED_MEMBERS:
        assert member in banned, f"{member} must stay banned with a reason message"
        assert banned[member]["msg"]


def test_sleep_ban_is_scoped_to_tests() -> None:
    config = _lint_config()
    ignores = config["per-file-ignores"]
    assert TESTS_EXEMPT_KEY in ignores, "app and scripts trees must stay exempt from TID251"
    assert SLEEP_BAN in ignores[TESTS_EXEMPT_KEY]


def test_every_sleep_suppression_carries_reason_and_issue() -> None:
    #: Files that document the suppression format itself; their fixtures and
    #: docstrings quote it, so a raw line scan would read the example as a real
    #: suppression. The AQPY000 tokenizer skips them for free (strings, not
    #: comments).
    format_documenting = {
        Path(__file__).resolve(),
        BACKEND / "tests/architecture/test_test_determinism_guard.py",
    }
    for path in sorted((BACKEND / "tests").rglob("*.py")):
        if path.resolve() in format_documenting:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if "TID251" not in line or "noqa" not in line:
                continue
            match = SUPERSEDED.search(line)
            assert match is not None, (
                f"{path.relative_to(BACKEND)}:{number} suppresses TID251 without "
                "'# noqa: TID251 - <reason> (#<issue>)'"
            )
            assert match.group("reason") and int(match.group("issue")) > 0


PROBE = (
    "import asyncio\n"
    "import time\n"
    "\n"
    "\n"
    "def test_sleeper() -> None:\n"
    "    time.sleep(1)\n"
    "\n"
    "\n"
    "async def test_async_sleeper() -> None:\n"
    "    await asyncio.sleep(1)\n"
)


def test_ruff_actually_flags_sleep_waits_under_tests() -> None:
    """Run Ruff itself: config-verified bans stay executable, both members."""
    # A unique probe name keeps concurrent runs on one checkout from deleting
    # each other's file; the ban only applies under tests/, so it stays there.
    probe = BACKEND / "tests" / f"_ruff_sleep_ban_probe_{uuid.uuid4().hex}.py"
    probe.write_text(PROBE)
    try:
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--config",
                str(BACKEND / "pyproject.toml"),
                str(probe),
            ],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    finally:
        probe.unlink(missing_ok=True)
    assert result.returncode == 1
    assert result.stdout.count(SLEEP_BAN) == len(BANNED_MEMBERS)
