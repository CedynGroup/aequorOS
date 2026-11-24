"""The audit hash chain (ARCHITECTURE.md, "Evidence: the audit chain").

``entry_hash = sha256(prev_hash_hex + "\\n" + JCS(body))`` where ``body`` is every column
except ``prev_hash``, ``entry_hash``, ``signature`` and ``signature_kid``; the first entry's
``prev_hash`` is 64 zeros. ``signature`` is the audit key's ES256 signature (raw R||S,
base64url) over the ASCII bytes of ``entry_hash``. Appends hold a transaction-level lock,
so ``seq`` is gapless and the chain linear.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import Delete, Update, event, select, text
from sqlalchemy.orm import ORMExecuteState, Session

from app.core.clock import Clock, iso, utc
from app.core.encoding import b64url_decode, b64url_encode, sha256_hex
from app.core.jcs import canonicalize
from app.db.models import AuditEntry
from app.keys.jwk import verify_raw
from app.keys.provider import KeyProvider

ActorType = Literal["staff", "institution", "system", "operator"]
Category = Literal[
    "sign_in",
    "filing",
    "decision",
    "view",
    "query",
    "configuration",
    "credential",
    "operator_access",
    "system",
]

GENESIS_HASH = "0" * 64
AUDIT_TABLE = "audit_entries"
ADVISORY_LOCK_KEY = 0x5352_4741_5544_4954  # "SRGAUDIT"

_sqlite_lock = threading.RLock()


class AppendOnlyViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class Actor:
    type: ActorType
    ref: str
    label: str
    via: str


SYSTEM = Actor("system", "system", "System", "Gateway service")


def entry_body(entry: AuditEntry) -> dict[str, JsonValue]:
    return {
        "seq": entry.seq,
        "occurred_at": iso(entry.occurred_at),
        "actor_type": entry.actor_type,
        "actor_ref": entry.actor_ref,
        "actor_label": entry.actor_label,
        "actor_via": entry.actor_via,
        "category": entry.category,
        "action": entry.action,
        "object_type": entry.object_type,
        "object_ref": entry.object_ref,
        "institution_code": entry.institution_code,
        "detail": entry.detail,
    }


def compute_entry_hash(prev_hash: str, body: dict[str, JsonValue]) -> str:
    return sha256_hex((prev_hash + "\n").encode("ascii") + canonicalize(body))


def _truncate_ms(moment: datetime) -> datetime:
    value = utc(moment)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _lock(session: Session) -> None:
    if session.info.get("audit_lock_held"):
        return
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
        session.info["audit_lock_held"] = True
        return
    _sqlite_lock.acquire()
    session.info["audit_lock_held"] = True
    session.info["audit_lock_local"] = True


def _release(session: Session) -> None:
    held_local = bool(session.info.pop("audit_lock_local", False))  # pyright: ignore[reportAny]
    session.info.pop("audit_lock_held", None)
    session.info.pop("audit_head", None)
    if held_local:
        _sqlite_lock.release()


def head(session: Session) -> tuple[int, str]:
    row = session.execute(
        select(AuditEntry.seq, AuditEntry.entry_hash).order_by(AuditEntry.seq.desc()).limit(1)
    ).first()
    if row is None:
        return 0, GENESIS_HASH
    return int(row[0]), str(row[1])


def append(
    session: Session,
    keys: KeyProvider,
    clock: Clock,
    *,
    actor: Actor,
    category: Category,
    action: str,
    object_type: str,
    object_ref: str,
    institution_code: str | None = None,
    detail: dict[str, JsonValue] | None = None,
    at: datetime | None = None,
) -> AuditEntry:
    _lock(session)
    cached: object = session.info.get("audit_head")
    if isinstance(cached, tuple):
        last_seq, prev_hash = cached  # pyright: ignore[reportUnknownVariableType]
        last_seq = int(last_seq)  # pyright: ignore[reportUnknownArgumentType]
        prev_hash = str(prev_hash)  # pyright: ignore[reportUnknownArgumentType]
    else:
        session.flush()
        last_seq, prev_hash = head(session)
    key = keys.current("audit")
    entry = AuditEntry(
        seq=last_seq + 1,
        occurred_at=_truncate_ms(at or clock.now()),
        actor_type=actor.type,
        actor_ref=actor.ref,
        actor_label=actor.label,
        actor_via=actor.via,
        category=category,
        action=action,
        object_type=object_type,
        object_ref=object_ref,
        institution_code=institution_code,
        detail=detail or {},
        prev_hash=prev_hash,
        entry_hash="",
        signature="",
        signature_kid=key.kid,
    )
    entry.entry_hash = compute_entry_hash(prev_hash, entry_body(entry))
    entry.signature = b64url_encode(key.sign(entry.entry_hash.encode("ascii")))
    session.add(entry)
    session.flush()
    session.info["audit_head"] = (entry.seq, entry.entry_hash)
    return entry


@dataclass(frozen=True)
class VerifyResult:
    intact: bool
    entries: int
    head_hash: str
    first_bad_seq: int | None


def signature_valid(keys: KeyProvider, entry: AuditEntry) -> bool:
    key = keys.find("audit", entry.signature_kid)
    if key is None:
        return False
    try:
        signature = b64url_decode(entry.signature)
    except ValueError:
        return False
    return verify_raw(key.public_key, entry.entry_hash.encode("ascii"), signature)


def iter_entries(session: Session, batch: int = 2000) -> Iterator[AuditEntry]:
    last = 0
    while True:
        rows = list(
            session.scalars(
                select(AuditEntry)
                .where(AuditEntry.seq > last)
                .order_by(AuditEntry.seq)
                .limit(batch)
            )
        )
        if not rows:
            return
        yield from rows
        last = rows[-1].seq
        for row in rows:
            session.expunge(row)


def verify(session: Session, keys: KeyProvider) -> VerifyResult:
    expected_seq = 1
    prev_hash = GENESIS_HASH
    count = 0
    for entry in iter_entries(session):
        count += 1
        recomputed = compute_entry_hash(entry.prev_hash, entry_body(entry))
        if (
            entry.seq != expected_seq
            or entry.prev_hash != prev_hash
            or recomputed != entry.entry_hash
            or not signature_valid(keys, entry)
        ):
            return VerifyResult(False, count, prev_hash, entry.seq)
        prev_hash = entry.entry_hash
        expected_seq += 1
    return VerifyResult(True, count, prev_hash, None)


def install_append_only_guard(session_factory: type[Session] = Session) -> None:
    """Refuse UPDATE and DELETE of audit rows through the ORM, on every backend."""

    @event.listens_for(session_factory, "before_flush")
    def _no_dirty(session: Session, _context: object, _instances: object) -> None:  # pyright: ignore[reportUnusedFunction]
        dirty: list[object] = list(session.dirty)
        deleted: list[object] = list(session.deleted)
        for obj in dirty:
            if isinstance(obj, AuditEntry) and session.is_modified(obj):
                raise AppendOnlyViolation("audit entries cannot be changed")
        for obj in deleted:
            if isinstance(obj, AuditEntry):
                raise AppendOnlyViolation("audit entries cannot be deleted")

    @event.listens_for(session_factory, "do_orm_execute")
    def _no_bulk(state: ORMExecuteState) -> None:  # pyright: ignore[reportUnusedFunction]
        statement = state.statement
        if isinstance(statement, (Update, Delete)):
            table = getattr(statement, "table", None)
            if getattr(table, "name", None) == AUDIT_TABLE:
                raise AppendOnlyViolation("audit entries cannot be changed or deleted")

    @event.listens_for(session_factory, "after_transaction_end")
    def _release_lock(session: Session, transaction: object) -> None:  # pyright: ignore[reportUnusedFunction]
        if getattr(transaction, "parent", None) is None:
            _release(session)
