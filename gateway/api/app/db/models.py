"""The gateway data model (ARCHITECTURE.md, "Data model")."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import JsonValue
from sqlalchemy import BigInteger, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_id


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(8), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str | None] = mapped_column(String(20))
    software_declared: Mapped[str | None] = mapped_column(String(120))
    flags: Mapped[list[str]] = mapped_column(default=list)
    supervisor_subject: Mapped[str | None] = mapped_column(String(120))
    portal_users: Mapped[int] = mapped_column(default=0)
    portal_mfa: Mapped[bool] = mapped_column(default=True)
    registered_by: Mapped[str] = mapped_column(String(120))
    registered_at: Mapped[datetime]
    live_since: Mapped[date | None]


class InstitutionContact(Base):
    __tablename__ = "institution_contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    role: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))


class ApiClient(Base):
    __tablename__ = "api_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(String(80), unique=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    environment: Mapped[str] = mapped_column(String(20))
    certificate_pem: Mapped[str] = mapped_column(Text)
    certificate_thumbprint: Mapped[str] = mapped_column(String(64), unique=True)
    certificate_serial: Mapped[str] = mapped_column(String(64))
    certificate_not_after: Mapped[datetime]
    jwks: Mapped[dict[str, JsonValue]]
    scopes: Mapped[list[str]]
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime]
    created_by: Mapped[str] = mapped_column(String(120))


class SigningKey(Base):
    __tablename__ = "signing_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    kid: Mapped[str] = mapped_column(String(120), unique=True)
    public_jwk: Mapped[dict[str, JsonValue]]
    status: Mapped[str] = mapped_column(String(20), default="active")
    custody_note: Mapped[str] = mapped_column(String(300))
    registered_at: Mapped[datetime]
    registered_by: Mapped[str] = mapped_column(String(120))
    revoked_at: Mapped[datetime | None]


class ReturnDefinition(Base):
    __tablename__ = "return_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    cadence: Mapped[str] = mapped_column(String(20))
    applies_to: Mapped[list[str]]
    synthetic: Mapped[bool] = mapped_column(default=False)
    auto_accept: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime]


class ReturnVersion(Base):
    __tablename__ = "return_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    definition_id: Mapped[str] = mapped_column(ForeignKey("return_definitions.id"), index=True)
    version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    first_reporting_date: Mapped[date]
    last_reporting_date: Mapped[date | None]
    schema: Mapped[dict[str, JsonValue]]
    rules: Mapped[list[JsonValue]]
    schema_sha256: Mapped[str] = mapped_column(String(64))
    rules_sha256: Mapped[str] = mapped_column(String(64))
    rule_count: Mapped[int]
    change_summary: Mapped[str] = mapped_column(Text, default="")
    notice_days: Mapped[int] = mapped_column(default=0)
    breaking: Mapped[bool] = mapped_column(default=False)
    drafted_by: Mapped[str] = mapped_column(String(120))
    drafted_at: Mapped[datetime]
    published_at: Mapped[datetime | None]
    returned_reason: Mapped[str | None] = mapped_column(Text)


class ReturnVersionApproval(Base):
    __tablename__ = "return_version_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("return_versions.id"), index=True)
    step: Mapped[str] = mapped_column(String(20))
    actor_subject: Mapped[str] = mapped_column(String(120))
    actor_name: Mapped[str] = mapped_column(String(200))
    role_label: Mapped[str] = mapped_column(String(80))
    at: Mapped[datetime]


class Obligation(Base):
    """A per-institution due-date exception (an extension). Obligations themselves are
    derived from the published return definitions; only exceptions are stored."""

    __tablename__ = "obligations"
    __table_args__: tuple[object, ...] = (
        UniqueConstraint("institution_id", "return_code", "reporting_date", "basis"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    return_code: Mapped[str] = mapped_column(String(40))
    reporting_date: Mapped[date]
    basis: Mapped[str] = mapped_column(String(20))
    due_at: Mapped[datetime]
    reason: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[str] = mapped_column(String(120))
    granted_at: Mapped[datetime]


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__: tuple[object, ...] = (
        Index("ix_submissions_key", "institution_id", "return_code", "reporting_date", "basis"),
        Index("ix_submissions_received_at", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    receipt_id: Mapped[str] = mapped_column(String(20), unique=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    return_code: Mapped[str] = mapped_column(String(40))
    return_version: Mapped[str] = mapped_column(String(20))
    reporting_date: Mapped[date]
    basis: Mapped[str] = mapped_column(String(20))
    version: Mapped[int]
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id"))
    supersedes_receipt_id: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20))
    environment: Mapped[str] = mapped_column(String(20))
    client_id: Mapped[str | None] = mapped_column(String(80))
    certificate_thumbprint: Mapped[str | None] = mapped_column(String(64))
    certificate_not_after: Mapped[datetime | None]
    signing_kid: Mapped[str] = mapped_column(String(120))
    manifest: Mapped[dict[str, JsonValue]]
    payload: Mapped[dict[str, JsonValue]]
    declaration: Mapped[dict[str, JsonValue]]
    signature: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    declaration_sha256: Mapped[str] = mapped_column(String(64))
    software_declared: Mapped[str | None] = mapped_column(String(120))
    received_at: Mapped[datetime]
    receipt_ms: Mapped[int] = mapped_column(default=0)
    ledger_seq: Mapped[int] = mapped_column(BigInteger)
    validation: Mapped[dict[str, JsonValue] | None] = mapped_column(default=None)
    validated_at: Mapped[datetime | None]
    reviewer_subject: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None] = mapped_column(String(120))
    superseded_at: Mapped[datetime | None]


class SubmissionDocument(Base):
    """A document the institution filed with a return: the official Excel, CSV or PDF.

    The bytes are exactly what was received. Each document's SHA-256 and size are in
    the manifest the institution signed, so the signature covers the files as well as
    the structured payload. Immutable on PostgreSQL (migration 0003's trigger).
    """

    __tablename__ = "submission_documents"
    __table_args__: tuple[object, ...] = (UniqueConstraint("submission_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    received_at: Mapped[datetime]


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), unique=True)
    receipt_id: Mapped[str] = mapped_column(String(20), unique=True)
    receipt: Mapped[dict[str, JsonValue]]
    receipt_jws: Mapped[str] = mapped_column(Text)
    kid: Mapped[str] = mapped_column(String(120))
    issued_at: Mapped[datetime]


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    receipt_id: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30))
    due_at: Mapped[datetime | None]
    opened_at: Mapped[datetime]
    opened_by: Mapped[str] = mapped_column(String(120))
    opened_by_name: Mapped[str] = mapped_column(String(200))
    delivered_at: Mapped[datetime | None]
    acknowledged_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    closed_by: Mapped[str | None] = mapped_column(String(120))
    last_activity_at: Mapped[datetime]


class QueryMessage(Base):
    __tablename__ = "query_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(ForeignKey("queries.id"), index=True)
    author: Mapped[str] = mapped_column(String(20))
    author_label: Mapped[str] = mapped_column(String(200))
    author_role: Mapped[str | None] = mapped_column(String(120))
    author_ref: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    require_resubmission: Mapped[bool] = mapped_column(default=False)
    resubmission_receipt_id: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime]


class SupervisoryNote(Base):
    __tablename__ = "supervisory_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    author_subject: Mapped[str] = mapped_column(String(120))
    author_name: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]


class StaffUser(Base):
    __tablename__ = "staff_users"

    subject: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))
    groups: Mapped[list[str]]
    roles: Mapped[list[str]]
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class StaffAccount(Base):
    """A supervisor account held by the gateway's own identity provider.

    Used when the Bank does not federate its own identity provider
    (``GATEWAY_OIDC_ISSUER`` unset). The password is stored only as an Argon2id hash;
    ``groups`` carry the role mapping exactly as an external provider's claim would.
    Accounts are disabled, never deleted, so the audit log's references stay resolvable.
    """

    __tablename__ = "staff_accounts"

    subject: Mapped[str] = mapped_column(String(120), primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    given_name: Mapped[str | None] = mapped_column(String(100))
    family_name: Mapped[str | None] = mapped_column(String(100))
    groups: Mapped[list[str]]
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="active")
    failed_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    password_changed_at: Mapped[datetime]
    last_sign_in_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    created_by: Mapped[str] = mapped_column(String(120))


class StaffSession(Base):
    __tablename__ = "staff_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    first_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    mfa: Mapped[bool] = mapped_column(default=False)


class OperatorAccessRequest(Base):
    __tablename__ = "operator_access_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    operator_id: Mapped[str] = mapped_column(String(80))
    operator_email: Mapped[str] = mapped_column(String(200))
    operator_name: Mapped[str] = mapped_column(String(200))
    operator_title: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(Text)
    excludes: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    incident_ref: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    requested_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_by_name: Mapped[str | None] = mapped_column(String(200))
    duration_minutes: Mapped[int | None]
    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]
    deny_reason: Mapped[str | None] = mapped_column(Text)


class AuditEntry(Base):
    __tablename__ = "audit_entries"
    __table_args__: tuple[object, ...] = (
        Index("ix_audit_entries_occurred_at", "occurred_at"),
        Index("ix_audit_entries_object_ref", "object_ref"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    occurred_at: Mapped[datetime]
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_ref: Mapped[str] = mapped_column(String(120))
    actor_label: Mapped[str] = mapped_column(String(200))
    actor_via: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(40))
    object_ref: Mapped[str] = mapped_column(String(120))
    institution_code: Mapped[str | None] = mapped_column(String(8), index=True)
    detail: Mapped[dict[str, JsonValue]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    signature_kid: Mapped[str] = mapped_column(String(120))


class AuditVerification(Base):
    __tablename__ = "audit_verifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    verified_at: Mapped[datetime] = mapped_column(index=True)
    trigger: Mapped[str] = mapped_column(String(20))
    intact: Mapped[bool]
    entries: Mapped[int] = mapped_column(BigInteger)
    head_hash: Mapped[str] = mapped_column(String(64))
    first_bad_seq: Mapped[int | None] = mapped_column(BigInteger)


class Counter(Base):
    """Gapless sequences (receipt ids, query and operator-session numbers)."""

    __tablename__ = "counters"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__: tuple[object, ...] = (UniqueConstraint("client_ref", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_ref: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(80))
    request_sha256: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int]
    response_body: Mapped[dict[str, JsonValue]]
    created_at: Mapped[datetime]


class UsedAssertion(Base):
    __tablename__ = "used_client_assertions"
    __table_args__: tuple[object, ...] = (UniqueConstraint("client_id", "jti"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(String(80))
    jti: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime]


class ApiDailyStat(Base):
    __tablename__ = "api_daily_stats"

    day: Mapped[date] = mapped_column(primary_key=True)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    server_errors: Mapped[int] = mapped_column(BigInteger, default=0)
