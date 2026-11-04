"""The migration consumer must resolve one unambiguous upgrade history."""

from __future__ import annotations

import warnings
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_have_unique_revisions_and_one_head() -> None:
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    # Alembic warns instead of rejecting duplicate ids; make that ambiguous
    # history fail the hermetic gate before any database is contacted.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        scripts = ScriptDirectory.from_config(config)
        heads = scripts.get_heads()
        revisions = list(scripts.walk_revisions())
    assert len(heads) == 1, f"Expected one migration head, found {heads}"
    ids = [revision.revision for revision in revisions]
    assert len(ids) == len(set(ids)), "Migration revision ids must be unique"
