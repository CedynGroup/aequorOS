from __future__ import annotations

from pathlib import Path

import pytest

from scripts.backup_storage import RestoredVersion, RestoreReport
from scripts.dr_common import DisasterRecoveryError
from scripts.reconcile_storage_versions import load_mapping, main


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


@pytest.mark.parametrize(
    "failure", ["missing-id", "blank-id", "duplicate", "collapsed", "count", "checksum"]
)
def test_loader_refuses_unusable_or_ambiguous_mapping(tmp_path: Path, failure: str) -> None:
    report = _report()
    if failure == "missing-id":
        report.versions[0].restored_version_id = ""
    elif failure == "blank-id":
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
    else:
        report.versions[0].sha256 = "z" * 64
    path = tmp_path / "restore.json"
    report.write(path)
    with pytest.raises(DisasterRecoveryError):
        load_mapping(path)


def test_loader_refuses_truncated_report(tmp_path: Path) -> None:
    path = tmp_path / "restore.json"
    path.write_text('{"versions": [')
    with pytest.raises(ValueError):
        load_mapping(path)


def test_cli_refuses_primary_before_opening_a_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app:synthetic@primary.example/risk")
    monkeypatch.setenv(
        "RESTORE_DATABASE_URL", "postgresql+psycopg://recovery:synthetic@primary.example/risk"
    )
    with pytest.raises(DisasterRecoveryError, match="protected"):
        main(["--mapping", "absent.json", "--storage-env", "dev", "--reason", "synthetic"])


def test_cli_refuses_non_postgres_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "restore.json"
    _report().write(path)
    monkeypatch.setenv("RESTORE_DATABASE_URL", "sqlite://")
    with pytest.raises(DisasterRecoveryError, match="PostgreSQL"):
        main(["--mapping", str(path), "--storage-env", "dev", "--reason", "synthetic"])


def test_loader_refuses_duplicate_json_fields(tmp_path: Path) -> None:
    path = tmp_path / "restore.json"
    path.write_text('{"versions": [], "versions": []}')
    with pytest.raises(DisasterRecoveryError, match="Ambiguous"):
        load_mapping(path)
