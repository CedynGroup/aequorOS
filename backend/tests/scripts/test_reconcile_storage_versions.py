from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, MagicMock, Mock

import pytest
from psycopg import OperationalError as PostgresOperationalError
from psycopg.errors import RaiseException
from psycopg.pq import DiagnosticField
from sqlalchemy import Engine
from sqlalchemy.engine import URL, Connection
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from scripts.backup_storage import RestoredVersion, RestoreReport
from scripts.dr_common import DisasterRecoveryError
from scripts.reconcile_storage_versions import cli, load_mapping

FAILURE = "[reconcile] FAILED: recovery refused; transaction rolled back."


def _report() -> RestoreReport:
    return RestoreReport(
        "synthetic",
        "synthetic",
        objects=2,
        versions=[
            RestoredVersion("bucket", "a", "null", "restored-a", "a" * 64),
            RestoredVersion("bucket", "b", "null", "restored-b", "b" * 64),
        ],
    )


def test_mapping_identity_includes_bucket_and_key(tmp_path: Path) -> None:
    report = _report()
    report.versions.append(RestoredVersion("another-bucket", "a", "null", "restored-a", "c" * 64))
    report.objects += 1
    path = tmp_path / "restore.json"
    report.write(path)
    assert load_mapping(path) == report


def test_loader_accepts_unversioned_entries_in_full_report(tmp_path: Path) -> None:
    report = _report()
    report.versions.append(RestoredVersion("temp-bucket", "upload", "", "", "c" * 64))
    report.objects += 1
    path = tmp_path / "restore.json"
    report.write(path)
    assert load_mapping(path) == report


@pytest.mark.parametrize(
    "failure", ["missing-id", "blank-id", "duplicate", "collapsed", "count", "checksum"]
)
def test_loader_refuses_unusable_or_ambiguous_mapping(tmp_path: Path, failure: str) -> None:
    report = _report()
    if failure == "blank-id":
        report.versions[0].restored_version_id = " "
    elif failure == "duplicate":
        report.versions.append(report.versions[0])
        report.objects += 1
    elif failure == "collapsed":
        report.versions[1].key = report.versions[0].key
        report.versions[1].backed_up_version_id = "other-old-id"
        report.versions[1].restored_version_id = report.versions[0].restored_version_id
    elif failure == "count":
        report.objects += 1
    elif failure == "checksum":
        report.versions[0].sha256 = "z" * 64
    path = tmp_path / "restore.json"
    report.write(path)
    if failure == "missing-id":
        raw = cast(dict[str, list[dict[str, object]]], json.loads(path.read_text()))
        del raw["versions"][0]["restored_version_id"]
        path.write_text(json.dumps(raw))
    with pytest.raises((DisasterRecoveryError, ValueError)):
        load_mapping(path)


def test_loader_refuses_truncated_report(tmp_path: Path) -> None:
    path = tmp_path / "restore.json"
    path.write_text('{"versions": [')
    with pytest.raises(ValueError):
        load_mapping(path)


def test_cli_refuses_primary_before_opening_a_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app:synthetic@primary.example/risk")
    monkeypatch.setenv(
        "RESTORE_DATABASE_URL", "postgresql+psycopg://recovery:synthetic@primary.example/risk"
    )
    assert cli(["--mapping", "absent.json", "--storage-env", "dev", "--reason", "synthetic"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        f"{FAILURE} Refusing to operate on a protected production database. "
        "Point at a dedicated restore target.\n"
    )


def test_cli_refuses_non_postgres_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "restore.json"
    _report().write(path)
    monkeypatch.setenv("RESTORE_DATABASE_URL", "sqlite://")
    assert cli(["--mapping", str(path), "--storage-env", "dev", "--reason", "synthetic"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"{FAILURE} Recovery requires a PostgreSQL target.\n"


def test_loader_refuses_duplicate_json_fields(tmp_path: Path) -> None:
    path = tmp_path / "restore.json"
    path.write_text('{"versions": [], "versions": []}')
    with pytest.raises(DisasterRecoveryError, match="Ambiguous"):
        load_mapping(path)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing-target", "RESTORE_DATABASE_URL must name a dedicated recovery target."),
        ("missing-env", "Environment file not found."),
        ("duplicate-fields", "Ambiguous duplicate field in restore report."),
        ("empty-mapping", "Restore report must contain every restored object mapping."),
        ("incomplete-mapping", "Incomplete restore version mapping."),
        ("ambiguous-mapping", "Ambiguous restore version mapping."),
        ("invalid-json", ""),
        ("invalid-schema", ""),
        ("missing-file", ""),
    ],
)
def test_cli_preflight_diagnostics(
    case: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "private-mapping.json"
    report = _report()
    if case == "empty-mapping":
        report.versions.clear()
    elif case == "incomplete-mapping":
        report.versions[0].sha256 = "invalid"
    elif case == "ambiguous-mapping":
        report.versions[1] = report.versions[0]
    report.write(path)
    if case == "duplicate-fields":
        path.write_text('{"versions": [], "versions": []}')
    elif case == "invalid-json":
        path.write_text('{"private": ')
    elif case == "invalid-schema":
        path.write_text('{"source": "private-value"}')
    elif case == "missing-file":
        path.unlink()
    monkeypatch.setenv("RESTORE_DATABASE_URL", "" if case == "missing-target" else "sqlite://")
    args = ["--mapping", str(path), "--storage-env", "dev", "--reason", "synthetic"]
    if case == "missing-env":
        args.extend(["--env-file", str(tmp_path / "private-env-file")])
    assert cli(args) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (f"{FAILURE} {reason}\n" if reason else f"{FAILURE}\n")


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (
            DBAPIError(
                "private SQL",
                {"password": "private parameter"},
                RaiseException(
                    "private driver message",
                    info={
                        DiagnosticField.MESSAGE_PRIMARY: (
                            b"Missing or ambiguous mapping for pinned storage version"
                        ),
                        DiagnosticField.MESSAGE_DETAIL: b"private detail",
                        DiagnosticField.MESSAGE_HINT: b"private hint",
                        DiagnosticField.CONTEXT: b"private context",
                    },
                ),
            ),
            "Missing or ambiguous mapping for pinned storage version",
        ),
        (DBAPIError("private SQL", {}, PostgresOperationalError("private connection")), ""),
        (DBAPIError("private SQL", {}, RuntimeError("private driver message")), ""),
        (SQLAlchemyError("private SQLAlchemy message"), ""),
        (ValueError("private invalid URL"), ""),
        (OSError("private path"), ""),
    ],
)
def test_cli_database_failures_show_only_postgres_primary_diagnostic(
    failure: Exception,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scalar, exit_transaction, dispose, args = _database_cli(tmp_path, monkeypatch)
    scalar.side_effect = [True, failure]
    assert cli(args) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (f"{FAILURE} {reason}\n" if reason else f"{FAILURE}\n")
    exit_transaction.assert_called_once_with(type(failure), failure, ANY)
    dispose.assert_called_once_with()


def test_cli_reports_unproved_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scalar, _exit_transaction, dispose, args = _database_cli(tmp_path, monkeypatch)
    scalar.side_effect = [False]
    assert cli(args) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"{FAILURE} Proved cross-tenant PostgreSQL recovery session required.\n"
    assert scalar.call_count == 1
    dispose.assert_called_once_with()


def test_cli_success_preserves_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scalar, exit_transaction, dispose, args = _database_cli(tmp_path, monkeypatch)
    scalar.side_effect = [True, 4]
    assert cli(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "reconciled_references": 4,
        "audit_event": "storage.version_reconciled",
    }
    exit_transaction.assert_called_once_with(None, None, None)
    dispose.assert_called_once_with()


def _database_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Mock, Mock, Mock, list[str]]:
    """Substitute only the database boundary; run the CLI and reconciliation code."""
    path = tmp_path / "restore.json"
    _report().write(path)
    monkeypatch.setenv("RESTORE_DATABASE_URL", "postgresql://incident:private@restore.example/db")
    scalar = Mock()
    connection = Mock(spec=Connection, dialect=SimpleNamespace(name="postgresql"), scalar=scalar)
    exit_transaction = Mock(return_value=False)
    transaction = MagicMock(__enter__=Mock(return_value=connection), __exit__=exit_transaction)
    dispose = Mock()
    engine = Mock(spec=Engine, begin=Mock(return_value=transaction), dispose=dispose)

    def create_engine(_url: URL) -> Engine:
        return cast(Engine, engine)

    monkeypatch.setattr("scripts.reconcile_storage_versions.create_engine", create_engine)
    return (
        scalar,
        exit_transaction,
        dispose,
        [
            "--mapping",
            str(path),
            "--storage-env",
            "dev",
            "--reason",
            "synthetic",
        ],
    )
