"""Hash-chained storage access log (storage.md §9).

Every storage operation produces an entry whose ``entry_hash`` covers the
previous entry's hash, forming a tamper-evident chain: modifying any past
entry breaks every subsequent hash and is detectable by re-verification.

The chain lives in memory per recorder and is flushed as JSON lines to the
audit bucket (and mirrored to application logs). MVP keeps the recorder
in-process and synchronous; the chain semantics — not the transport — are
the contract, and ``verify_chain`` works on any exported sequence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.storage.client import StorageLocation

GENESIS_HASH = "0" * 64

# Signature every StorageClient backend calls for each operation.
AccessLogHook = Callable[..., None]


def null_access_log(
    operation: str,
    location: StorageLocation,
    *,
    version_id: str | None = None,
    result: str = "success",
) -> None:
    """No-op hook for tests and tooling that opt out of audit recording."""


@dataclass(frozen=True)
class AccessLogEntry:
    sequence: int
    timestamp: str
    operation: str
    institution_slug: str
    tier: str
    object_path: str
    version_id: str | None
    identity: str
    result: str
    prev_hash: str
    entry_hash: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "operation": self.operation,
            "institution_slug": self.institution_slug,
            "tier": self.tier,
            "object_path": self.object_path,
            "version_id": self.version_id,
            "identity": self.identity,
            "result": self.result,
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class HashChainedAccessLog:
    """Records operations as a hash chain; export as JSONL for the audit bucket."""

    identity: str
    entries: list[AccessLogEntry] = field(default_factory=list)
    _flushed_through: int = 0

    def __call__(
        self,
        operation: str,
        location: StorageLocation,
        *,
        version_id: str | None = None,
        result: str = "success",
    ) -> None:
        prev_hash = self.entries[-1].entry_hash if self.entries else GENESIS_HASH
        entry = AccessLogEntry(
            sequence=len(self.entries),
            timestamp=datetime.now(UTC).isoformat(),
            operation=operation,
            institution_slug=location.institution_slug,
            tier=location.tier,
            object_path=location.object_path,
            version_id=version_id,
            identity=self.identity,
            result=result,
            prev_hash=prev_hash,
        )
        sealed = AccessLogEntry(**{**entry.__dict__, "entry_hash": entry.compute_hash()})
        self.entries.append(sealed)

    def export_jsonl(self) -> str:
        return "\n".join(
            json.dumps({**entry.payload(), "entry_hash": entry.entry_hash}, sort_keys=True)
            for entry in self.entries
        )

    def drain_segment(self) -> tuple[str, int, int] | None:
        """Entries recorded since the last drain, as (jsonl, first_seq, last_seq).

        Entries stay in memory so the chain remains continuous across
        segments: the first entry of segment N+1 carries the last hash of
        segment N in ``prev_hash``, so concatenated segments verify as one
        chain. Returns None when nothing new was recorded.
        """
        pending = self.entries[self._flushed_through :]
        if not pending:
            return None
        jsonl = "\n".join(
            json.dumps({**entry.payload(), "entry_hash": entry.entry_hash}, sort_keys=True)
            for entry in pending
        )
        first, last = pending[0].sequence, pending[-1].sequence
        self._flushed_through = len(self.entries)
        return jsonl, first, last


def verify_chain(jsonl: str) -> tuple[bool, str]:
    """Re-verify an exported chain; returns (intact, detail).

    Runs as a scheduled job in production (storage.md §9.3); any mismatch
    names the first broken sequence number so investigation starts exactly
    where tampering (or corruption) begins.
    """
    prev_hash = GENESIS_HASH
    for line in filter(None, jsonl.splitlines()):
        record = json.loads(line)
        claimed_hash = record.pop("entry_hash")
        if record["prev_hash"] != prev_hash:
            return False, f"chain broken at sequence {record['sequence']}: prev_hash mismatch"
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        actual = hashlib.sha256(canonical.encode()).hexdigest()
        if actual != claimed_hash:
            return False, f"chain broken at sequence {record['sequence']}: entry_hash mismatch"
        prev_hash = claimed_hash
    return True, "chain intact"
