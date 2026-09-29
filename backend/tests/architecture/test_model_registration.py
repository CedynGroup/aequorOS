"""Importing ``app.models`` must register EVERY mapped table.

Why this exists
---------------
``app/models/bi_commentary.py`` defined ``AiCommentaryDraft`` and
``app/models/__init__.py`` did not import it. Every test passed, because the
hermetic pytest suite imports the whole application and that registers the model
long before ``Base.metadata.create_all`` runs. The Playwright stack does not: it
builds its schema from ``app.models``, so it built 197 tables instead of 198 and
``POST /bi/ask`` answered 500 with ``no such table: ai_commentary_drafts``.

That is the ``create_all`` hazard AGENTS.md states in general terms — anything
built with ``create_all`` runs no migration and no worker, so it must contain what
those two would have written — in its sharpest form. The model existed, the
migration existed, the table was in the census, and the ONE path that mattered
never saw it.

Why this runs in a SUBPROCESS, which is the whole point
------------------------------------------------------
The first version of this file did the obvious thing and was VACUOUS: it compared
``Base.metadata`` after importing ``app.models`` against ``Base.metadata`` after
importing every module under it, inside this process. Both sets were identical
even with the offending import deleted — proved by deleting it and watching the
test pass — for two compounding reasons. ``Base.metadata`` is process-global and a
mapped class registers its table the moment its module is executed, so nothing is
ever *removed*; and ``importlib.reload`` re-executes a module without unregistering
anything. Worse, this suite's own conftest imports the application, so everything
was already registered before the test body began.

A question about what ONE import pulls in can therefore only be answered by an
interpreter that has imported nothing else. Hence ``subprocess``: it is slower and
it is the only honest way to ask.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: Import ``app.models`` and NOTHING else, then report what that registered. Run in
#: a fresh interpreter with the worker disabled and no database configured, because
#: importing ``app.main`` would start a worker thread against the primary.
_PACKAGE_ONLY = """
import json
import app.models  # noqa: F401 - imported for the registration side effect
from app.db.base import Base
print(json.dumps(sorted(Base.metadata.tables)))
"""

#: The same, after also importing every module in the package, which is the set a
#: correct ``__init__`` would already have produced.
_EVERY_MODULE = """
import importlib, json, pkgutil
import app.models
for info in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{info.name}")
from app.db.base import Base
print(json.dumps(sorted(Base.metadata.tables)))
"""


def _tables(script: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "RUN_INPROCESS_WORKER": "0", "DATABASE_URL": ""},
        check=False,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr[-2000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_importing_the_models_package_registers_every_mapped_table() -> None:
    """A model file with no entry in ``app/models/__init__.py`` is invisible to
    ``create_all``, and therefore to the Playwright stack and to any script that
    builds a schema without importing the whole application."""

    from_package = set(_tables(_PACKAGE_ONLY))
    from_everything = set(_tables(_EVERY_MODULE))
    unreachable = sorted(from_everything - from_package)
    assert unreachable == [], (
        "these tables are mapped but NOT registered by importing ``app.models``, so "
        f"``Base.metadata.create_all`` builds a schema without them: {unreachable}. "
        "Import the model in app/models/__init__.py."
    )


def test_the_subject_is_the_whole_model_layer_and_not_a_handful() -> None:
    """Anti-vacuity: if either probe stopped finding models, the test above would
    compare two empty sets and pass over nothing."""

    from_package = _tables(_PACKAGE_ONLY)
    assert len(from_package) >= 150, f"only {len(from_package)} tables registered"
    # The one that motivated this file, named so a regression is unmistakable.
    assert "ai_commentary_drafts" in from_package
