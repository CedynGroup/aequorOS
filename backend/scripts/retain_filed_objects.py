"""Inventory historical filed versions; apply compliance retention only with --apply."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import session as database
from app.models import (
    Bank,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatorySubmissionEvent,
)
from app.reporting.service.filing_retention import archived_version
from app.reporting.service.registry import get_definition
from app.reporting.service.workflow import filing_admits_artifact
from app.storage.client import StorageError, StorageLocation, StorageValidationError
from app.storage.config import get_storage_settings
from app.storage.factory import get_storage_client
from app.storage.retention import retention_until


class FiledRecord(BaseModel):
    kind: str
    object_path: str
    checksum_sha256: str
    storage_version_id: str | None = None


class FilingManifest(BaseModel):
    filed_artifacts: list[FiledRecord]


@dataclass(frozen=True)
class FiledVersion:
    location: StorageLocation
    version_id: str


def historical_versions(db: Session, organization_id: str, bank_id: str) -> list[FiledVersion]:
    bank = db.scalar(
        select(Bank).where(Bank.organization_id == organization_id, Bank.id == bank_id)
    )
    if bank is None or bank.storage_slug is None:
        raise StorageValidationError("The bank has no existing storage location.")
    rows = db.execute(
        select(RegulatoryPackage, RegulatorySubmissionEvent)
        .join(
            RegulatorySubmissionEvent,
            (RegulatorySubmissionEvent.package_id == RegulatoryPackage.id)
            & (RegulatorySubmissionEvent.organization_id == RegulatoryPackage.organization_id),
        )
        .where(
            RegulatoryPackage.organization_id == organization_id,
            RegulatoryPackage.bank_id == bank_id,
            RegulatorySubmissionEvent.event == "submitted",
        )
        .order_by(RegulatorySubmissionEvent.created_at, RegulatorySubmissionEvent.id)
    ).tuples()
    found: dict[tuple[str, str], FiledVersion] = {}
    for package, event in rows:
        manifest = TypeAdapter(FilingManifest).validate_python(event.detail)
        definition = get_definition(package.return_code)
        generator = definition.generator if definition else None
        for record in manifest.filed_artifacts:
            if not filing_admits_artifact(record.kind, generator=generator):
                raise StorageValidationError("A historical filing contains an internal artifact.")
            artifact = RegulatoryPackageArtifact(
                kind=record.kind,
                object_path=record.object_path,
                checksum_sha256=record.checksum_sha256,
            )
            if record.storage_version_id is not None:
                version = db.scalar(
                    select(RegulatoryArtifactVersion).where(
                        RegulatoryArtifactVersion.organization_id == organization_id,
                        RegulatoryArtifactVersion.package_id == package.id,
                        RegulatoryArtifactVersion.kind == record.kind,
                        RegulatoryArtifactVersion.object_path == record.object_path,
                        RegulatoryArtifactVersion.checksum_sha256 == record.checksum_sha256,
                        RegulatoryArtifactVersion.storage_version_id == record.storage_version_id,
                    )
                )
                if version is None:
                    raise StorageValidationError("The filing's pinned version is not archived.")
            else:
                version = archived_version(db, package, artifact, before=event.created_at)
            version_id = version.storage_version_id
            if not version_id or version_id == "null":
                raise StorageValidationError("A historical filing has no concrete object version.")
            location = StorageLocation(bank.storage_slug, "outputs", record.object_path)
            found[(record.object_path, version_id)] = FiledVersion(location, version_id)
    return list(found.values())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--bank-id", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Irreversibly apply COMPLIANCE retention."
    )
    return parser


class _Arguments(argparse.Namespace):
    organization_id: str = ""
    bank_id: str = ""
    apply: bool = False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv, namespace=_Arguments())
    settings = get_storage_settings()
    if args.apply and not settings.object_lock_enabled:
        raise StorageValidationError("--apply requires STORAGE_OBJECT_LOCK_ENABLED=true.")
    sessions = cast(Callable[[], Callable[[], Session]], database.get_sessionmaker)()
    with sessions() as db:
        db.info["organization_id"] = args.organization_id
        if db.get_bind().dialect.name == "postgresql":
            _ = db.execute(text("SET TRANSACTION READ ONLY"))
        versions = historical_versions(db, args.organization_id, args.bank_id)
    until = retention_until(settings)
    client = get_storage_client() if args.apply else None
    for version in versions:
        print(
            json.dumps(
                {
                    "bucket": version.location.bucket_name(settings.env),
                    "key": version.location.object_path,
                    "version_id": version.version_id,
                    "retain_until": until.isoformat(),
                    "action": "apply" if args.apply else "dry-run",
                }
            )
        )
        if client is not None:
            client.retain(version.location, version.version_id, until)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StorageError, ValidationError) as exc:
        print(f"Historical retention refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
