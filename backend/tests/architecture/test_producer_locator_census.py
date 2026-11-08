"""Every job/notification producer must persist its locator and provenance.

Why this guard exists
---------------------
The #186 read paths treat a row without a persisted locator as hidden, so a
producer that forgets the locator does not fail loudly — it silently produces
work its tenant can never see. Runtime enforcement cannot catch it either:
``enqueue`` accepts an unattributed insert (legacy rows look identical), and a
direct ``Job(...)`` construction bypasses the queue's stamping entirely.

So this census asks the question statically, over every module under ``app/``:

1. every ``job_queue.enqueue(...)`` call states BOTH ``locator=`` and
   ``provenance=``, even when the answer is ``None`` (an explicit decision,
   never an omission);
2. every direct ``Job(...)`` construction sets the locator columns and the
   enqueue provenance itself (the two case-service writers are the precedent);
3. every ``notifications.emit(...)`` call states ``module=`` and
   ``sensitivity=`` (``bank_id`` is optional: some notifications are
   org-scoped).

Like the enqueue-reachability guard, this is static analysis: it proves the
arguments are PRESENT, not that they are correct. The behavioral tests next to
the queue and the notification service carry that half.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: The keywords every ``enqueue`` call must state. ``None`` is a valid VALUE
#: (defined worker/system enqueues carry no locator) but never a default.
_ENQUEUE_KEYWORDS = frozenset({"locator", "provenance"})

#: The columns a direct ``Job(...)`` must set itself, since no queue code runs.
_JOB_COLUMNS = frozenset(
    {
        "institution_scope",
        "module",
        "sensitivity",
        "enqueued_by_principal",
        "enqueued_action",
    }
)

#: The locator keywords every ``notifications.emit`` call must state.
_EMIT_KEYWORDS = frozenset({"module", "sensitivity"})


def _import_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to the job-queue service module and the Job model class."""
    queue_aliases: set[str] = set()
    job_aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module == "app.services" and alias.name == "job_queue":
                    queue_aliases.add(bound)
                if node.module in ("app.models", "app.models.job") and alias.name == "Job":
                    job_aliases.add(bound)
    return queue_aliases, job_aliases


def _offenders(root: Path = APP_ROOT) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        queue_aliases, job_aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
            # **-unpacked constructions cannot be checked; none exist in app/ today.
            has_star_kwargs = any(keyword.arg is None for keyword in node.keywords)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "enqueue"
                and isinstance(func.value, ast.Name)
                and func.value.id in queue_aliases
            ):
                missing = _ENQUEUE_KEYWORDS - keywords
                if missing:
                    found.append(f"{path}: {node.lineno}: enqueue omits {sorted(missing)}")
            if (
                isinstance(func, ast.Name)
                and func.id in job_aliases
                and not has_star_kwargs
            ):
                missing = _JOB_COLUMNS - keywords
                if missing:
                    found.append(f"{path}: {node.lineno}: Job(...) omits {sorted(missing)}")
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "emit"
                and isinstance(func.value, ast.Name)
                and func.value.id in _notification_aliases(tree)
            ):
                missing = _EMIT_KEYWORDS - keywords
                if missing:
                    found.append(f"{path}: {node.lineno}: emit omits {sorted(missing)}")
    return found


def _notification_aliases(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "app.notifications"
        ):
            for alias in node.names:
                if alias.name == "notifications":
                    aliases.add(alias.asname or alias.name)
    return aliases


@pytest.fixture(scope="module")
def offenders() -> list[str]:
    return _offenders()


def test_every_enqueue_call_states_locator_and_provenance(offenders: list[str]) -> None:
    locator_omissions = [line for line in offenders if "enqueue omits" in line]
    assert locator_omissions == [], (
        "these job_queue.enqueue call sites do not state their locator and "
        "provenance. A locator-less or unattributed job is hidden from every "
        f"tenant read path, so the work would silently vanish: {locator_omissions}"
    )


def test_every_direct_job_write_sets_the_locator_columns(offenders: list[str]) -> None:
    direct = [line for line in offenders if "Job(...) omits" in line]
    assert direct == [], (
        "these direct Job(...) constructions bypass job_queue.enqueue and must "
        f"set the locator and provenance columns themselves: {direct}"
    )


def test_every_notification_emit_declares_its_module_locator(offenders: list[str]) -> None:
    emit_omissions = [line for line in offenders if "emit omits" in line]
    assert emit_omissions == [], (
        "these notifications.emit call sites do not declare module and "
        "sensitivity. A locator-less notification is hidden from every tenant "
        f"read path, so the message would silently vanish: {emit_omissions}"
    )


def test_the_census_convicts_an_unstamped_producer(tmp_path: Path) -> None:
    """A site omitting the keywords must be convicted, or the census guards nothing."""
    (tmp_path / "producer.py").write_text(
        "from app.services import job_queue\n"
        "from app.models import Job\n"
        "from app.notifications.public import notifications\n"
        "\n"
        "def enqueue_site(db):\n"
        "    return job_queue.enqueue(db, 'OR-X', 'pipeline_refresh')\n"
        "\n"
        "def direct_write(ctx):\n"
        "    return Job(organization_id='OR-X', job_type='x', status='queued')\n"
        "\n"
        "def emit_site(db, ctx):\n"
        "    return notifications.emit(db, ctx, type='t', severity='info',\n"
        "title='t', body='b')\n"
    )
    assert _offenders(tmp_path) == [
        f"{tmp_path / 'producer.py'}: 6: enqueue omits ['locator', 'provenance']",
        f"{tmp_path / 'producer.py'}: 9: Job(...) omits ['enqueued_action', "
        "'enqueued_by_principal', 'institution_scope', 'module', 'sensitivity']",
        f"{tmp_path / 'producer.py'}: 12: emit omits ['module', 'sensitivity']",
    ]


def test_the_census_acquits_a_fully_stamped_producer(tmp_path: Path) -> None:
    """Explicit ``None`` for a defined system enqueue is a value, not an omission."""
    (tmp_path / "producer.py").write_text(
        "from app.services import job_queue\n"
        "\n"
        "def enqueue_site(db):\n"
        "    return job_queue.enqueue(db, 'OR-X', 'scheduled_tick',\n"
        "        locator=None,\n"
        "        provenance=job_queue.worker_provenance('scheduler.tick'))\n"
    )
    assert _offenders(tmp_path) == []
