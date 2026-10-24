"""Bounded, resumable bank encryption maintenance through the existing queue."""

from __future__ import annotations

import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import ColumnElement, String, delete, or_, select, text
from sqlalchemy.orm import Session

from app.core.key_management import maintenance, registry
from app.core.key_management.fields import ProtectedField, SealedField
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.types import KeyUnavailableError
from app.db.base import Base
from app.models.job import Job
from app.services import job_queue
from app.storage.client import (
    EncryptionCursor,
    EncryptionInventoryItem,
    ReencryptionIsolation,
    StorageClient,
)
from app.storage.factory import get_storage_client

JOB_TYPE = "bank_reencrypt"
BATCH_SIZE = 100
OBJECT_BATCH_SIZE = 10
HOP_SECONDS = 30
LARGE_OBJECT_BYTES = 16 * 1024 * 1024


class Progress(BaseModel):
    target_version: UUID | None = None
    phase: Literal[
        "start",
        "fields",
        "bi",
        "objects",
        "large_object",
        "verify_fields",
        "verify_objects",
        "cleanup",
        "done",
    ] = "start"
    table: int = 0
    after: UUID | None = None
    object_cursor: EncryptionCursor = Field(default_factory=EncryptionCursor)
    large_object: EncryptionInventoryItem | None = None
    references: list[UUID] = Field(default_factory=list)
    fields_rewritten: int = 0
    objects_rewritten: int = 0
    retained: dict[str, int] = Field(default_factory=dict)
    summary: str = "queued"
    next_job_id: UUID | None = None


def _unfinished() -> tuple[ColumnElement[bool], ...]:
    phase = Job.progress["phase"].as_string()
    return (
        Job.completed_at.is_(None),
        Job.progress["next_job_id"].as_string().is_(None),
        or_(phase.is_(None), phase != "done"),
    )


def _owned_job(db: Session, job: Job) -> Job | None:
    return db.scalar(
        select(Job)
        .where(
            Job.id == job.id,
            Job.organization_id == job.organization_id,
            Job.bank_id == job.bank_id,
            *_unfinished(),
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


def enqueue(db: Session, organization: str, bank: str) -> Job:
    _ = registry.scoped_key(db, organization_id=organization, bank_id=bank)
    active = db.scalar(
        select(Job)
        .where(
            Job.organization_id == organization,
            Job.bank_id == bank,
            Job.job_type == JOB_TYPE,
            Job.status.in_(("queued", "running")),
            *_unfinished(),
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
    )


def _pinned(db: Session, key: BankEncryptionKey, item: EncryptionInventoryItem) -> bool:
    if item.retained:
        return True
    if item.version_id != "null":
        return False
    for table in Base.metadata.tables.values():
        if "organization_id" not in table.c:
            continue
        for column in table.c:
            if column.name not in {"storage_version_id", "letter_storage_version_id", "version_id"}:
                continue
            if not isinstance(column.type, String):
                continue
            predicates = [table.c.organization_id == key.organization_id, column == item.version_id]
            if "bank_id" in table.c:
                predicates.append(table.c.bank_id == key.bank_id)
            if column.name == "version_id":
                if "bucket" not in table.c or "object_key" not in table.c:
                    continue
                predicates.extend(
                    (
                        table.c.bucket.endswith(f"-{key.storage_slug}-{item.location.tier}"),
                        table.c.object_key == item.location.object_path,
                    )
                )
            else:
                prefix = "letter_" if column.name.startswith("letter_") else ""
                predicates.append(table.c[prefix + "object_path"] == item.location.object_path)
                if prefix + "storage_tier" in table.c:
                    predicates.append(table.c[prefix + "storage_tier"] == item.location.tier)
                elif item.location.tier != "outputs":
                    continue
            if db.execute(select(column).where(*predicates).limit(1)).first() is not None:
                return True
    return False


def _fields(db: Session, key: BankEncryptionKey, progress: Progress, deadline: float) -> None:
    tables = maintenance.protected_tables()
    if progress.table == len(tables):
        progress.table, progress.after = 0, None
        progress.phase = "verify_objects" if progress.phase == "verify_fields" else "bi"
        progress.object_cursor = EncryptionCursor()
        return
    table = tables[progress.table]
    statement = select(table).where(*maintenance.scope(table, key.organization_id, key.bank_id))
    if progress.after is not None:
        statement = statement.where(table.c.id > progress.after)
    statement = statement.order_by(table.c.id).limit(BATCH_SIZE)
    if any(
        isinstance(column.type, ProtectedField)
        and column.name not in maintenance.IMMUTABLE_FIELDS.get(table.name, set())
        for column in table.c
    ):
        statement = statement.with_for_update()
    rows = db.execute(statement).mappings().all()
    references = set(progress.references)
    processed = 0
    for result in rows:
        if processed and time.monotonic() >= deadline:
            break
        row = cast(Mapping[str, object], result)
        progress.fields_rewritten += maintenance.rewrite_row(db, key, table, row)
        if progress.phase == "verify_fields":
            retained = False
            for column in table.c:
                if not isinstance(column.type, ProtectedField):
                    continue
                value = row[column.name]
                if column.name in maintenance.IMMUTABLE_FIELDS.get(table.name, set()):
                    if isinstance(value, SealedField):
                        references.add(value.envelope_id)
                    retained |= value is not None and (
                        not isinstance(value, SealedField)
                        or value.envelope_id != progress.target_version
                    )
            if retained:
                progress.retained[table.name] = progress.retained.get(table.name, 0) + 1
        progress.after = cast(UUID, row["id"])
        processed += 1
    progress.references = sorted(references)
    if processed == len(rows) and len(rows) < BATCH_SIZE:
        progress.table += 1
        progress.after = None


def _objects(
    db: Session, key: BankEncryptionKey, progress: Progress, client: StorageClient, deadline: float
) -> None:
    for number in range(OBJECT_BATCH_SIZE):
        if number and time.monotonic() >= deadline:
            return
        page = client.encryption_page(key.storage_slug, progress.object_cursor, 1)
        references = set(progress.references)
        for item in page.items:
            retained = _pinned(db, key, item)
            if progress.phase == "verify_objects":
                if item.envelope_id is not None:
                    references.add(item.envelope_id)
                if item.envelope_id != key.current_version_id:
                    if not retained:
                        progress.phase = "objects"
                        progress.object_cursor = EncryptionCursor()
                        progress.references, progress.retained = [], {}
                        return
                    category = "stored_object_versions:" + item.location.tier
                    progress.retained[category] = progress.retained.get(category, 0) + 1
            elif item.envelope_id != key.current_version_id and not retained:
                if item.size_bytes >= LARGE_OBJECT_BYTES:
                    progress.phase, progress.large_object = "large_object", item
                    return
                if client.reencrypt(item):
                    progress.objects_rewritten += 1
        progress.references = sorted(references)
        if page.cursor is not None:
            progress.object_cursor = page.cursor
            continue
        progress.after, progress.table = None, 0
        if progress.phase == "objects":
            progress.phase = "verify_fields"
            progress.references = [
                version
                for version in (key.current_version_id, key.index_version_id)
                if version is not None
            ]
            progress.retained = {}
        else:
            progress.phase = "cleanup"
        return


def _large_object(
    db: Session, job: Job, key: BankEncryptionKey, progress: Progress, client: StorageClient
) -> bool:
    item = progress.large_object
    assert item is not None
    organization, bank = key.organization_id, key.bank_id

    @contextmanager
    def publication(version: UUID) -> Generator[bool]:
        if _owned_job(db, job) is None:
            yield False
            return
        maintenance.lock_bank(db, organization, bank, exclusive=True)
        live = registry.scoped_key(db, organization_id=organization, bank_id=bank)
        if (
            version != progress.target_version
            or live.current_version_id != version
            or _pinned(db, live, item)
        ):
            yield False
            return
        _ = registry.open_envelope(
            db,
            live,
            version,
            registry.version_context(organization, bank),
            registry.provider_for,
        )
        yield True

    moved = client.reencrypt_isolated(item, ReencryptionIsolation(db.commit, publication))
    if _owned_job(db, job) is None:
        return False
    if moved:
        progress.objects_rewritten += 1
    progress.phase, progress.large_object = "objects", None
    return True


def _cleanup(db: Session, key: BankEncryptionKey, progress: Progress, deadline: float) -> None:
    statement = select(ObjectKeyEnvelope.id).where(
        ObjectKeyEnvelope.organization_id == key.organization_id,
        ObjectKeyEnvelope.bank_id == key.bank_id,
    )
    if progress.after is not None:
        statement = statement.where(ObjectKeyEnvelope.id > progress.after)
    rows = db.scalars(
        statement.order_by(ObjectKeyEnvelope.id).limit(BATCH_SIZE).with_for_update()
    ).all()
    references = set(progress.references)
    processed = 0
    for envelope_id in rows:
        if processed and time.monotonic() >= deadline:
            break
        if envelope_id not in references:
            _ = db.execute(
                delete(ObjectKeyEnvelope).where(
                    ObjectKeyEnvelope.id == envelope_id,
                    ObjectKeyEnvelope.organization_id == key.organization_id,
                    ObjectKeyEnvelope.bank_id == key.bank_id,
                )
            )
        progress.after = envelope_id
        processed += 1
    if processed == len(rows) and len(rows) < BATCH_SIZE:
        progress.retained.update(maintenance.retained_audits(db, key))
        count = sum(progress.retained.values())
        progress.summary = (
            f"complete except {count} retained immutable records" if count else "complete"
        )
        progress.phase = "done"


def run(db: Session, job: Job, storage: StorageClient | None = None) -> None:
    deadline = time.monotonic() + HOP_SECONDS
    bank = job.bank_id
    if bank is None:
        raise KeyUnavailableError("Re-encryption requires a bank.")
    organization = job.organization_id
    current = _owned_job(db, job)
    if current is None:
        return
    progress = Progress.model_validate(
        TypeAdapter(dict[str, object]).validate_python(current.progress)
    )
    if db.get_bind().dialect.name == "postgresql":
        _ = db.execute(text("SET LOCAL statement_timeout = '60s'"))
    maintenance.lock_bank(db, organization, bank, exclusive=True)
    key = registry.scoped_key(db, organization_id=organization, bank_id=bank)
    if progress.target_version != key.current_version_id:
        progress = Progress(
            target_version=key.current_version_id,
            fields_rewritten=progress.fields_rewritten,
            objects_rewritten=progress.objects_rewritten,
        )
    if progress.phase == "start":
        maintenance.move_index_key(db, key)
        progress.phase = "fields"
        progress.summary = "re-encrypting editable data"
    elif progress.phase in {"fields", "verify_fields"}:
        _fields(db, key, progress, deadline)
    elif progress.phase == "bi":
        changes = list(maintenance.bi_identity_updates(db, key, limit=BATCH_SIZE))
        for index, (table, predicates, values) in enumerate(changes):
            if index and time.monotonic() >= deadline:
                break
            _ = db.execute(table.update().where(*predicates).values(**values))
        if not changes:
            progress.phase = "objects"
            progress.object_cursor = EncryptionCursor()
    elif progress.phase in {"objects", "verify_objects"}:
        client = (storage or get_storage_client()).for_session(db)
        _objects(db, key, progress, client, deadline)
    elif progress.phase == "large_object":
        client = (storage or get_storage_client()).for_session(db)
        if not _large_object(db, current, key, progress, client):
            return
    else:
        if next(maintenance.bi_identity_updates(db, key, limit=1), None) is not None:
            progress.phase = "bi"
            progress.after, progress.table = None, 0
        else:
            _cleanup(db, key, progress, deadline)
    if progress.phase != "done":
        successor = job_queue.enqueue(
            db,
            organization,
            JOB_TYPE,
            bank_id=bank,
            entity_type="bank",
            entity_id=bank,
            payload={"reencryption_bytes": progress.large_object.size_bytes}
            if progress.large_object
            else {},
        )
        successor.progress = progress.model_dump(mode="json")
        progress.next_job_id = successor.id
    current.progress = progress.model_dump(mode="json")
    db.commit()
