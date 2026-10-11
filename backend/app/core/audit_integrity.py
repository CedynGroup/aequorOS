"""Verify database-owned audit chains; never trust a stored digest alone."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import Condition, emit
from app.core.tls import TransportSecurityError, require_boto_tls

STREAMS = ("audit_events", "operator_audit_log")


class AlertClient(Protocol):
    def publish(self, *, TopicArn: str, Message: str, Subject: str) -> object: ...


def publish_alert(reason: str, stream: str | None = None) -> bool:
    """Publish safe incident metadata to the deployment's standard SNS topic.

    The topic subscription owns pager delivery. Use the worker's AWS identity;
    no bank credential or financial payload participates in this operation.
    """
    topic = get_settings().worker.audit_integrity_alert_topic_arn
    match = re.fullmatch(
        r"arn:(?:aws|aws-us-gov|aws-cn):sns:([a-z0-9-]+):[0-9]{12}:[A-Za-z0-9_-]+",
        topic or "",
    )
    if match is None:
        emit(
            Condition.AUDIT_CHAIN_BROKEN,
            "Audit integrity pager is unconfigured",
            severity="error",
            reason="paging_unconfigured",
        )
        return False
    try:
        factory = cast(Callable[..., object], boto3.client)
        client = cast(
            AlertClient,
            factory(
                "sns",
                region_name=match[1],
                verify=get_settings().tls.ca_bundle or True,
                use_ssl=True,
                config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
            ),
        )
        require_boto_tls(client)
        _ = client.publish(
            TopicArn=topic or "",
            Subject="AequorOS audit integrity failure",
            Message=json.dumps(
                {
                    "condition": Condition.AUDIT_CHAIN_BROKEN.value,
                    "reason": reason,
                    "stream": stream,
                }
            ),
        )
    except (BotoCoreError, ClientError, TransportSecurityError):
        emit(
            Condition.AUDIT_CHAIN_BROKEN,
            "Audit integrity page delivery failed",
            severity="error",
            reason="paging_failed",
        )
        return False
    return True


@dataclass(frozen=True)
class ChainVerification:
    stream: str
    entries: int
    valid: bool


def verify_chains(session: Session) -> list[ChainVerification]:
    """Use one statement snapshot per stream, including its protected head.

    Requires PostgreSQL and an all-tenant verification role. RLS visibility
    must never turn a partial read into a clean verification.
    """
    if session.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Audit integrity verification requires PostgreSQL.")
    privileged = cast(
        bool,
        session.scalar(
            text("""
        SELECT rolsuper OR rolbypassrls OR (
            NOT EXISTS (SELECT 1 FROM pg_class WHERE oid IN
                ('audit_events'::regclass, 'operator_audit_log'::regclass)
                AND (NOT pg_has_role(relowner, 'USAGE')
                     OR relforcerowsecurity)))
        FROM pg_roles WHERE rolname = current_user
    """)
        ),
    )
    if not privileged:
        raise RuntimeError("Audit integrity verification requires an all-tenant role.")
    session.execute(text("SET LOCAL row_security = on"))
    session.execute(text("SET LOCAL TimeZone = 'UTC'"))
    results: list[ChainVerification] = []
    for stream in STREAMS:
        row = session.execute(
            text(f"""
            WITH links AS (
                SELECT c.*, CASE WHEN e.id IS NULL THEN NULL ELSE
                    (SELECT jsonb_object_agg(key, value) FROM jsonb_each(to_jsonb(e))
                     WHERE key = ANY(c.payload_columns)) END AS payload,
                       lag(c.entry_hash, 1, repeat('0', 64)) OVER
                         (ORDER BY c.sequence) AS expected_previous,
                       row_number() OVER (ORDER BY c.sequence) AS expected_sequence
                FROM audit_chain_entries c LEFT JOIN {stream} e ON e.id = c.event_id
                WHERE c.stream = :stream
            )
            SELECT count(l.sequence) AS entries,
                coalesce(bool_and(l.payload IS NOT NULL
                    AND l.sequence = l.expected_sequence
                    AND l.previous_hash = l.expected_previous
                    AND l.entry_hash = encode(sha256(convert_to(jsonb_build_array(
                        'aequoros-audit-v1', :stream, l.sequence, l.previous_hash,
                        l.payload)::text, 'UTF8')), 'hex'))
                    FILTER (WHERE l.sequence IS NOT NULL), true)
                AND count(l.sequence) = h.sequence
                AND (SELECT count(*) FROM {stream}) = h.sequence
                AND coalesce((SELECT entry_hash FROM links ORDER BY sequence DESC LIMIT 1),
                             repeat('0', 64)) = h.entry_hash AS valid
            FROM audit_chain_heads h LEFT JOIN links l ON true
            WHERE h.stream = :stream GROUP BY h.sequence, h.entry_hash
        """),
            {"stream": stream},
        ).one()
        entries, valid = TypeAdapter(tuple[int, bool]).validate_python(tuple(row))
        results.append(ChainVerification(stream, entries, valid))
    return results


def scheduled_verification(session: Session) -> list[ChainVerification]:
    """Return verification outcomes without publishing inside the transaction."""
    try:
        with session.begin_nested():
            results = verify_chains(session)
    except Exception:
        return [ChainVerification("verification_unavailable", 0, False)]
    for result in results:
        if result.valid:
            logger.bind(stream=result.stream, entries=result.entries).info(
                "Audit integrity verification passed"
            )
    return results


@dataclass
class AuditIntegrityTask:
    unavailable_reported: bool = False
    broken_streams: set[str] = field(default_factory=set)

    def report(self, results: list[ChainVerification] | None, *, task_failed: bool = False) -> None:
        broken = {
            result.stream
            for result in results or []
            if not result.valid and result.stream != "verification_unavailable"
        }
        for stream in sorted(broken - self.broken_streams):
            emit(
                Condition.AUDIT_CHAIN_BROKEN,
                "Audit chain verification failed",
                severity="error",
                stream=stream,
            )
            self.broken_streams.add(stream)
            publish_alert("chain_mismatch", stream)
        unavailable = task_failed or any(
            result.stream == "verification_unavailable" for result in results or []
        )
        if unavailable:
            if not self.unavailable_reported and not broken and not self.broken_streams:
                emit(
                    Condition.AUDIT_CHAIN_BROKEN,
                    "Audit integrity verification unavailable",
                    severity="error",
                    reason="verification_unavailable",
                )
                self.unavailable_reported = True
                publish_alert("verification_unavailable")
        elif results and not broken:
            self.unavailable_reported = False
            self.broken_streams.clear()

    def run(
        self, new_session: Callable[[], AbstractContextManager[Session]], now: datetime
    ) -> list[ChainVerification] | None:
        """Coordinate the platform task and report once per worker outage episode."""
        if not get_settings().worker.audit_integrity_enabled:
            return None
        results: list[ChainVerification] | None = None
        failed = False
        try:
            with new_session() as session:
                due = cast(
                    bool | None,
                    session.scalar(
                        text("""
                            SELECT id FROM audit_integrity_schedule
                            WHERE run_after <= :now FOR UPDATE SKIP LOCKED
                        """),
                        {"now": now},
                    ),
                )
                if due is not None:
                    results = scheduled_verification(session)
                    session.execute(
                        text(
                            "UPDATE audit_integrity_schedule "
                            "SET run_after = :next_hour WHERE id = true"
                        ),
                        {
                            "next_hour": now.replace(minute=0, second=0, microsecond=0)
                            + timedelta(hours=1)
                        },
                    )
                    session.commit()
        except Exception:
            failed = True
        self.report(results, task_failed=failed)
        return results
