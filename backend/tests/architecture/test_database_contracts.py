"""Execute protection failures through pytest collection and JUnit consumers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import database_contracts
from scripts.test_suites import case_key

BACKEND = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("scenario", "passes"),
    [
        ("unit", True),
        ("integration", True),
        ("protected-unit", False),
        ("protected-fake", False),
        ("protected-indirect-fake", False),
        ("forbidden-path-fake", False),
        ("feature-api-fake", False),
        ("unit-database-fixture", False),
        ("missing-postgres", False),
        ("skipped-contract", False),
        ("marked-unit", False),
        ("stale-protected", False),
    ],
)
def test_contract_collection_and_required_execution(
    tmp_path: Path, scenario: str, passes: bool
) -> None:
    module = "tests/db/test_probe.py" if scenario == "forbidden-path-fake" else "test_probe.py"
    if scenario == "feature-api-fake":
        module = "tests/icaap/api/test_probe.py"
    nodeid = module + "::test_probe"
    integration = scenario in {
        "integration",
        "protected-fake",
        "protected-indirect-fake",
        "missing-postgres",
        "skipped-contract",
    }
    protected = scenario.startswith("protected-") or scenario in {
        "missing-postgres",
        "skipped-contract",
        "stale-protected",
    }
    suites = tmp_path / "suites.json"
    _ = suites.write_text(json.dumps({"cases": {nodeid: "integration" if integration else "unit"}}))
    contracts = tmp_path / "contracts.json"
    _ = contracts.write_text(
        json.dumps(
            {
                "contracts": [
                    {
                        "category": "SQL",
                        "reason": "Synthetic guard contract",
                        "cases": [nodeid + "_old" if scenario == "stale-protected" else nodeid],
                    }
                ]
                if protected
                else []
            }
        )
    )
    _ = (tmp_path / "conftest.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "from scripts import test_suites, database_contracts\n"
        f"test_suites.CATALOGUE = Path({str(suites)!r})\n"
        "test_suites.catalogue.cache_clear()\n"
        f"database_contracts.CATALOGUE = Path({str(contracts)!r})\n"
        "@pytest.fixture\ndef current_annex_repository(): return object()\n"
        "@pytest.fixture\ndef annex_input(current_annex_repository):\n"
        "    return current_annex_repository\n"
        "@pytest.fixture\ndef db_session(): return None\n"
    )
    arguments = {
        "protected-fake": "current_annex_repository",
        "protected-indirect-fake": "annex_input",
        "forbidden-path-fake": "current_annex_repository",
        "feature-api-fake": "current_annex_repository",
        "unit-database-fixture": "db_session",
    }.get(scenario, "")
    decorator = "@pytest.mark.database_contract\n" if scenario == "marked-unit" else ""
    body = (
        "pytest.skip('required contract was skipped')" if scenario == "skipped-contract" else "pass"
    )
    path = tmp_path / module
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(f"import pytest\n{decorator}def test_probe({arguments}):\n    {body}\n")
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "CI_REQUIRE_DATABASE_CONTRACTS": "1",
        "TEST_DATABASE_URL": "sqlite://"
        if scenario == "missing-postgres"
        else "postgresql+psycopg://guard:guard@127.0.0.1:1/guard",
    }
    for name in ("CI_UNIT_REPORT_DIR", "CI_DATABASE_CENSUS"):
        _ = environment.pop(name, None)
    selection: list[str] = []
    if scenario == "stale-protected":
        environment["CI_UNIT_REPORT_DIR"] = str(tmp_path / "evidence")
        selection = ["-m", "unit"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--rootdir=.",
            module,
            "-p",
            "scripts.test_suites_plugin",
            *selection,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert (result.returncode == 0) == passes, result.stdout + result.stderr
    if not passes:
        assert any(
            token in result.stdout + result.stderr
            for token in (
                "Protected database contract",
                "fake is forbidden",
                "database resource fixtures",
            )
        ), result.stdout + result.stderr


@pytest.mark.parametrize("change", ["retained", "unit", "removed"])
def test_protected_classification_requires_the_same_integration_identity(change: str) -> None:
    nodeid = "tests/contracts/test_sql.py::test_sql"
    classified = (
        {} if change == "removed" else {nodeid: "unit" if change == "unit" else "integration"}
    )
    if change == "retained":
        database_contracts.check_classification(classified, protected={nodeid: "SQL selection"})
    else:
        with pytest.raises(ValueError, match="Protected database contract"):
            database_contracts.check_classification(classified, protected={nodeid: "SQL selection"})


@pytest.mark.parametrize(
    "change", ["retained", "unit-owner", "skipped", "failed", "missing", "duplicate"]
)
def test_protected_junit_proves_one_successful_postgres_execution(
    tmp_path: Path, change: str
) -> None:
    nodeid = "tests/contracts/test_sql.py::test_sql"
    owner = "unit-tests" if change == "unit-owner" else "pg-suite-1"
    folder = tmp_path / owner
    folder.mkdir()
    report = folder / "report.xml"
    tag = {"skipped": "<skipped/>", "failed": "<failure/>"}.get(change, "")
    case = f'<testcase classname="tests.contracts.test_sql" name="test_sql">{tag}</testcase>'
    if change == "missing":
        case = ""
    elif change == "duplicate":
        case += case
    _ = report.write_text(f"<testsuite>{case}</testsuite>")
    if change == "retained":
        database_contracts.verify_execution(
            [report], {nodeid}, case_key, protected={nodeid: "SQL selection"}
        )
    else:
        with pytest.raises(ValueError, match="Protected database contract"):
            database_contracts.verify_execution(
                [report], {nodeid}, case_key, protected={nodeid: "SQL selection"}
            )


def test_primary_opt_in_keeps_protection_without_forcing_primary_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodeid = "tests/contracts/test_primary.py::test_primary"
    path = tmp_path / "contracts.json"
    _ = path.write_text(
        json.dumps(
            {
                "contracts": [
                    {"category": "primary", "reason": "Explicit opt-in", "cases": [nodeid]}
                ],
                "primary_opt_in": [nodeid],
            }
        )
    )
    monkeypatch.setattr(database_contracts, "CATALOGUE", path)
    monkeypatch.delenv("REAL_DATA_DATABASE_URL", raising=False)
    assert not database_contracts.must_execute(nodeid)
    with pytest.raises(ValueError, match="must remain integration"):
        database_contracts.check_classification({nodeid: "unit"})
    with pytest.raises(ValueError, match="fake is forbidden"):
        database_contracts.check_collection(nodeid, "integration", ["current_annex_repository"])
    folder = tmp_path / "pg-suite-1"
    folder.mkdir()
    report = folder / "report.xml"
    _ = report.write_text(
        '<testsuite><testcase classname="tests.contracts.test_primary" name="test_primary">'
        "<skipped/></testcase></testsuite>"
    )
    database_contracts.verify_execution([report], {nodeid}, case_key)
    monkeypatch.setenv("REAL_DATA_DATABASE_URL", "postgresql+psycopg://unused@127.0.0.1:1/unused")
    assert database_contracts.must_execute(nodeid)
    with pytest.raises(ValueError, match="did not execute successfully"):
        database_contracts.verify_execution([report], {nodeid}, case_key)
