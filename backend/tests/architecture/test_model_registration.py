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

Why the "everything" probe walks RECURSIVELY (audit A360-2)
-----------------------------------------------------------
Its first form used ``pkgutil.iter_modules``, which lists a package's direct
children only. A model defined in a SUBPACKAGE of ``app/models`` would have been
invisible to the probe — so both sides of the comparison would have lacked it, the
difference would have been empty, and the guard would have acquitted exactly the
shape it exists to catch, one directory deeper. No subpackage exists today; the
self-proof below builds one and shows the shallow walk missing it and the
recursive walk seeing it, so the blind spot stays closed by evidence rather than
by the absence of the case.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: Import the package and NOTHING else, then report what that registered. Run in
#: a fresh interpreter with the worker disabled and no database configured, because
#: importing ``app.main`` would start a worker thread against the primary.
_PACKAGE_ONLY = """
import importlib, json
importlib.import_module("{package}")
from app.db.base import Base
print(json.dumps(sorted(Base.metadata.tables)))
"""

#: The same, after also importing every module in the package — RECURSIVELY, so a
#: subpackage's models count — and every feature's own models (``<feature>/models.py``
#: or ``<feature>/models/``) under ``{root}``, which is the set a correct
#: ``__init__`` would already have produced. Feature models live outside the
#: registry package once a feature moves to ``app/<feature>/``, and the registry must
#: still import them.
_EVERY_MODULE = """
import importlib, json, pathlib, pkgutil
package = importlib.import_module("{package}")
for info in pkgutil.walk_packages(package.__path__, prefix="{package}."):
    importlib.import_module(info.name)
root = pathlib.Path("{root}")
for path in sorted(root.rglob("*.py")):
    parts = path.relative_to(root.parent).with_suffix("").parts
    if "models" in parts[1:-1] or parts[-1] == "models":
        importlib.import_module(".".join(parts).removesuffix(".__init__"))
from app.db.base import Base
print(json.dumps(sorted(Base.metadata.tables)))
"""

#: The probe's FIRST form, kept ONLY as the negative control for the self-proof
#: below: ``iter_modules`` lists direct children, so a subpackage's models never
#: enter the comparison. Never use this for the real check.
_EVERY_MODULE_SHALLOW = """
import importlib, json, pkgutil
package = importlib.import_module("{package}")
for info in pkgutil.iter_modules(package.__path__):
    importlib.import_module(f"{package}.{{info.name}}")
from app.db.base import Base
print(json.dumps(sorted(Base.metadata.tables)))
"""


def _tables(
    script: str,
    *,
    package: str = "app.models",
    root: Path = BACKEND_ROOT / "app",
    extra_path: Path | None = None,
) -> list[str]:
    env = {"PATH": "/usr/bin:/bin", "RUN_INPROCESS_WORKER": "0", "DATABASE_URL": ""}
    if extra_path is not None:
        env["PYTHONPATH"] = str(extra_path)
    result = subprocess.run(
        [sys.executable, "-c", script.format(package=package, root=root)],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env=env,
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


def _synthetic_models_package(root: Path) -> None:
    """A package whose ``__init__`` imports nothing, with one model a level down."""

    package = root / "probe_models"
    (package / "nested").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "nested" / "__init__.py").write_text("")
    (package / "nested" / "child.py").write_text(
        "from sqlalchemy import Column, Integer\n"
        "from app.db.base import Base\n"
        "class ProbeChild(Base):\n"
        "    __tablename__ = 'probe_child_table'\n"
        "    id = Column(Integer, primary_key=True)\n"
    )


def test_the_probe_sees_a_model_one_directory_down_and_the_shallow_walk_did_not(
    tmp_path: Path,
) -> None:
    """The self-proving case for the recursive walk.

    Builds a package with an EMPTY ``__init__`` and one mapped class in a
    subpackage, then asks all three probes. The package-only probe must not see
    the table (nothing imports it — the defect shape); the recursive probe MUST,
    which is what lets the main test convict it; and the shallow walk the first
    version of this file used must NOT, which is the blind spot being closed. If
    the last assertion ever fails, ``iter_modules`` has started recursing and the
    negative control is no longer a control.
    """

    _synthetic_models_package(tmp_path)
    package_only = set(_tables(_PACKAGE_ONLY, package="probe_models", extra_path=tmp_path))
    everything = set(
        _tables(
            _EVERY_MODULE,
            package="probe_models",
            root=tmp_path / "probe_models",
            extra_path=tmp_path,
        )
    )
    shallow = set(_tables(_EVERY_MODULE_SHALLOW, package="probe_models", extra_path=tmp_path))

    assert "probe_child_table" not in package_only, "the synthetic __init__ must import nothing"
    assert "probe_child_table" in everything, (
        "the recursive probe did not see a model one directory down, so the main "
        "test cannot convict an unregistered subpackage model"
    )
    assert "probe_child_table" not in shallow, (
        "the shallow probe now sees the nested model, so it is no longer the negative "
        "control this self-proof relies on"
    )
    # And the main test's arithmetic convicts it.
    assert sorted(everything - package_only) == ["probe_child_table"]


def test_the_probe_sees_a_feature_model_outside_the_registry_package(tmp_path: Path) -> None:
    """The self-proving case for feature packages.

    ``app/<feature>/models.py`` sits outside ``app.models``, so walking the registry
    package alone would never import it, and a feature model the registry forgot
    would pass. The probe builds an empty registry beside one feature model and
    must see the model only through the feature walk.
    """
    root = tmp_path / "probe_app"
    (root / "models").mkdir(parents=True)
    (root / "fx").mkdir()
    for package in (root, root / "models", root / "fx"):
        (package / "__init__.py").write_text("")
    (root / "fx" / "models.py").write_text(
        "from sqlalchemy import Column, Integer\n"
        "from app.db.base import Base\n"
        "class ProbeFeature(Base):\n"
        "    __tablename__ = 'probe_feature_table'\n"
        "    id = Column(Integer, primary_key=True)\n"
    )
    probe = {"package": "probe_app.models", "root": root, "extra_path": tmp_path}
    assert "probe_feature_table" not in _tables(_PACKAGE_ONLY, **probe)
    assert "probe_feature_table" in _tables(_EVERY_MODULE, **probe), (
        "the probe did not import a feature's models.py, so a feature model missing "
        "from the app.models registry would go unconvicted"
    )
