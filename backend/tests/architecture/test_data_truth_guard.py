"""Exercise the data-truth lint interface and real strict type checker."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TypedDict

import pytest
from pydantic import TypeAdapter

from scripts import data_truth_guard as guard


def test_migrated_modules_pass_data_truth_lint() -> None:
    assert guard.main([]) == 0


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("amount: float = 1", "float-financial-value"),
        ("amount = Decimal(0.1)", "float-financial-value"),
        ("amount = float(raw)", "float-financial-value"),
        ("amount = builtins.float(raw)", "float-financial-value"),
        ("from builtins import float as binary\namount = binary(raw)", "float-financial-value"),
        ("amount: Money | None = None", "nullable-figure"),
        ("def result() -> CalculationResult[Money] | None: pass", "nullable-figure"),
        ("amount: Optional[Money] = None", "nullable-figure"),
        ("amount: 'Money | None' = None", "nullable-figure"),
        ("type OptionalAmount = Decimal | None", "nullable-figure"),
        (
            "from typing import Optional as Maybe\n"
            "from app.core.data_truth.types import Money as Amount\n"
            "amount: Maybe[Amount] = None",
            "nullable-figure",
        ),
        ("def result() -> CalculationResult[Money]: return None", "bare-missing-result"),
        ("async def result() -> Money: return", "bare-missing-result"),
        ("match result:\n case Value(): pass", "non-exhaustive-match"),
        ("match result:\n case _: return_zero()", "non-exhaustive-match"),
        ("match result:\n case _ if flag: assert_never(result)", "non-exhaustive-match"),
        ("match result:\n case _: assert_never(other)", "non-exhaustive-match"),
        ("def broken(:", "invalid Python syntax"),
    ],
)
def test_lint_rejects_regressions_at_its_executable_interface(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, rule: str
) -> None:
    module = tmp_path / "engine.py"
    module.write_text(source, encoding="utf-8")
    assert guard.main([str(module)]) == 1
    assert rule in capsys.readouterr().out


def test_lint_accepts_explicit_states_and_command_callbacks(tmp_path: Path) -> None:
    module = tmp_path / "engine.py"
    module.write_text(
        "def result() -> CalculationResult[Money]:\n"
        " def log() -> None:\n"
        "  return None\n"
        " return Unavailable('Balance missing')\n"
        "def render(result: CalculationResult[Money]):\n"
        " match result:\n"
        "  case Value(): return result.value\n"
        "  case Unavailable() | NotApplicable(): return result.reason\n"
        "  case _: assert_never(result)\n",
        encoding="utf-8",
    )
    assert guard.main([str(module)]) == 0


class _Diagnostic(TypedDict):
    message: str


class _Report(TypedDict):
    generalDiagnostics: list[_Diagnostic]


def _typecheck(tmp_path: Path, source: str) -> list[str]:
    module = tmp_path / "probe.py"
    module.write_text(source, encoding="utf-8")
    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        json.dumps(
            {
                "include": [str(module)],
                "extraPaths": [str(guard.BACKEND)],
                "typeCheckingMode": "strict",
                "pythonVersion": "3.13",
                "reportAny": "error",
                "reportExplicitAny": False,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["basedpyright", "--project", str(config), "--outputjson"],
        cwd=guard.BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr
    report = TypeAdapter(_Report).validate_json(result.stdout)
    return [item["message"] for item in report["generalDiagnostics"]]


_IMPORTS = (
    "from decimal import Decimal\n"
    "from typing import assert_never\n"
    "from app.core.data_truth.types import (Money, Rate, Ratio, Percentage, "
    "CalculationResult, ResultStatus, Value, Unavailable, NotApplicable)\n"
)


def test_strict_types_reject_mixed_kinds_and_missing_values(tmp_path: Path) -> None:
    # Every bad expression must diagnose, not merely an incidental bad import.
    cases = {
        "Money(0.1, 'USD')": "float",
        "Money(Decimal(1), 'USD') + Ratio(Decimal(1))": "Ratio",
        "Money(Decimal(1), 'USD').scale(Percentage(Decimal(5)))": "Percentage",
        "Value(None)": "None",
    }
    for expression, expected in cases.items():
        diagnostics = _typecheck(tmp_path, _IMPORTS + f"probe = {expression}\n")
        assert any(expected in message for message in diagnostics), diagnostics


def test_strict_types_require_all_result_and_enum_states(tmp_path: Path) -> None:
    exhaustive = (
        "def render(result: CalculationResult[Money]) -> str:\n"
        " match result:\n"
        "  case Value(): return str(result.value.amount)\n"
        "  case Unavailable() | NotApplicable(): return result.reason\n"
        "  case _: assert_never(result)\n"
        "def label(status: ResultStatus) -> str:\n"
        " match status:\n"
        "  case ResultStatus.VALUE: return 'Value'\n"
        "  case ResultStatus.UNAVAILABLE: return 'Unavailable'\n"
        "  case ResultStatus.NOT_APPLICABLE: return 'Not applicable'\n"
        "  case _: assert_never(status)\n"
    )
    # Disable only unused-import noise in probes; strict argument diagnostics remain active.
    imports = _IMPORTS + "# pyright: reportUnusedImport=false\n"
    assert _typecheck(tmp_path, imports + exhaustive) == []
    incomplete = exhaustive.replace(" | NotApplicable()", "").replace(
        "  case ResultStatus.NOT_APPLICABLE: return 'Not applicable'\n", ""
    )
    diagnostics = _typecheck(tmp_path, imports + incomplete)
    assert any('"NotApplicable"' in message and '"Never"' in message for message in diagnostics)
    assert any("NOT_APPLICABLE" in message and '"Never"' in message for message in diagnostics)
