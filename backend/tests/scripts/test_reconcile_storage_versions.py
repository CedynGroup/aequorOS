from __future__ import annotations

import json
import os
import socket
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

from scripts import reconcile_storage_versions as reconciler
from scripts.backup_storage import RestoredVersion, RestoreReport
from scripts.dr_common import DisasterRecoveryError
from scripts.reconcile_storage_versions import cli, load_mapping

FAILURE = "[reconcile] FAILED: recovery refused; transaction rolled back."
PRIMARY_URL_VARIABLES = ("DATABASE_URL", "WORKER_DATABASE_URL", "OPERATOR_DATABASE_URL")


@pytest.fixture(autouse=True)
def synthetic_primary_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*PRIMARY_URL_VARIABLES, *(key for key in os.environ if key.startswith("PG"))):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:synthetic@primary.example/risk")

    addresses = {
        "primary.example": ("192.0.2.1",),
        "other-primary.example": ("192.0.2.2",),
        "restore.example": ("198.51.100.1",),
        "primary": ("192.0.2.1",),
        "primary-alias": ("192.0.2.1",),
        "replica": ("192.0.2.2",),
        "restore": ("198.51.100.1",),
        "backup": ("198.51.100.2",),
        "localhost": ("127.0.0.1", "::1"),
    }

    def resolve(host: str, _port: object, **_kwargs: object) -> list[tuple[object, ...]]:
        if host.lower().rstrip(".") not in addresses:
            raise socket.gaierror("Synthetic unresolved endpoint")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in addresses[host.lower().rstrip(".")]
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


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


@pytest.mark.parametrize("value", [None, "", " \t"], ids=["unset", "empty", "whitespace"])
def test_cli_refuses_without_primary_protection(
    value: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "restore.json"
    _report().write(path)
    for name in PRIMARY_URL_VARIABLES:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("RESTORE_DATABASE_URL", "postgresql://incident:synthetic@restore.example/db")
    create_engine = Mock(side_effect=AssertionError("Unprotected target connection attempted"))
    monkeypatch.setattr("scripts.reconcile_storage_versions.create_engine", create_engine)

    assert cli(["--mapping", str(path), "--storage-env", "dev", "--reason", "synthetic"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        f"{FAILURE} At least one protected primary database URL must be configured.\n"
    )
    create_engine.assert_not_called()


@pytest.mark.parametrize("name", ["WORKER_DATABASE_URL", "OPERATOR_DATABASE_URL"])
def test_cli_refuses_primary_from_runtime_configuration(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "restore.json"
    _report().write(path)
    monkeypatch.setenv(name, "postgresql://app:synthetic@other-primary.example/risk")
    monkeypatch.setenv(
        "RESTORE_DATABASE_URL", "postgresql+psycopg://incident:synthetic@other-primary.example/risk"
    )
    create_engine = Mock(side_effect=AssertionError("Protected target connection attempted"))
    monkeypatch.setattr("scripts.reconcile_storage_versions.create_engine", create_engine)

    assert cli(["--mapping", str(path), "--storage-env", "dev", "--reason", "synthetic"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        f"{FAILURE} Refusing to operate on a protected production database. "
        "Point at a dedicated restore target.\n"
    )
    create_engine.assert_not_called()


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


@pytest.mark.parametrize(
    "routing",
    [
        pytest.param(
            ("postgresql://localhost/risk", "postgresql://127.0.0.1/risk", False),
            id="loopback-alias",
        ),
        pytest.param(
            ("postgresql://primary/risk", "postgresql://primary-alias/risk", False), id="dns-alias"
        ),
        pytest.param(
            ("postgresql://[::1]/risk", "postgresql:///risk?host=restore&host=localhost", False),
            id="fallback-dns-alias",
        ),
        pytest.param(
            ("postgresql://192.0.2.1/risk", "postgresql://198.51.100.1/risk", True),
            id="distinct-addresses",
        ),
        pytest.param(
            ("postgresql://primary/risk", "postgresql://restore/risk?host=primary", False),
            id="target-query-host",
        ),
        pytest.param(
            ("postgresql://restore/risk?host=primary", "postgresql://primary/risk", False),
            id="primary-query-host",
        ),
        pytest.param(
            ("postgresql://primary:5432/risk", "postgresql://primary:6543/risk?port=5432", False),
            id="target-query-port",
        ),
        pytest.param(
            ("postgresql://primary:6543/risk?port=5432", "postgresql://primary:5432/risk", False),
            id="primary-query-port",
        ),
        pytest.param(
            ("postgresql://primary/risk", "postgresql://primary/restore?dbname=risk", False),
            id="target-query-database",
        ),
        pytest.param(
            ("postgresql://primary/restore?dbname=risk", "postgresql://primary/risk", False),
            id="primary-query-database",
        ),
        pytest.param(
            ("postgresql://primary/risk", "postgresql:///risk?host=restore&host=primary", False),
            id="target-fallback-host",
        ),
        pytest.param(
            ("postgresql:///risk?host=restore&host=primary", "postgresql://primary/risk", False),
            id="primary-fallback-host",
        ),
        pytest.param(
            (
                "postgresql://primary:6543/risk",
                "postgresql:///risk?host=restore:5432&host=primary:6543",
                False,
            ),
            id="paired-fallback-ports",
        ),
        pytest.param(
            (
                "postgresql://primary/risk",
                "postgresql:///risk?host=restore:6543&host=primary",
                False,
            ),
            id="default-fallback-port",
        ),
        pytest.param(
            ("postgresql://PRIMARY./risk", "postgresql://primary/risk", False),
            id="dns-canonicalization",
        ),
        pytest.param(
            ("postgresql://[::1]/risk", "postgresql://[0:0:0:0:0:0:0:1]/risk", False),
            id="ipv6-canonicalization",
        ),
        pytest.param(
            ("postgresql:///risk?host=/tmp/Primary", "postgresql:///risk?host=/tmp/Primary", False),
            id="same-socket",
        ),
        pytest.param(
            ("postgresql:///risk?host=primary", "postgresql:///risk?host=restore", True),
            id="distinct-query-hosts",
        ),
        pytest.param(
            (
                "postgresql://primary/risk?host=primary",
                "postgresql://primary/risk?host=restore",
                True,
            ),
            id="distinct-query-overrides",
        ),
        pytest.param(
            ("postgresql://primary:5432/risk", "postgresql://primary:5432/risk?port=6543", True),
            id="distinct-query-ports",
        ),
        pytest.param(
            ("postgresql://primary/risk", "postgresql://primary/risk?dbname=restore", True),
            id="distinct-query-databases",
        ),
        pytest.param(
            (
                "postgresql:///risk?host=primary&host=replica",
                "postgresql:///risk?host=restore&host=backup",
                True,
            ),
            id="disjoint-fallback-hosts",
        ),
        pytest.param(
            (
                "postgresql://primary:5432/risk",
                "postgresql:///risk?host=primary:6543&host=restore:5432",
                True,
            ),
            id="distinct-paired-ports",
        ),
        pytest.param(
            ("postgresql:///risk?host=/tmp/Primary", "postgresql:///risk?host=/tmp/primary", True),
            id="case-sensitive-sockets",
        ),
    ],
)
def test_cli_compares_effective_postgres_targets(
    routing: tuple[str, str, bool],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scalar, exit_transaction, dispose, args = _database_cli(tmp_path, monkeypatch)
    scalar.side_effect = [True, 4]
    factory = Mock(wraps=reconciler.create_engine)
    monkeypatch.setattr(reconciler, "create_engine", factory)
    primary, target, allowed = routing
    monkeypatch.setenv("DATABASE_URL", primary)
    monkeypatch.setenv("RESTORE_DATABASE_URL", target)

    assert cli(args) == (0 if allowed else 1)

    output = capsys.readouterr()
    if allowed:
        assert output.err == ""
        assert json.loads(output.out)["reconciled_references"] == 4
        factory.assert_called_once()
        exit_transaction.assert_called_once_with(None, None, None)
        dispose.assert_called_once_with()
    else:
        assert output.out == ""
        assert output.err == (
            f"{FAILURE} Refusing to operate on a protected production database. "
            "Point at a dedicated restore target.\n"
        )
        factory.assert_not_called()
        scalar.assert_not_called()


@pytest.mark.parametrize(
    "routing",
    [
        pytest.param(
            ("DATABASE_URL", "postgresql://unresolved.invalid/risk"), id="unresolved-primary"
        ),
        pytest.param(
            ("RESTORE_DATABASE_URL", "postgresql://unresolved.invalid/risk"), id="unresolved-target"
        ),
        pytest.param(("DATABASE_URL", "postgresql:///risk?service=private"), id="primary-service"),
        pytest.param(
            ("RESTORE_DATABASE_URL", "postgresql:///risk?service=private"), id="target-service"
        ),
        pytest.param(
            ("RESTORE_DATABASE_URL", "postgresql://restore/risk?hostaddr=192.0.2.1"),
            id="target-hostaddr",
        ),
        pytest.param(("PGSERVICE", "private"), id="environment-service"),
        pytest.param(("PGHOSTADDR", "192.0.2.1"), id="environment-hostaddr"),
        pytest.param(("RESTORE_DATABASE_URL", "postgresql:///risk"), id="implicit-socket"),
        pytest.param(("RESTORE_DATABASE_URL", "postgresql://restore"), id="implicit-database"),
    ],
)
def test_cli_refuses_unproved_postgres_routing(
    routing: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scalar, _exit_transaction, _dispose, args = _database_cli(tmp_path, monkeypatch)
    factory = Mock(side_effect=AssertionError("Unproved connection attempted"))
    monkeypatch.setattr(reconciler, "create_engine", factory)
    monkeypatch.setenv(*routing)

    assert cli(args) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"{FAILURE} Explicit, comparable PostgreSQL routing is required.\n"
    factory.assert_not_called()
    scalar.assert_not_called()


@pytest.mark.parametrize(
    "routing",
    [
        pytest.param(("postgresql:///risk", {"PGHOST": "primary"}), id="environment-host"),
        pytest.param(("postgresql://primary", {"PGDATABASE": "risk"}), id="environment-database"),
        pytest.param(("postgresql://primary/risk", {"PGPORT": "6543"}), id="environment-port"),
        pytest.param(
            ("postgresql:///risk?host=restore&host=primary", {"PGPORT": "6543"}),
            id="blank-port-uses-compiled-default",
        ),
    ],
)
def test_cli_protects_postgres_environment_defaults(
    routing: tuple[str, dict[str, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scalar, _exit_transaction, _dispose, args = _database_cli(tmp_path, monkeypatch)
    factory = Mock(side_effect=AssertionError("Protected connection attempted"))
    monkeypatch.setattr(reconciler, "create_engine", factory)
    target, environment = routing
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    primary_port = "6543" if environment == {"PGPORT": "6543"} and "?" not in target else "5432"
    monkeypatch.setenv("DATABASE_URL", f"postgresql://primary:{primary_port}/risk")
    monkeypatch.setenv("RESTORE_DATABASE_URL", target)

    assert cli(args) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "Refusing to operate on a protected production database." in output.err
    factory.assert_not_called()
    scalar.assert_not_called()


def test_cli_protects_same_host_with_changing_dns_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scalar, _exit_transaction, _dispose, args = _database_cli(tmp_path, monkeypatch)
    factory = Mock(side_effect=AssertionError("Protected connection attempted"))
    monkeypatch.setattr(reconciler, "create_engine", factory)
    resolver = Mock(
        side_effect=[
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 0))],
        ]
    )
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary/risk")
    monkeypatch.setenv("RESTORE_DATABASE_URL", "postgresql://primary/risk")

    assert cli(args) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "Refusing to operate on a protected production database." in output.err
    assert resolver.call_count == 2
    factory.assert_not_called()
    scalar.assert_not_called()
