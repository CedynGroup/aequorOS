"""Audit log views for the supervisor API: list, entry detail, verify and signed export."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import (
    GENESIS_HASH,
    AuditEntry,
    compute_entry_hash,
    entry_body,
    signature_valid,
)
from app.core.clock import iso
from app.core.context import AppContext
from app.core.encoding import sha256_hex
from app.core.errors import ApiError
from app.core.jcs import canonicalize
from app.db.models import AuditVerification
from app.keys.jws import sign_compact
from app.keys.provider import jwks_document

RETENTION_LABEL = "[Retention period set by BoG]"
ActorType = Literal["staff", "institution", "system", "operator"]


class Integrity(BaseModel):
    intact: bool
    entries: int
    verified_at: str
    head_hash: str


class Siem(BaseModel):
    streaming: bool
    lag_seconds: int | None


class AuditRow(BaseModel):
    seq: int
    occurred_at: str
    actor_label: str
    actor_via: str
    category: str
    action: str
    object_ref: str


class AuditList(BaseModel):
    integrity: Integrity
    siem: Siem
    retention_label: str
    items: list[AuditRow]
    next_cursor: str | None


class AuditEntryOut(BaseModel):
    seq: int
    occurred_at: str
    actor_label: str
    actor_via: str
    category: str
    action: str
    object_ref: str
    detail: dict[str, JsonValue]
    prev_hash: str
    entry_hash: str
    signature_kid: str
    signature_valid: bool


class VerifyOut(BaseModel):
    intact: bool
    entries: int
    verified_at: str
    first_bad_seq: int | None


def integrity(verification: AuditVerification) -> Integrity:
    return Integrity(
        intact=verification.intact,
        entries=verification.entries,
        verified_at=iso(verification.verified_at),
        head_hash=verification.head_hash,
    )


def audit_list(
    session: Session,
    verification: AuditVerification,
    *,
    actor_type: str | None,
    category: str | None,
    institution_code: str | None,
    from_: date | None,
    to: date | None,
    cursor: str | None,
    limit: int,
) -> AuditList:
    statement = select(AuditEntry)
    if actor_type:
        statement = statement.where(AuditEntry.actor_type == actor_type)
    if category:
        statement = statement.where(AuditEntry.category == category)
    if institution_code:
        statement = statement.where(AuditEntry.institution_code == institution_code)
    if from_:
        statement = statement.where(
            AuditEntry.occurred_at
            >= datetime.combine(from_, time.min).replace(tzinfo=verification.verified_at.tzinfo)
        )
    if to:
        statement = statement.where(
            AuditEntry.occurred_at
            < datetime.combine(to + timedelta(days=1), time.min).replace(
                tzinfo=verification.verified_at.tzinfo
            )
        )
    if cursor:
        if not cursor.isdigit():
            raise ApiError("invalid_request", "The cursor is not valid.")
        statement = statement.where(AuditEntry.seq < int(cursor))
    rows = list(session.scalars(statement.order_by(AuditEntry.seq.desc()).limit(limit + 1)))
    page = rows[:limit]
    return AuditList(
        integrity=integrity(verification),
        siem=Siem(streaming=False, lag_seconds=None),
        retention_label=RETENTION_LABEL,
        items=[
            AuditRow(
                seq=e.seq,
                occurred_at=iso(e.occurred_at),
                actor_label=e.actor_label,
                actor_via=e.actor_via,
                category=e.category,
                action=e.action,
                object_ref=e.object_ref,
            )
            for e in page
        ],
        next_cursor=str(page[-1].seq) if len(rows) > limit and page else None,
    )


def audit_entry(session: Session, ctx: AppContext, seq: int) -> AuditEntryOut:
    entry = session.get(AuditEntry, seq)
    if entry is None:
        raise ApiError("not_found", f"No audit entry {seq}.")
    recomputed_ok = compute_entry_hash(entry.prev_hash, entry_body(entry)) == entry.entry_hash
    return AuditEntryOut(
        seq=entry.seq,
        occurred_at=iso(entry.occurred_at),
        actor_label=entry.actor_label,
        actor_via=entry.actor_via,
        category=entry.category,
        action=entry.action,
        object_ref=entry.object_ref,
        detail=entry.detail,
        prev_hash=entry.prev_hash,
        entry_hash=entry.entry_hash,
        signature_kid=entry.signature_kid,
        signature_valid=recomputed_ok and signature_valid(ctx.keys, entry),
    )


VERIFICATION_NOTE = (
    "Each line of entries.jsonl is one audit entry. body = the entry without prev_hash, entry_hash, "
    "signature and signature_kid. entry_hash = sha256_hex(prev_hash + '\\n' + JCS(body)) with JCS as "
    "RFC 8785; the first entry of the chain has prev_hash of 64 zeros. signature is base64url of the raw "
    "ES256 (R||S) signature over the ASCII entry_hash, verifiable with jwks.json (audit keys). "
    "manifest.jws is a compact JWS by the audit key over JCS(manifest.json)."
)


def export_bundle(session: Session, ctx: AppContext, from_: date | None, to: date | None) -> bytes:
    statement = select(AuditEntry).order_by(AuditEntry.seq)
    tz = ctx.clock.now().tzinfo
    if from_:
        statement = statement.where(
            AuditEntry.occurred_at >= datetime.combine(from_, time.min).replace(tzinfo=tz)
        )
    if to:
        statement = statement.where(
            AuditEntry.occurred_at
            < datetime.combine(to + timedelta(days=1), time.min).replace(tzinfo=tz)
        )
    lines: list[bytes] = []
    first_seq: int | None = None
    last_seq: int | None = None
    first_prev = GENESIS_HASH
    head = GENESIS_HASH
    for entry in session.scalars(statement):
        record: dict[str, JsonValue] = {
            **entry_body(entry),
            "prev_hash": entry.prev_hash,
            "entry_hash": entry.entry_hash,
            "signature": entry.signature,
            "signature_kid": entry.signature_kid,
        }
        lines.append(canonicalize(record))
        if first_seq is None:
            first_seq = entry.seq
            first_prev = entry.prev_hash
        last_seq = entry.seq
        head = entry.entry_hash
    entries_bytes = b"\n".join(lines) + (b"\n" if lines else b"")
    jwks: dict[str, JsonValue] = {
        "audit": jwks_document(ctx.keys.public_keys("audit")),
        "receipt": jwks_document(ctx.keys.public_keys("receipt")),
    }
    manifest: dict[str, JsonValue] = {
        "generated_at": iso(ctx.clock.now()),
        "from": from_.isoformat() if from_ else None,
        "to": to.isoformat() if to else None,
        "entries": len(lines),
        "first_seq": first_seq,
        "last_seq": last_seq,
        "first_prev_hash": first_prev,
        "last_entry_hash": head,
        "entries_sha256": sha256_hex(entries_bytes),
        "hash_algorithm": "sha256",
        "canonicalization": "RFC 8785 (JCS)",
        "signature_algorithm": "ES256",
        "verification": VERIFICATION_NOTE,
    }
    manifest_jws = sign_compact(
        ctx.keys.current("audit"), canonicalize(manifest), typ="srg-audit-export+jws"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("entries.jsonl", entries_bytes)
        archive.writestr("jwks.json", json.dumps(jwks, indent=2))
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr("manifest.jws", manifest_jws)
    return buffer.getvalue()
