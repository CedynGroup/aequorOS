"""Verify database-owned audit chains; never trust a stored digest alone."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
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
            current_setting('row_security') = 'off' AND
            NOT EXISTS (SELECT 1 FROM pg_class WHERE oid IN
                ('audit_events'::regclass, 'operator_audit_log'::regclass)
                AND (relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
                     OR relforcerowsecurity)))
        FROM pg_roles WHERE rolname = current_user
    """)
        ),
    )
    if not privileged:
        raise RuntimeError("Audit integrity verification requires an all-tenant role.")
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
    """Emit a paging condition on every failed sweep, without audit recursion."""
    try:
        with session.begin_nested():
            results = verify_chains(session)
    except Exception:
        emit(
            Condition.AUDIT_CHAIN_BROKEN,
            "Audit integrity verification unavailable",
            severity="error",
            reason="verification_unavailable",
        )
        publish_alert("verification_unavailable")
        return [ChainVerification("verification_unavailable", 0, False)]
    for result in results:
        if not result.valid:
            emit(
                Condition.AUDIT_CHAIN_BROKEN,
                "Audit chain verification failed",
                severity="error",
                stream=result.stream,
            )
            publish_alert("chain_mismatch", result.stream)
        else:
            logger.bind(stream=result.stream, entries=result.entries).info(
                "Audit integrity verification passed"
            )
    return results
