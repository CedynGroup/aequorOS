"""The feature-move codemod moves code and rewrites every reference to it.

Each test builds a small repository in a temporary directory: a ``backend/`` with an
``app/`` that imports a module in every shape the real tree uses (plain, split
``from`` lists, lazy imports carrying ``noqa`` comments, relative imports, string
patch targets), an architecture guard that names files relative to ``app/``, the
boundary baseline, and docs that cite paths. The codemod then runs exactly as a
move PR runs it, so a failure names the reference shape that regressed.
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import feature_moves
from scripts.feature_moves import Renamer

MOVES = (
    ("app.services.regulatory_fx", "app.fx.service"),
    ("app.domain.fx", "app.fx.domain"),
)

FILES: dict[str, str] = {
    "backend/pyproject.toml": "[tool.ruff]\nline-length = 100\n",
    "backend/scripts/feature_module_moves.json": "[]\n",
    "backend/app/__init__.py": "",
    "backend/app/services/__init__.py": '"""Services."""\n',
    "backend/app/services/audit.py": "def record() -> None:\n    pass\n",
    "backend/app/services/regulatory_fx.py": "def run() -> int:\n    return 1\n",
    "backend/app/services/pipeline.py": (
        "from __future__ import annotations\n"
        "\n"
        "from app.services import audit, regulatory_fx\n"
        "from app.services.regulatory_fx import run\n"
        "\n"
        'TARGET = "app.services.regulatory_fx.run"\n'
        "\n"
        "\n"
        "def lazy() -> int:\n"
        "    from app.services import regulatory_fx  # noqa: PLC0415 - avoid a cycle\n"
        "\n"
        "    return regulatory_fx.run() + run() + bool(audit)\n"
    ),
    "backend/app/domain/__init__.py": "",
    "backend/app/domain/risk.py": "LIMIT = 1\n",
    "backend/app/domain/fx/__init__.py": '"""FX engine."""\n',
    "backend/app/domain/fx/engine.py": "def compute() -> int:\n    return 2\n",
    "backend/app/domain/fx/helpers.py": (
        "from __future__ import annotations\n"
        "\n"
        "from ..risk import LIMIT\n"
        "from .engine import compute\n"
        "\n"
        "VALUE = compute() + LIMIT\n"
    ),
    "backend/tests/architecture/test_guard.py": (
        'PLANE = ("services/regulatory_fx.py", "domain/fx", "services/regulatory_*.py")\n'
    ),
    "backend/tests/architecture/feature_boundary_baseline.json": json.dumps(
        [
            "private live->fx: app.services.pipeline -> app.services.regulatory_fx",
            "private live->fx: app.services.alerts -> app.domain.fx.engine",
        ],
        indent=1,
    )
    + "\n",
    "backend/tests/test_fx.py": (
        "from unittest import mock\n"
        "\n"
        "from app.services import regulatory_fx\n"
        "\n"
        "\n"
        "def test_run() -> None:\n"
        '    with mock.patch("app.services.regulatory_fx.run", return_value=3):\n'
        "        assert regulatory_fx.run() == 3\n"
    ),
    "AGENTS.md": (
        "FX lives in `backend/app/services/regulatory_fx.py` "
        "(`app.services.regulatory_fx`); engines in `app/domain/fx/`.\n"
    ),
    "backend/dashboard/README.md": "Unrelated: app/services/regulatory_fx.py\n",
    "backend/scripts/feature_moves.py": "OLD = 'app.services.regulatory_fx'\n",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    for relative, content in FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "seed")
    return tmp_path


def _run(repository: Path, command: str, moves: Sequence[tuple[str, str]] = MOVES) -> list[str]:
    lines: list[str] = []
    backend = repository / "backend"
    if command == "move":
        code = feature_moves.move(backend, moves, echo=lines.append)
    elif command == "rewrite":
        code = feature_moves.rewrite(backend, echo=lines.append)
    else:
        code = feature_moves.check(backend, echo=lines.append)
    lines.append(f"exit {code}")
    return lines


def _read(repository: Path, relative: str) -> str:
    return (repository / relative).read_text()


def test_renamer_composes_moves_in_order_and_inverts() -> None:
    rename = Renamer((("app.a", "app.b"), ("app.b.x", "app.c")))
    assert rename("app.a.x.f") == "app.c.f"
    assert rename("app.a") == "app.b"
    assert rename("app.ab") == "app.ab"
    assert rename.original("app.c.f") == "app.a.x.f"
    assert rename.roots == {"app"}


def test_a_move_relocates_files_without_shims(repository: Path) -> None:
    output = _run(repository, "move")
    backend = repository / "backend"
    assert "exit 0" in output
    assert (backend / "app/fx/service.py").is_file()
    assert (backend / "app/fx/domain/engine.py").is_file()
    assert (backend / "app/fx/__init__.py").is_file()
    assert not (backend / "app/services/regulatory_fx.py").exists()
    assert not (backend / "app/domain/fx").exists()


def test_stale_bytecode_at_the_destination_does_not_block_a_move(repository: Path) -> None:
    stale = repository / "backend/app/fx/domain/__pycache__"
    stale.mkdir(parents=True)
    (stale / "engine.cpython-313.pyc").write_bytes(b"")
    assert "exit 0" in _run(repository, "move")
    assert (repository / "backend/app/fx/domain/engine.py").is_file()
    assert (repository / "backend/app/fx/__init__.py").is_file()
    assert not (repository / "backend/app/fx/domain/fx").exists()


def test_a_move_records_every_moved_module_in_the_ledger(repository: Path) -> None:
    _run(repository, "move")
    ledger = json.loads(_read(repository, "backend/scripts/feature_module_moves.json"))
    assert ledger == [
        ["app.services.regulatory_fx", "app.fx.service"],
        ["app.domain.fx", "app.fx.domain"],
        ["app.domain.fx.engine", "app.fx.domain.engine"],
        ["app.domain.fx.helpers", "app.fx.domain.helpers"],
    ]


def test_from_imports_of_a_moved_module_keep_their_local_name(repository: Path) -> None:
    _run(repository, "move")
    pipeline = _read(repository, "backend/app/services/pipeline.py")
    assert "from app.services import audit\n" in pipeline
    assert "from app.fx import service as regulatory_fx\n" in pipeline
    assert "from app.fx.service import run\n" in pipeline
    assert 'TARGET = "app.fx.service.run"' in pipeline
    assert (
        "    from app.fx import service as regulatory_fx  # noqa: PLC0415 - avoid a cycle\n"
        in pipeline
    )
    assert "regulatory_fx.run()" in pipeline
    assert "app.services.regulatory_fx" not in pipeline


def test_relative_imports_are_made_absolute_only_when_they_would_break(
    repository: Path,
) -> None:
    _run(repository, "move")
    helpers = _read(repository, "backend/app/fx/domain/helpers.py")
    assert "from app.domain.risk import LIMIT\n" in helpers
    assert "from .engine import compute\n" in helpers


def test_strings_paths_guards_and_docs_follow_the_move(repository: Path) -> None:
    _run(repository, "move")
    assert 'mock.patch("app.fx.service.run"' in _read(repository, "backend/tests/test_fx.py")
    assert "from app.fx import service as regulatory_fx" in _read(
        repository, "backend/tests/test_fx.py"
    )
    guard = _read(repository, "backend/tests/architecture/test_guard.py")
    assert '"fx/service.py", "fx/domain", "services/regulatory_*.py"' in guard
    agents = _read(repository, "AGENTS.md")
    assert "`backend/app/fx/service.py` (`app.fx.service`)" in agents
    assert "`app/fx/domain/`" in agents
    untouched = (
        "backend/dashboard/README.md",
        "backend/scripts/feature_moves.py",
        # The boundary guard maps moves back through the ledger; its baseline keeps old names.
        "backend/tests/architecture/feature_boundary_baseline.json",
    )
    for relative in untouched:
        assert _read(repository, relative) == FILES[relative]


def test_rewritten_python_still_parses(repository: Path) -> None:
    _run(repository, "move")
    for path in (repository / "backend").rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path))


def test_a_move_reports_guard_globs_that_lost_their_files(repository: Path) -> None:
    output = _run(repository, "move")
    drops = [line for line in output if line.startswith("guard scans fewer files")]
    assert drops == [
        "guard scans fewer files, review it: "
        "test_guard.py: 'services/regulatory_*.py' matched 1 files, now 0"
    ]


def test_the_rewrite_is_idempotent_and_check_passes_after_a_move(repository: Path) -> None:
    _run(repository, "move")
    assert _run(repository, "rewrite") == ["exit 0"]
    assert _run(repository, "check") == ["exit 0"]


def test_an_in_flight_branch_is_fixed_by_rewrite_only(repository: Path) -> None:
    _run(repository, "move")
    added = repository / "backend/app/services/new_report.py"
    added.write_text("from app.services.regulatory_fx import run\n\nVALUE = run()\n")
    check = _run(repository, "check")
    assert "old name still used: backend/app/services/new_report.py" in check
    assert check[-1] == "exit 1"
    _run(repository, "rewrite")
    assert added.read_text().startswith("from app.fx.service import run\n")
    assert _run(repository, "check") == ["exit 0"]


def test_check_reports_a_recorded_module_still_at_its_old_path(repository: Path) -> None:
    feature_moves.write_ledger(repository / "backend", [("app.services.audit", "app.audit")])
    check = _run(repository, "check")
    assert "module still at its old path: app.services.audit" in check
    assert check[-1] == "exit 1"


def test_a_move_from_a_missing_module_stops_before_touching_anything(repository: Path) -> None:
    with pytest.raises(SystemExit, match="cannot move"):
        _run(repository, "move", [("app.services.missing", "app.fx.missing")])
    assert _read(repository, "backend/scripts/feature_module_moves.json") == "[]\n"


def test_the_repository_uses_no_old_module_name() -> None:
    assert feature_moves.check(feature_moves.BACKEND, echo=lambda _line: None) == 0
