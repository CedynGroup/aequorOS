from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import io
from collections.abc import Callable, Iterator, Sequence
from copy import deepcopy
from typing import cast

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from fastapi import HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data_engine.service.ingestion import bank_slug
from app.models import (
    Bank,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatorySubmissionEvent,
    User,
)
from app.reporting.service import workflow
from app.reporting.service.channels import FiledArtifact, OrassSandboxChannel
from app.reporting.service.exports import export_package
from app.reporting.service.filing_retention import retain_filing
from app.storage.client import StorageLocation
from app.storage.config import StorageEngineSettings, get_storage_settings
from app.storage.encryption import ObjectEncryption
from app.storage.provisioning import ProvisioningClient
from app.storage.s3_compatible import S3CompatibleStorageClient
from scripts import retain_filed_objects
from tests.attestation.service.test_attestation_artifact_signing import (
    CHECKER,
    MAKER,
    VALIDATOR,
    _approver_sends_on,  # pyright: ignore[reportPrivateUsage]
    _fully_certify,  # pyright: ignore[reportPrivateUsage]
    _seed,  # pyright: ignore[reportPrivateUsage]
    _validator_releases,  # pyright: ignore[reportPrivateUsage]
    _version,  # pyright: ignore[reportPrivateUsage]
)
from tests.attestation.service.test_attestation_artifact_signing import (
    signing_env as signing_env,  # noqa: PLC0414
)
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, SAMPLE_BANK_ID
from tests.storage.contract import metadata_for
from tests.storage.test_object_lock import (
    LockClient,
    _moto_retention,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.helpers import headers


@pytest.fixture
def storage(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[S3CompatibleStorageClient, LockClient]]:
    monkeypatch.setenv("STORAGE_ENV", "dev")
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "false")
    get_storage_settings.cache_clear()
    with mock_aws():
        factory = cast(Callable[..., BaseClient], boto3.client)
        s3 = factory("s3", region_name="us-east-1")
        settings = StorageEngineSettings.model_validate(
            {
                "STORAGE_BACKEND": "s3",
                "STORAGE_ENV": "dev",
                "S3_ENDPOINT": None,
                "STORAGE_OBJECT_LOCK_ENABLED": False,
            }
        )

        def optional_key(_self: ObjectEncryption, _location: StorageLocation) -> bool:
            return False

        monkeypatch.setattr(ObjectEncryption, "bank_key_required", optional_key)
        store = S3CompatibleStorageClient(settings, client_factory=lambda *_args, **_kwargs: s3)
        client = cast(LockClient, cast(object, s3))
        _moto_retention(client, monkeypatch)
        for module in (
            "app.reporting.service.exports",
            "app.attestation.service.artifact_signing",
            "app.reporting.service.filing_retention",
            "scripts.retain_filed_objects",
        ):
            monkeypatch.setattr(f"{module}.get_storage_client", lambda: store)
        yield store, client
    get_storage_settings.cache_clear()


def _lock_capability(db: Session, storage: S3CompatibleStorageClient, client: LockClient) -> str:
    bank = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    slug = bank_slug(db, bank)
    bucket = StorageLocation(slug, "outputs", "").bucket_name("dev")
    _ = cast(ProvisioningClient, client).create_bucket(
        Bucket=bucket, ObjectLockEnabledForBucket=True
    )
    storage.ensure_institution(slug)
    return slug


@pytest.mark.parametrize("interface", ["api", "service"])
@pytest.mark.parametrize("missing_kind", ["xlsx", "csv"])
@pytest.mark.parametrize("policy", ["absent", "COMPLIANCE", "GOVERNANCE", "valid"])
def test_submission_preparation_storage_refusals_are_atomic_conflicts(  # noqa: PLR0913, PLR0915
    db_session: Session,
    db_client: TestClient,
    storage: tuple[S3CompatibleStorageClient, LockClient],
    monkeypatch: pytest.MonkeyPatch,
    interface: str,
    missing_kind: str,
    policy: str,
) -> None:
    store, client = storage
    package = _seed(db_session)
    if policy == "absent":
        bank = db_session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        slug = bank_slug(db_session, bank)
        store.ensure_institution(slug)
    else:
        slug = _lock_capability(db_session, store, client)
    _preparer, approver = _fully_certify(db_session, package)
    signed = _version(db_session, approver)
    if missing_kind == "csv":
        export_package(db_session, MAKER, package, "xlsx")
    _approver_sends_on(db_session, package)
    _validator_releases(db_session, package)
    snapshot = deepcopy(package.snapshot)
    seal = package.snapshot_sha256
    artifact_rows = list(db_session.scalars(select(RegulatoryPackageArtifact)))
    artifacts_before = {
        (row.id, row.kind, row.object_path, row.checksum_sha256) for row in artifact_rows
    }
    versions_before = set(db_session.scalars(select(RegulatoryArtifactVersion.id)))
    assert missing_kind not in {row.kind for row in artifact_rows}
    location = StorageLocation(slug, "outputs", signed.object_path)
    _, stream = store.read(location, signed.storage_version_id)
    signed_before = stream.read()
    stream.close()
    bucket = location.bucket_name("dev")
    if policy in {"COMPLIANCE", "GOVERNANCE"}:

        def default_configuration(**_kwargs: str) -> dict[str, object]:
            return {
                "ObjectLockConfiguration": {
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": policy, "Days": 2555}},
                }
            }

        monkeypatch.setattr(client, "get_object_lock_configuration", default_configuration)
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "true")
    get_storage_settings.cache_clear()
    settings = store._settings.model_copy(update={"object_lock_enabled": True})  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(store, "_settings", settings)
    delivered: list[list[str]] = []
    original_submit = OrassSandboxChannel.submit

    def submit(
        channel: OrassSandboxChannel,
        submitted: RegulatoryPackage,
        artifacts: Sequence[FiledArtifact],
    ) -> str:
        delivered.append([artifact.kind for artifact in artifacts])
        return original_submit(channel, submitted, artifacts)

    monkeypatch.setattr(OrassSandboxChannel, "submit", submit)
    if interface == "api":
        actor = db_session.get(User, VALIDATOR.actor_user_id)
        assert actor is not None
        response = db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package.id}/submit",
            json={"channel": "orass_sandbox"},
            headers=headers(user_id=actor.id, authorization_version=actor.authorization_version),
        )
        if policy == "valid":
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 409, response.text
            assert (
                response.json()["error"]["details"]["error_code"] == "filing_retention_unconfirmed"
            )
    elif policy == "valid":
        workflow.submit_package_via_channel(
            db_session, VALIDATOR, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
        )
    else:
        with pytest.raises(HTTPException) as raised:
            workflow.submit_package_via_channel(
                db_session, VALIDATOR, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
            )
        assert raised.value.status_code == 409
        detail = TypeAdapter(dict[str, object]).validate_python(raised.value.detail)
        assert detail["error_code"] == "filing_retention_unconfirmed"
    db_session.refresh(package)
    assert package.snapshot == snapshot
    assert package.snapshot_sha256 == seal
    _, stream = store.read(location, signed.storage_version_id)
    assert stream.read() == signed_before
    stream.close()
    events = list(db_session.scalars(select(RegulatorySubmissionEvent)))
    if policy == "valid":
        assert package.status == "submitted"
        assert len(delivered) == 1
        assert missing_kind in delivered[0]
        assert len(events) == 1
        manifest = retain_filed_objects.FilingManifest.model_validate(events[0].detail)
        assert manifest.filed_artifacts
        for artifact in manifest.filed_artifacts:
            assert artifact.storage_version_id is not None
            retention = client.get_object_retention(
                Bucket=bucket, Key=artifact.object_path, VersionId=artifact.storage_version_id
            )
            assert cast(dict[str, object], retention["Retention"])["Mode"] == "COMPLIANCE"
    else:
        assert package.status == "approved"
        assert delivered == []
        assert events == []
        assert {
            (row.id, row.kind, row.object_path, row.checksum_sha256)
            for row in db_session.scalars(select(RegulatoryPackageArtifact))
        } == artifacts_before
        assert set(db_session.scalars(select(RegulatoryArtifactVersion.id))) == versions_before


@pytest.mark.parametrize("channel", ["manual", "orass_sandbox"])
@pytest.mark.parametrize("outcome", ["success", "denied", "unconfirmed", "missing-version"])
def test_sign_before_rollout_then_file_exact_versions(
    db_session: Session,
    storage: tuple[S3CompatibleStorageClient, LockClient],
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    outcome: str,
) -> None:
    store, client = storage
    package = _seed(db_session)
    slug = _lock_capability(db_session, store, client)
    _preparer, approver = _fully_certify(db_session, package)
    signed = _version(db_session, approver)
    assert signed.storage_version_id is not None
    location = StorageLocation(slug, "outputs", signed.object_path)
    kwargs = {
        "Bucket": location.bucket_name("dev"),
        "Key": location.object_path,
        "VersionId": signed.storage_version_id,
    }
    before = client.get_object_retention(**kwargs)
    assert cast(dict[str, object], before["Retention"])["Mode"] is None
    _approver_sends_on(db_session, package)
    _validator_releases(db_session, package)
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "true")
    get_storage_settings.cache_clear()
    if outcome == "denied":

        def deny(**_kwargs: object) -> object:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObjectRetention")

        monkeypatch.setattr(client, "put_object_retention", deny)
    elif outcome == "unconfirmed":

        def unconfirmed(**_kwargs: object) -> dict[str, object]:
            return {}

        monkeypatch.setattr(client, "put_object_retention", unconfirmed)
    elif outcome == "missing-version":
        signed.storage_version_id = None
        db_session.flush()
    if outcome != "success":
        with pytest.raises(HTTPException) as raised:
            workflow.submit_package_via_channel(
                db_session,
                CHECKER,
                SAMPLE_BANK_ID,
                package.id,
                channel_override=channel,
                external_ref="synthetic-receipt",
            )
        assert raised.value.status_code == 409
        detail = TypeAdapter(dict[str, object]).validate_python(raised.value.detail)
        assert detail["error_code"] == "filing_retention_unconfirmed"
        assert package.status == "approved"
        assert db_session.scalar(select(func.count()).select_from(RegulatorySubmissionEvent)) == 0
        return
    workflow.submit_package_via_channel(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        channel_override=channel,
        external_ref="synthetic-receipt",
    )
    assert package.status == "submitted"
    retained = cast(dict[str, object], client.get_object_retention(**kwargs)["Retention"])
    assert retained["Mode"] == "COMPLIANCE"
    with pytest.raises(ClientError):
        client.delete_object(**kwargs)
    event = db_session.scalar(select(RegulatorySubmissionEvent))
    assert event is not None
    manifest = retain_filed_objects.FilingManifest.model_validate(event.detail)
    assert manifest.filed_artifacts
    for record in manifest.filed_artifacts:
        assert record.storage_version_id is not None
        locked = client.get_object_retention(
            Bucket=location.bucket_name("dev"),
            Key=record.object_path,
            VersionId=record.storage_version_id,
        )
        assert cast(dict[str, object], locked["Retention"])["Mode"] == "COMPLIANCE"


def test_retention_refuses_to_assign_a_missing_storage_location(
    db_session: Session,
    storage: tuple[S3CompatibleStorageClient, LockClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client = storage
    package = _seed(db_session)
    _lock_capability(db_session, store, client)
    _preparer, approver = _fully_certify(db_session, package)
    signed = _version(db_session, approver)
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    bank.storage_slug = None
    db_session.flush()
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "true")
    get_storage_settings.cache_clear()

    with pytest.raises(HTTPException) as raised:
        retain_filing(db_session, MAKER, package, [signed])

    assert raised.value.status_code == 409
    detail = TypeAdapter(dict[str, object]).validate_python(raised.value.detail)
    assert detail["error_code"] == "filing_retention_unconfirmed"
    db_session.refresh(bank)
    assert bank.storage_slug is None


@pytest.mark.parametrize("return_code", ["BSD2", "SDI-LMT-MONTHLY", "ICAAP-REPORT"])
def test_filing_classification_controls_working_copy_retention(
    db_session: Session,
    storage: tuple[S3CompatibleStorageClient, LockClient],
    monkeypatch: pytest.MonkeyPatch,
    return_code: str,
) -> None:
    store, client = storage
    package = _seed(db_session)
    slug = _lock_capability(db_session, store, client)
    package.return_code = return_code
    settings = store._settings.model_copy(update={"object_lock_enabled": True})  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(store, "_settings", settings)
    for kind in ("xlsx_working", "docx_working", "pdf"):
        location = StorageLocation(slug, "outputs", f"synthetic/{kind}")
        content = kind.encode()
        stored = store.write(location, io.BytesIO(content), metadata_for(slug, "outputs", content))
        db_session.add(
            RegulatoryPackageArtifact(
                organization_id=DEMO_ORG_ID,
                package_id=package.id,
                kind=kind,
                object_path=location.object_path,
                checksum_sha256=stored.metadata.checksum_sha256,
                size_bytes=len(content),
            )
        )
        db_session.add(
            RegulatoryArtifactVersion(
                organization_id=DEMO_ORG_ID,
                package_id=package.id,
                kind=kind,
                object_path=location.object_path,
                checksum_sha256=stored.metadata.checksum_sha256,
                storage_version_id=stored.version_id,
                size_bytes=len(content),
            )
        )
    db_session.flush()
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "true")
    get_storage_settings.cache_clear()
    filed, _detail = workflow._filing_set(db_session, MAKER, package, mint_missing=False)  # pyright: ignore[reportPrivateUsage]
    retain_filing(db_session, MAKER, package, filed)
    for version in db_session.scalars(select(RegulatoryArtifactVersion)):
        assert version.storage_version_id is not None
        mode = cast(
            dict[str, object],
            client.get_object_retention(
                Bucket=StorageLocation(slug, "outputs", "").bucket_name("dev"),
                Key=version.object_path,
                VersionId=version.storage_version_id,
            )["Retention"],
        )["Mode"]
        expected = version.kind == "pdf" or (
            return_code == "BSD2" and version.kind == "xlsx_working"
        )
        assert (mode == "COMPLIANCE") == expected


def test_historical_command_is_dry_run_until_explicit_apply(
    db_session: Session,
    storage: tuple[S3CompatibleStorageClient, LockClient],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, client = storage
    package = _seed(db_session)
    slug = _lock_capability(db_session, store, client)
    _preparer, approver = _fully_certify(db_session, package)
    _approver_sends_on(db_session, package)
    _validator_releases(db_session, package)
    workflow.submit_package_via_channel(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        channel_override="manual",
        external_ref="historical-receipt",
    )
    signed = _version(db_session, approver)
    assert signed.storage_version_id is not None
    kwargs = {
        "Bucket": StorageLocation(slug, "outputs", "").bucket_name("dev"),
        "Key": signed.object_path,
        "VersionId": signed.storage_version_id,
    }
    monkeypatch.setattr(
        retain_filed_objects.database, "get_sessionmaker", lambda: lambda: db_session
    )
    args = ["--organization-id", DEMO_ORG_ID, "--bank-id", SAMPLE_BANK_ID]
    assert retain_filed_objects.main(args) == 0
    inventory = [
        TypeAdapter(dict[str, str]).validate_json(line)
        for line in capsys.readouterr().out.splitlines()
    ]
    assert inventory
    assert all(record["action"] == "dry-run" for record in inventory)
    assert (
        cast(dict[str, object], client.get_object_retention(**kwargs)["Retention"])["Mode"] is None
    )
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "true")
    get_storage_settings.cache_clear()
    assert retain_filed_objects.main([*args, "--apply"]) == 0
    assert (
        cast(dict[str, object], client.get_object_retention(**kwargs)["Retention"])["Mode"]
        == "COMPLIANCE"
    )
