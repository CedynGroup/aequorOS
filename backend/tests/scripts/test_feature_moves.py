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
import shlex
import shutil
import subprocess
import sys
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
    "backend/app/blocked": "not a directory\n",
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


def test_renamer_composes_moves_in_order() -> None:
    rename = Renamer((("app.a", "app.b"), ("app.b.x", "app.c")))
    assert rename("app.a.x.f") == "app.c.f"
    assert rename("app.a") == "app.b"
    assert rename("app.ab") == "app.ab"
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


@pytest.mark.parametrize("command", ["move", "rewrite"])
@pytest.mark.parametrize(
    "dockerfile",
    [
        "backend/Dockerfile",
        "backend/dashboard/Dockerfile",
        "backend/dashboard/Dockerfile.production",
        "backend/dashboard/production.dockerfile",
    ],
)
def test_dockerfile_copy_contract_follows_package_assets(
    repository: Path, command: str, dockerfile: str
) -> None:
    """Consume the normalized COPY contract and materialize its build-stage assets."""
    package = repository / "backend/app/services/attestation"
    (package / "fonts").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "fonts/Caveat-Regular.ttf").write_bytes(b"signature font")
    _git(repository, "add", ".")
    moves = (("app.services.attestation", "app.attestation.service"),)
    if command == "rewrite":
        assert "exit 0" in _run(repository, "move", moves)
    path = repository / dockerfile
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "COPY backend/app/services/attestation/fonts backend/app/services/attestation/fonts\n"
    )
    _git(repository, "add", str(path.relative_to(repository)))
    if command == "rewrite":
        assert _run(repository, "check") == [f"old name still used: {dockerfile}", "exit 1"]
    assert "exit 0" in _run(repository, command, moves)
    instruction, source, destination = shlex.split(path.read_text())
    assert instruction.upper() == "COPY"
    build_stage = repository / "build-stage"
    shutil.copytree(repository / source, build_stage / destination)
    assert (
        build_stage / "backend/app/attestation/service/fonts/Caveat-Regular.ttf"
    ).read_bytes() == b"signature font"
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


def test_stale_bytecode_at_the_destination_does_not_block_a_move(repository: Path) -> None:
    stale = repository / "backend/app/fx/domain/__pycache__"
    stale.mkdir(parents=True)
    (stale / "engine.cpython-313.pyc").write_bytes(b"")
    assert "exit 0" in _run(repository, "move")
    assert (repository / "backend/app/fx/domain/engine.py").is_file()
    assert (repository / "backend/app/fx/__init__.py").is_file()
    assert not (repository / "backend/app/fx/domain/fx").exists()


def test_stale_bytecode_beside_a_module_does_not_hide_its_source(repository: Path) -> None:
    stale = repository / "backend/app/services/regulatory_fx/__pycache__"
    stale.mkdir(parents=True)
    (stale / "__init__.cpython-313.pyc").write_bytes(b"")
    assert "exit 0" in _run(repository, "move")
    assert _python(repository, "from app.fx.service import run\nprint(run())") == "1"
    assert MOVES[0] in feature_moves.load_ledger(repository / "backend")
    assert _run(repository, "check") == ["exit 0"]


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
    assert _read(repository, "backend/dashboard/README.md") == "Unrelated: app/fx/service.py\n"
    untouched = (
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


def _python(repository: Path, script: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository / "backend",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.parametrize(
    "relative",
    [
        "backend/dashboard/lib/probe.test.ts",
        "backend/dashboard/lib/api/probe.test.tsx",
        "console/probe.test.js",
        "frontend/probe.test.jsx",
    ],
)
@pytest.mark.parametrize("literal", ["template", "quoted"])
@pytest.mark.parametrize("command", ["move", "rewrite"])
def test_embedded_python_references_remain_executable(
    repository: Path, relative: str, literal: str, command: str
) -> None:
    if command == "rewrite":
        _run(repository, "move")
    script = (
        "from unittest.mock import patch\n"
        "from app.services import regulatory_fx\n"
        "from app.services.regulatory_fx import run\n"
        'with patch("app.services.regulatory_fx.run", return_value=7):\n'
        "    print(regulatory_fx.run(), run())\n"
    )
    embedded = f"`{script}`" if literal == "template" else json.dumps(script)
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'import { execFileSync } from "node:child_process";\n'
        f"const script = {embedded};\n"
        f"process.stdout.write(execFileSync({json.dumps(sys.executable)}, "
        '["-c", script], {encoding: "utf8"}));\n'
    )
    if command == "rewrite":
        assert _run(repository, "check")[-1] == "exit 1"
    _run(repository, command)
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=path.read_text(),
        cwd=repository / "backend",
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "7 1"
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


@pytest.mark.parametrize(
    ("relative", "statement", "module"),
    [
        ("app/fx/domain/helpers.py", "from ..service import run", "app.fx.domain.helpers"),
        ("app/fx/domain/__init__.py", "from .. import service as executor", "app.fx.domain"),
    ],
)
def test_cumulative_rewrites_preserve_current_relative_imports(
    repository: Path, relative: str, statement: str, module: str
) -> None:
    _run(repository, "move")
    assert (
        _python(
            repository,
            "from app.fx.domain.helpers import VALUE\n"
            "from app.services.pipeline import lazy\nprint(VALUE, lazy())",
        )
        == "3 3"
    )
    path = repository / "backend" / relative
    expression = "run()" if relative.endswith("helpers.py") else "executor.run()"
    path.write_text(f"{statement}\nVALUE = {expression}\n")
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]
    assert _python(repository, f"from {module} import VALUE\nprint(VALUE)") == "1"
    path.write_text(
        f"{statement}\nfrom app.domain.fx.engine import compute\nVALUE = {expression} + compute()\n"
    )
    assert _run(repository, "check")[-1] == "exit 1"
    _run(repository, "rewrite")
    assert _python(repository, f"from {module} import VALUE\nprint(VALUE)") == "3"
    _run(repository, "move", [("app.fx.service", "app.currency.service")])
    assert _python(repository, f"from {module} import VALUE\nprint(VALUE)") == "3"
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


@pytest.mark.parametrize(
    "later",
    [
        ("app.services.missing", "app.fx.missing"),
        ("app.services.audit", "app.domain.risk"),
        ("app.services.audit", "app.fx.service"),
        ("app.services.audit", "app.fx.service.extra"),
        ("app.services.audit", "app.fx"),
        ("app.services.audit", "app.blocked.child"),
        ("app.domain.fx", "app.domain.fx.nested"),
        ("app.services.untracked", "app.fx.untracked"),
        ("app.domain.fx", "app.assets"),
        ("app.services.audit", "app.domain.fx"),
        ("app.domain.fx", "app.services.audit"),
    ],
)
def test_an_invalid_batch_does_not_apply_earlier_moves(
    repository: Path, later: tuple[str, str]
) -> None:
    if later[0] == "app.services.untracked":
        (repository / "backend/app/services/untracked.py").write_text("VALUE = 1\n")
    if later[1] == "app.assets":
        assets = repository / "backend/app/assets"
        assets.mkdir()
        (assets / "payload.txt").write_text("preserved\n")
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, capture_output=True, check=True
    ).stdout
    with pytest.raises(SystemExit, match="cannot move"):
        _run(repository, "move", [MOVES[0], later])
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, capture_output=True, check=True
    ).stdout
    assert before == after
    assert not (repository / "backend/app/fx").exists()
    assert _python(repository, "from app.services.regulatory_fx import run\nprint(run())") == "1"
    assert json.loads(_read(repository, "backend/scripts/feature_module_moves.json")) == []


def test_the_repository_uses_no_old_module_name() -> None:
    assert feature_moves.check(feature_moves.BACKEND, echo=lambda _line: None) == 0


@pytest.mark.parametrize("command", ["move", "rewrite"])
@pytest.mark.parametrize("kind", ["quoted", "adjacent", "triple", "raw", "fstring", "format"])
def test_python_hosted_program_imports_remain_executable(
    repository: Path, command: str, kind: str
) -> None:
    if command == "rewrite":
        _run(repository, "move")
    program = (
        "extra = 5\n"
        "label = 'ok'\n"
        "from app.services import (\n"
        "    audit,\n"
        "    regulatory_fx as fx,\n"
        ")\n"
        "def result():\n"
        "    from app.services import regulatory_fx as lazy\n"
        "    assert audit is not None and label == 'ok'\n"
        + r'    assert "\\n" == chr(92) + "n"'
        + "\n"
        "    return fx.run() + lazy.run() + extra\n"
    )
    if kind == "adjacent":
        literal = "(\n" + "\n".join(repr(line) for line in program.splitlines(True)) + "\n)"
    elif kind in {"triple", "raw"}:
        body = program if kind == "raw" else program.replace("\\", "\\\\")
        literal = ("r" if kind == "raw" else "") + f'"""{body}"""'
    elif kind in {"fstring", "format"}:
        body = program.replace("extra = 5", "extra = {amount}").replace(
            "label = 'ok'", "label = {marker!r}"
        )
        literal = (
            "f" + repr(body)
            if kind == "fstring"
            else repr(body) + ".format(amount=amount, marker=marker)"
        )
    else:
        literal = repr(program)
    host = repository / "backend/scripts/generator.py"
    host.write_text(
        "from pathlib import Path\n"
        "amount = 5\nmarker = 'ok'\n"
        f"emoji = '€'; PROGRAM = {literal}\n"
        "def generate():\n"
        "    assert emoji == '€'\n"
        "    Path('generated.py').write_text(PROGRAM)\n"
    )
    if command == "move":
        _python(repository, "from scripts.generator import generate\ngenerate()")
        assert _python(repository, "from generated import result\nprint(result())") == "7"
    else:
        assert _run(repository, "check")[-1] == "exit 1"
    _run(repository, command)
    _python(repository, "from scripts.generator import generate\ngenerate()")
    assert _python(repository, "from generated import result\nprint(result())") == "7"
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


@pytest.mark.parametrize("command", ["move", "rewrite"])
@pytest.mark.parametrize("kind", ["interpolated", "escaped", "both"])
def test_embedded_parent_imports_with_template_expressions_remain_executable(
    repository: Path, command: str, kind: str
) -> None:
    if command == "rewrite":
        _run(repository, "move")
    script = (
        "from app.services import (\n"
        "    audit,\n"
        "    regulatory_fx as fx,\n"
        ")\n"
        "def result():\n"
        "    from app.services import regulatory_fx as lazy\n"
        "    return lazy.run()\n"
        "print(fx.run(), result(), expected)\n"
    )
    if kind in {"interpolated", "both"}:
        script = "expected = ${Math.max(1, 1)}\n" + script
        script += 'assert "${({value: "ok"}).value}" == "ok"\n'
    else:
        script = "expected = 1\n" + script
    if kind in {"escaped", "both"}:
        script += 'assert len("a\\\\tb") == 3\n'
    path = repository / "backend/dashboard/live-verification/probe.spec.ts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'import { execFileSync } from "node:child_process";\n'
        f"const script = `{script}`;\n"
        f"process.stdout.write(execFileSync({json.dumps(sys.executable)}, "
        '["-c", script], {encoding: "utf8"}));\n'
    )
    if command == "rewrite":
        assert _run(repository, "check")[-1] == "exit 1"
    _run(repository, command)
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=path.read_text(),
        cwd=repository / "backend",
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "1 1 1"
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("parent_import", [False, True])
@pytest.mark.parametrize("moves", [1, 2])
def test_rebased_relative_imports_resolve_from_historical_locations(
    repository: Path, package: bool, parent_import: bool, moves: int
) -> None:
    statement = "from .. import risk" if parent_import else "from ..risk import LIMIT"
    value = "risk.LIMIT" if parent_import else "LIMIT"
    replayed = f"\n\ndef replayed():\n    {statement}\n    return {value}\n"
    _run(repository, "move")
    feature = "fx"
    if moves == 2:
        _run(repository, "move", [("app.fx.domain", "app.market.domain")])
        feature = "market"
    leaf = "__init__" if package else "helpers"
    path = repository / f"backend/app/{feature}/domain/{leaf}.py"
    with path.open("a") as output:
        output.write(replayed)
    module = f"app.{feature}.domain" + ("" if package else ".helpers")
    with pytest.raises(subprocess.CalledProcessError):
        _python(repository, f"from {module} import replayed\nprint(replayed())")
    assert _run(repository, "check")[-1] == "exit 1"
    _run(repository, "rewrite")
    assert _python(repository, f"from {module} import replayed\nprint(replayed())") == "1"
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize(
    ("statement", "expression"),
    [
        ("from ..risk import LIMIT", "LIMIT"),
        ("from ..risk import LIMIT as bound", "bound"),
        ("from .. import risk", "risk.LIMIT"),
        ("from .. import risk as bound", "bound.LIMIT"),
        ("from ..risk import *", "LIMIT"),
    ],
)
def test_ambiguous_rebased_imports_refuse_all_commands(
    repository: Path, package: bool, statement: str, expression: str
) -> None:
    leaf = "__init__" if package else "helpers"
    original = repository / f"backend/app/domain/fx/{leaf}.py"
    original_module = "app.domain.fx" + ("" if package else ".helpers")
    if statement.endswith("*"):
        added = f"\n\n{statement}\nREPLAYED = {expression}\n"
        probe = "REPLAYED"
    else:
        added = f"\n\ndef replayed():\n    {statement}\n    return {expression}\n"
        probe = "replayed()"
    _git(repository, "branch", "before-move")
    _git(repository, "switch", "-qc", "caller")
    with original.open("a") as output:
        output.write(added)
    _git(repository, "add", str(original.relative_to(repository)))
    _git(
        repository, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "caller"
    )
    assert _python(repository, f"import {original_module} as caller\nprint(caller.{probe})") == "1"
    _git(repository, "switch", "-qc", "migration", "before-move")
    _run(repository, "move")
    current_risk = repository / "backend/app/fx/risk.py"
    current_risk.write_text("LIMIT = 9\n")
    _git(repository, "add", "-u")
    _git(repository, "add", str(current_risk.relative_to(repository)))
    _git(repository, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "move")
    _git(repository, "switch", "-q", "caller")
    _git(repository, "-c", "user.name=t", "-c", "user.email=t@example.com", "rebase", "migration")
    current_module = "app.fx.domain" + ("" if package else ".helpers")
    assert _python(repository, f"import {current_module} as caller\nprint(caller.{probe})") == "9"
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, capture_output=True, check=True
    ).stdout
    assert _run(repository, "check")[-1] == "exit 1"
    with pytest.raises(feature_moves.UnresolvedImport):
        _run(repository, "rewrite")
    with pytest.raises(feature_moves.UnresolvedImport):
        _run(repository, "move", [("app.fx.service", "app.currency.service")])
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, capture_output=True, check=True
    ).stdout
    assert before == after
    assert not (repository / "backend/app/currency").exists()
    current = repository / f"backend/app/fx/domain/{leaf}.py"
    explicit = statement.replace("..risk", "app.domain.risk").replace(
        ".. import", "app.domain import"
    )
    current.write_text(current.read_text().replace(statement, explicit))
    assert _run(repository, "rewrite") == ["exit 0"]
    assert _run(repository, "check") == ["exit 0"]
    assert _python(repository, f"import {current_module} as caller\nprint(caller.{probe})") == "1"


@pytest.mark.parametrize("parent_import", [False, True])
def test_equivalent_current_and_historical_targets_remain_executable(
    repository: Path, parent_import: bool
) -> None:
    _run(repository, "move")
    _run(repository, "move", [("app.domain.risk", "app.fx.risk")])
    statement = "from .. import risk" if parent_import else "from ..risk import LIMIT"
    value = "risk.LIMIT" if parent_import else "LIMIT"
    helpers = repository / "backend/app/fx/domain/helpers.py"
    with helpers.open("a") as output:
        output.write(f"\n\ndef equivalent():\n    {statement}\n    return {value}\n")
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]
    assert (
        _python(repository, "from app.fx.domain.helpers import equivalent\nprint(equivalent())")
        == "1"
    )


def test_rebased_imports_can_use_an_intermediate_location(repository: Path) -> None:
    _run(repository, "move")
    _run(repository, "move", [("app.fx.domain", "app.market.domain")])
    path = repository / "backend/app/market/domain/helpers.py"
    with path.open("a") as output:
        output.write("\n\ndef replayed():\n    from ..service import run\n    return run()\n")
    assert _run(repository, "check")[-1] == "exit 1"
    _run(repository, "rewrite")
    assert (
        _python(repository, "from app.market.domain.helpers import replayed\nprint(replayed())")
        == "1"
    )
    assert _run(repository, "check") == ["exit 0"]


def test_relative_package_exports_remain_executable(repository: Path) -> None:
    package = repository / "backend/app/domain/fx/__init__.py"
    package.write_text("LIMIT = 5\n\nclass Exported:\n    value = LIMIT\n")
    helpers = repository / "backend/app/domain/fx/helpers.py"
    with helpers.open("a") as output:
        output.write(
            "\n\ndef exported():\n    from . import Exported, LIMIT\n"
            "    return Exported.value + LIMIT\n"
        )
    _run(repository, "move")
    assert (
        _python(repository, "from app.fx.domain.helpers import exported\nprint(exported())") == "10"
    )
    assert _run(repository, "check") == ["exit 0"]
    assert _run(repository, "rewrite") == ["exit 0"]


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_unresolved_relative_imports_refuse_all_commands(repository: Path, kind: str) -> None:
    _run(repository, "move")
    _run(repository, "move", [("app.fx.domain", "app.market.domain")])
    name = "missing"
    if kind == "ambiguous":
        name = "risk"
        (repository / "backend/app/fx/risk.py").write_text("LIMIT = 9\n")
    path = repository / "backend/app/market/domain/helpers.py"
    with path.open("a") as output:
        output.write(f"\n\ndef invalid():\n    from ..{name} import LIMIT\n    return LIMIT\n")
    before = subprocess.run(
        ["git", "diff", "HEAD"], cwd=repository, capture_output=True, check=True
    ).stdout
    assert _run(repository, "check")[-1] == "exit 1"
    with pytest.raises(feature_moves.UnresolvedImport):
        _run(repository, "rewrite")
    with pytest.raises(feature_moves.UnresolvedImport):
        _run(repository, "move", [("app.fx.service", "app.currency.service")])
    after = subprocess.run(
        ["git", "diff", "HEAD"], cwd=repository, capture_output=True, check=True
    ).stdout
    assert before == after
    assert _python(repository, "from app.fx.service import run\nprint(run())") == "1"
    assert not (repository / "backend/app/currency").exists()
