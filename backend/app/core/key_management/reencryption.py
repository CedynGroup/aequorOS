"""Resumable editable-data migration; immutable evidence remains explicitly retained."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management import maintenance, registry
from app.core.key_management.fields import ProtectedField, SealedField
from app.core.key_management.types import KeyUnavailableError
from app.db.base import Base
from app.models.job import Job
from app.services import job_queue
from app.storage.client import TIERS, EncryptionInventoryItem, StorageClient
from app.storage.factory import get_storage_client

JOB_TYPE = "bank_reencrypt"
BATCH_SIZE = 100


class Progress(BaseModel):
    target_version: UUID | None = None
    table: int = 0
    after: UUID | None = None
    fields_rewritten: int = 0
    objects_rewritten: int = 0
    retained: dict[str, int] = Field(default_factory=dict)
    summary: str = "queued"
    indexed_version: UUID | None = None


def enqueue(db: Session, organization: str, bank: str) -> Job:
    _ = registry.scoped_key(db, organization_id=organization, bank_id=bank)
    active = db.scalar(
        select(Job)
        .where(
            Job.organization_id == organization,
            Job.bank_id == bank,
            Job.job_type == JOB_TYPE,
            Job.status.in_(("queued", "running")),
        )
        .order_by(Job.queued_at)
        .limit(1)
    )
    if active is not None:
        return active
    return job_queue.enqueue(
        db,
        organization,
        JOB_TYPE,
        bank_id=bank,
        entity_type="bank",
        entity_id=bank,
        coalesce_key=f"bank-reencrypt:{bank}",
    )


def _save(db: Session, job: Job, progress: Progress) -> None:
    job.progress = progress.model_dump(mode="json")
    db.commit()


def _pinned_versions(
    db: Session, organization: str, bank: str, slug: str
) -> set[tuple[str, str, str]]:
    # Object version IDs (especially "null") are not globally unique. Preserve
    # the scoped location as well as the version when matching evidence pins.
    result: set[tuple[str, str, str]] = set()
    for table in Base.metadata.tables.values():
        for col in table.c:
            if col.name not in {"storage_version_id", "letter_storage_version_id", "version_id"}:
                continue
            if "organization_id" not in table.c:
                continue
            predicates = [table.c.organization_id == organization, col.is_not(None)]
            if "bank_id" in table.c:
                predicates.append(table.c.bank_id == bank)
            for row in db.execute(select(table).where(*predicates)).mappings():
                version = cast(object, row[col.name])
                if not isinstance(version, str):
                    continue
                if col.name == "version_id":
                    bucket = str(cast(object, row["bucket"]))
                    tiers = [tier for tier in TIERS if bucket.endswith(f"-{slug}-{tier}")]
                    if not tiers:
                        continue
                    tier, path = tiers[0], str(cast(object, row["object_key"]))
                else:
                    prefix = "letter_" if col.name.startswith("letter_") else ""
                    tier = (
                        str(cast(object, row[prefix + "storage_tier"]))
                        if prefix + "storage_tier" in table.c
                        else "outputs"
                    )
                    path = str(cast(object, row[prefix + "object_path"]))
                result.add((tier, path, version))
    return result


def _retained(item: EncryptionInventoryItem, pins: set[tuple[str, str, str]]) -> bool:
    # A versioned current object can gain a new encrypted current version while
    # the pinned original remains intact. An unversioned PUT would overwrite a
    # pinned "null" version, so that location must remain untouched.
    return item.retained or (
        item.version_id == "null"
        and (item.location.tier, item.location.object_path, item.version_id) in pins
    )


def run(db: Session, job: Job, storage: StorageClient | None = None) -> None:  # noqa: PLR0912, PLR0915 - resumable maintenance state machine
    bank = job.bank_id
    if bank is None:
        raise KeyUnavailableError("Re-encryption requires a bank.")
    organization = job.organization_id
    progress = Progress.model_validate(TypeAdapter(dict[str, object]).validate_python(job.progress))
    tables = maintenance.protected_tables()
    while True:
        if progress.indexed_version != progress.target_version or progress.target_version is None:
            db.commit()
            maintenance.lock_bank(db, organization, bank, exclusive=True)
            key = registry.scoped_key(db, organization_id=organization, bank_id=bank)
            _ = registry.current_version(db, key)
            key.index_version_id = key.current_version_id
            db.flush()
            maintenance.reindex_bank(db, key)
            progress.indexed_version = key.current_version_id
            progress.target_version = key.current_version_id
            progress.table, progress.after = 0, None
            progress.summary = "re-encrypting editable data"
            _save(db, job, progress)
        key = registry.bank_key(db, organization, bank)
        if key is None:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        if progress.target_version != key.current_version_id:
            progress.target_version = key.current_version_id
            progress.table, progress.after = 0, None
            progress.summary = "re-encrypting editable data"
            _save(db, job, progress)
            continue
        if progress.table < len(tables):
            table = tables[progress.table]
            if all(
                c.name in maintenance.IMMUTABLE_FIELDS.get(table.name, set())
                for c in table.c
                if isinstance(c.type, ProtectedField)
            ):
                progress.table += 1
                progress.after = None
                _save(db, job, progress)
                continue
            statement = select(table).where(*maintenance.scope(table, organization, bank))
            if progress.after is not None:
                statement = statement.where(table.c.id > progress.after)
            rows = (
                db.execute(statement.order_by(table.c.id).limit(BATCH_SIZE).with_for_update())
                .mappings()
                .all()
            )
            for row in rows:
                progress.fields_rewritten += maintenance.rewrite_row(
                    db, key, table, cast(Mapping[str, object], row)
                )
                progress.after = cast(UUID, row["id"])
            if len(rows) < BATCH_SIZE:
                progress.table += 1
                progress.after = None
            _save(db, job, progress)
            continue

        maintenance.rewrite_bi_identities(db, key)
        _save(db, job, progress)
        key = registry.bank_key(db, organization, bank)
        if key is None:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        if key.current_version_id != progress.target_version:
            continue
        client = (storage or get_storage_client()).for_session(db)
        pinned = _pinned_versions(db, organization, bank, key.storage_slug)
        moved = False
        for item in client.encryption_inventory(key.storage_slug):
            key = registry.bank_key(db, organization, bank)
            if key is None:
                raise KeyUnavailableError("The bank encryption key is not connected.")
            if key.current_version_id != progress.target_version:
                moved = True
                break
            if item.envelope_id == key.current_version_id or _retained(item, pinned):
                continue
            if client.reencrypt(item):
                progress.objects_rewritten += 1
            moved = True
            _save(db, job, progress)
        if moved:
            continue

        # No earlier admitted field/object writer can publish an old version
        # after this barrier. Rotation bookkeeping is locked only at finalization.
        db.commit()
        maintenance.lock_bank(db, organization, bank, exclusive=True)
        key = registry.scoped_key(db, organization_id=organization, bank_id=bank)
        if key.current_version_id != progress.target_version:
            db.commit()
            continue
        pending = next(maintenance.bi_identity_updates(db, key), None) is not None
        for table in tables:
            for row in db.execute(
                select(table).where(*maintenance.scope(table, organization, bank))
            ).mappings():
                for col in table.c:
                    if not isinstance(
                        col.type, ProtectedField
                    ) or col.name in maintenance.IMMUTABLE_FIELDS.get(table.name, set()):
                        continue
                    value = cast(object, row[col.name])
                    if value is not None and (
                        not isinstance(value, SealedField)
                        or value.envelope_id != key.current_version_id
                    ):
                        pending = True
        retained = maintenance.retained_fields(db, key)
        pinned = _pinned_versions(db, organization, bank, key.storage_slug)
        for item in client.encryption_inventory(key.storage_slug):
            if item.envelope_id == key.current_version_id:
                continue
            if _retained(item, pinned):
                category = "stored_object_versions:" + item.location.tier
                retained[category] = retained.get(category, 0) + 1
            else:
                pending = True
        if pending:
            progress.table, progress.after = 0, None
            _save(db, job, progress)
            continue
        progress.retained = retained
        count = sum(retained.values())
        progress.summary = (
            f"complete except {count} retained immutable records" if count else "complete"
        )
        _save(db, job, progress)
        return
