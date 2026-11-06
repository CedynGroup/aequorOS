"""Public modules declare re-exports, never local implementations or initialization.

This is a syntax contract, not a claim that importing a dependency has no side
effects. Parse and compile without executing files, preserving deliberate import order.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture._planes import APP

# An architecture coverage floor, not an exception list: discover new public.py
# files recursively, but fail if an established interface disappears from scope.
REQUIRED_ROOTS = frozenset(
    {
        "ai",
        "attestation",
        "behavioral",
        "bi",
        "capital",
        "case",
        "credit",
        "data_engine",
        "forecasting",
        "ftp",
        "fx",
        "icaap",
        "identity",
        "irrbb",
        "liquidity",
        "live",
        "market_data",
        "notifications",
        "operator",
        "policy",
        "rating",
        "reporting",
        "storage",
        "stress",
    }
)


def _export_annotation(annotation: ast.expr) -> bool:
    match annotation:
        case ast.Subscript(value=ast.Name(id="list"), slice=ast.Name(id="str")):
            return True
        case ast.Subscript(
            value=ast.Name(id="tuple"),
            slice=ast.Tuple(elts=[ast.Name(id="str"), ast.Constant(value=ellipsis)]),
        ):
            return ellipsis is Ellipsis
        case _:
            return False


def _literal_exports(statement: ast.stmt) -> bool:
    match statement:
        case ast.Assign(targets=[ast.Name(id="__all__")], value=value):
            pass
        case ast.AnnAssign(target=ast.Name(id="__all__"), value=value, annotation=annotation):
            if not _export_annotation(annotation):
                return False
        case _:
            return False
    return isinstance(value, ast.List | ast.Tuple) and all(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts
    )


def _violations(app: Path) -> list[str]:
    files = sorted(app.rglob("public.py"))
    missing = sorted(REQUIRED_ROOTS - {p.parent.name for p in files if p.parent.parent == app})
    failures = [f"{root}/public.py: missing required public interface" for root in missing]
    for path in files:
        relative = path.relative_to(app)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            _ = compile(tree, str(path), "exec", dont_inherit=True)
        except SyntaxError as error:
            failures.append(f"{relative}:{error.lineno}: invalid syntax")
            continue
        for index, statement in enumerate(tree.body):
            if isinstance(statement, ast.Import | ast.ImportFrom):
                continue
            if (
                index == 0
                and isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            if not _literal_exports(statement):
                failures.append(
                    f"{relative}:{statement.lineno}: {type(statement).__name__} is not a re-export "
                    "or literal __all__ declaration"
                )
    return failures


def test_public_interfaces_are_reexports_only() -> None:
    assert _violations(APP) == []


@pytest.fixture
def public_app(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    for root in REQUIRED_ROOTS:
        directory = app / root
        directory.mkdir(parents=True)
        (directory / "public.py").write_text('"""Interface."""\n', encoding="utf-8")
    return app


@pytest.mark.parametrize(
    "source",
    [
        "",
        '# A comment mentioning def, class and initialize() is not code.\n"""Prose: call()."""\n',
        "from __future__ import annotations\nimport missing_dependency as dependency\n",
        "from .service import calculate as calculate\nfrom ..other import Result\n",
        "from .service import *\n",
        '__all__ = ["Result", "calculate"]\n',
        '__all__ = ("Result",)\n',
        '__all__: list[str] = ["Result"]\n',
        "__all__: tuple[str, ...] = ()\n",
        (
            '"""Interface."""\n'
            "# isort: split\n"
            "from .service import late  # noqa: E402 - deliberate order\n"
            '__all__ = ["late"]\n'
        ),
        (
            '"""Interface."""\n'
            "from __future__ import annotations\n"
            "from .service import Result as Result\n"
            '__all__: tuple[str, ...] = ("Result",)\n'
        ),
    ],
)
def test_public_interface_guard_accepts_declarations(public_app: Path, source: str) -> None:
    path = public_app / "capital" / "public.py"
    path.write_text(source, encoding="utf-8")
    assert _violations(public_app) == []
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    "source,kind",
    [
        ("def calculate(): pass\n", "FunctionDef"),
        ("async def calculate(): pass\n", "AsyncFunctionDef"),
        ("class Result: pass\n", "ClassDef"),
        ("initialize()\n", "Expr"),
        ("Result = calculate()\n", "Assign"),
        ("Result = dependency.Result\n", "Assign"),
        ("Result = 1\n", "Assign"),
        ("Result: int\n", "AnnAssign"),
        ("Result = lambda: 1\n", "Assign"),
        ("if TYPE_CHECKING:\n    from .service import Result\n", "If"),
        ("if True:\n    initialize()\n", "If"),
        ("try:\n    import dependency\nexcept ImportError:\n    pass\n", "Try"),
        ("__all__ = build_exports()\n", "Assign"),
        ("__all__ = dependency.__all__\n", "Assign"),
        ('__all__ += ["Result"]\n', "AugAssign"),
        ('__all__.append("Result")\n', "Expr"),
        ('__all__ = [str("Result")]\n', "Assign"),
        ("__all__ = [*names]\n", "Assign"),
        ('__all__ = ["Result"] + ["Other"]\n', "Assign"),
        ("__all__ = [name for name in names]\n", "Assign"),
        ('__all__ = other = ["Result"]\n', "Assign"),
        ("__all__ = [1]\n", "Assign"),
        ('__all__: build_type() = ["Result"]\n', "AnnAssign"),
        ('__all__: tuple[str, calculate()] = ["Result"]\n', "AnnAssign"),
        ('import dependency\n"Not a module docstring"\n', "Expr"),
        ('f"{initialize()}"\n', "Expr"),
    ],
)
def test_public_interface_guard_rejects_logic(public_app: Path, source: str, kind: str) -> None:
    # New/nested public modules must be discovered too, not just the floor.
    path = public_app / "new_feature" / "nested" / "public.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    failures = _violations(public_app)
    assert len(failures) == 1
    assert failures[0].startswith("new_feature/nested/public.py:")
    assert f"{kind} is not a re-export" in failures[0]


@pytest.mark.parametrize("root", sorted(REQUIRED_ROOTS))
def test_public_interface_guard_requires_established_roots(public_app: Path, root: str) -> None:
    (public_app / root / "public.py").unlink()
    assert _violations(public_app) == [f"{root}/public.py: missing required public interface"]


def test_public_interface_guard_rejects_missing_tree(tmp_path: Path) -> None:
    assert len(_violations(tmp_path / "absent")) == len(REQUIRED_ROOTS)


def test_public_interface_guard_rejects_invalid_syntax(public_app: Path) -> None:
    (public_app / "capital" / "public.py").write_text("from .service import (\n", encoding="utf-8")
    assert _violations(public_app) == ["capital/public.py:1: invalid syntax"]


@pytest.mark.parametrize(
    "source,line",
    [
        ("import os\nfrom __future__ import annotations\n", 2),
        ("from __future__ import nonexistent_feature\n", 1),
        ("from __future__ import *\n", 1),
    ],
)
def test_public_interface_guard_rejects_compile_invalid_imports(
    public_app: Path, source: str, line: int
) -> None:
    path = public_app / "capital" / "public.py"
    path.write_text(source, encoding="utf-8")
    # CPython's source compiler is the independent validity oracle; neither
    # compiling nor scanning may execute the fixture's imports.
    with pytest.raises(SyntaxError) as error:
        _ = compile(source, str(path), "exec", dont_inherit=True)
    assert error.value.lineno == line
    assert _violations(public_app) == [f"capital/public.py:{line}: invalid syntax"]
