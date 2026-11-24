"""Filing: ``POST /v1/submissions`` and amendments (SPEC Part 1, "Filing").

Checks run in the contract's order: authentication (by the router), Idempotency-Key, the
institution's JWS, decoded JWS payload equals JCS(manifest), institution, both digests,
return version applicability, JSON Schema, then version rules (duplicate / amendment).
Any failure so far is refused with no receipt. A well-formed filing is then always
receipted; validation runs after the receipt so rejected evidence is kept.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from jsonschema.exceptions import ValidationError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, Actor, append
from app.core.clock import iso
from app.core.context import AppContext
from app.core.encoding import jcs_sha256, sha256_hex
from app.core.errors import ApiError, Issue
from app.core.jcs import CanonicalizationError, canonicalize
from app.core.sequences import receipt_id as next_receipt_id
from app.db.models import (
    IdempotencyRecord,
    Institution,
    Receipt,
    ReturnDefinition,
    SigningKey,
    Submission,
    SubmissionDocument,
)
from app.filing.obligations import is_reporting_date
from app.keys.jwk import JwkError, public_key_from_jwk
from app.keys.jws import JwsError, header_kid, sign_compact, unverified_header, verify_compact
from app.returns.service import definition as find_definition
from app.returns.service import schema_errors, versions_of

MANIFEST_TYP = "srg-manifest+jws"
RECEIPT_TYP = "srg-receipt+jws"
Basis = Literal["solo", "consolidated"]
SubmissionState = Literal[
    "received", "validating", "rejected", "in_review", "accepted", "queried", "superseded"
]
Channel = Literal["api", "portal_upload"]
HEX64 = r"^[0-9a-f]{64}$"


class Software(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)


#: The documents an institution may file with a return, by media type and extension.
DOCUMENT_TYPES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "application/zip": ".zip",  # a CSV export of a multi-sheet return: one CSV per sheet
    "application/pdf": ".pdf",
}
MAX_DOCUMENTS = 8
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
DOCUMENT_NAME = r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,199}$"


class DocumentRef(BaseModel):
    """One filed document as the signed manifest names it."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=DOCUMENT_NAME)
    media_type: str
    sha256: str = Field(pattern=HEX64)
    bytes: int = Field(ge=1, le=MAX_DOCUMENT_BYTES)


class DocumentUpload(BaseModel):
    """One filed document's content, matched to the manifest by name."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=DOCUMENT_NAME)
    media_type: str
    content_base64: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution_code: str
    return_code: str
    return_version: str
    reporting_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    basis: Basis
    payload_sha256: str = Field(pattern=HEX64)
    declaration_sha256: str = Field(pattern=HEX64)
    supersedes_receipt_id: str | None
    created_at: str
    documents: list[DocumentRef] = Field(default_factory=list, max_length=MAX_DOCUMENTS)


class Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prepared_by_role: str = Field(min_length=1, max_length=200)
    approved_by_role: str = Field(min_length=1, max_length=200)
    approved_at: str
    amendment_reason: str | None
    software: Software | None


class FilingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: Manifest
    payload: dict[str, JsonValue]
    declaration: Declaration
    signature: str = Field(
        description="Compact JWS (ES256) over JCS(manifest), typ srg-manifest+jws"
    )
    documents: list[DocumentUpload] = Field(default_factory=list, max_length=MAX_DOCUMENTS)


class ReceiptBody(BaseModel):
    receipt_id: str
    submission_id: str
    institution_code: str
    return_code: str
    return_version: str
    reporting_date: str
    basis: Basis
    version: int
    supersedes_receipt_id: str | None
    payload_sha256: str
    manifest_sha256: str
    signing_kid: str
    received_at: str
    ledger_seq: int


class ReceiptEnvelope(BaseModel):
    receipt: ReceiptBody
    receipt_jws: str
    status: SubmissionState


@dataclass(frozen=True)
class FilerContext:
    """Who is filing: an API client (``channel="api"``) or a portal upload."""

    institution: Institution
    actor: Actor
    channel: Channel
    environment: str
    client_ref: str
    client_id: str | None
    certificate_thumbprint: str | None
    certificate_not_after: datetime | None


@dataclass(frozen=True)
class FilingOutcome:
    status_code: int
    body: dict[str, JsonValue]
    submission_id: str | None
    replayed: bool


def _state(value: str) -> SubmissionState:
    for state in (
        "received",
        "validating",
        "rejected",
        "in_review",
        "accepted",
        "queried",
        "superseded",
    ):
        if value == state:
            return state
    return "received"


def request_digest(path: str, raw: dict[str, JsonValue]) -> str:
    """The idempotency fingerprint of a filing request: the path and the signed content.

    ``signature`` is left out. ES256 signatures are randomised, so a retried filing
    carries different signature bytes over the identical manifest, and the manifest
    already binds the payload and declaration digests. Every request's signature is
    still verified before anything is recorded.
    """
    signed = {name: value for name, value in raw.items() if name != "signature"}
    return sha256_hex(path.encode("utf-8") + b"\n" + canonicalize(signed))


def _json_object(raw: dict[str, JsonValue], name: str) -> dict[str, JsonValue]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ApiError("invalid_request", f"{name} must be an object.")
    return value


def _schema_issues(error_list: list[ValidationError]) -> list[Issue]:
    issues: list[Issue] = []
    for error in error_list[:50]:
        parts: list[object] = list(error.absolute_path)  # pyright: ignore[reportAny]
        path = "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)
        issues.append(
            Issue(rule_id="schema", severity="error", pointer=path or "/", message=error.message)
        )
    return issues


def _check_signature(
    session: Session, filer: FilerContext, signature: str, manifest_raw: dict[str, JsonValue]
) -> SigningKey:
    try:
        header = unverified_header(signature)
    except JwsError as exc:
        raise ApiError("signature_invalid", "The signature is not a compact JWS.") from exc
    kid = header_kid(header)
    if kid is None:
        raise ApiError("signing_key_unknown", "The JWS protected header has no kid.")
    key = session.scalars(select(SigningKey).where(SigningKey.kid == kid)).first()
    if key is None or key.institution_id != filer.institution.id:
        raise ApiError(
            "signing_key_unknown", f"Signing key {kid} is not registered for this institution."
        )
    if key.status != "active":
        raise ApiError(
            "signing_key_revoked", f"Signing key {kid} is {key.status} and cannot sign new filings."
        )
    if header.get("typ") != MANIFEST_TYP:
        raise ApiError("signature_invalid", f"The JWS typ must be {MANIFEST_TYP}.")
    try:
        parts = verify_compact(
            signature, public_key_from_jwk(key.public_jwk), expected_typ=MANIFEST_TYP
        )
    except (JwsError, JwkError) as exc:
        raise ApiError(
            "signature_invalid", "The filing signature does not verify with the registered key."
        ) from exc
    if parts.payload != canonicalize(manifest_raw):
        raise ApiError(
            "manifest_mismatch", "The signed manifest is not JCS(manifest) of this request."
        )
    return key


def _verified_documents(
    refs: list[DocumentRef], uploads: list[DocumentUpload]
) -> list[tuple[DocumentRef, bytes]]:
    """Each document the manifest names, received intact; nothing more, nothing less.

    The manifest is what the institution signed, so it is the authority: every file it
    lists must arrive with exactly that SHA-256 and size, and no file it does not list
    is accepted.
    """
    names = [ref.name for ref in refs]
    if len(set(names)) != len(names):
        raise ApiError("document_invalid", "manifest.documents names a file twice.")
    by_name = {upload.name: upload for upload in uploads}
    if len(by_name) != len(uploads) or set(by_name) != set(names):
        raise ApiError(
            "document_invalid",
            "documents must carry exactly the files manifest.documents names.",
        )
    verified: list[tuple[DocumentRef, bytes]] = []
    for ref in refs:
        upload = by_name[ref.name]
        extension = DOCUMENT_TYPES.get(ref.media_type)
        if extension is None or upload.media_type != ref.media_type:
            raise ApiError(
                "document_invalid",
                f"{ref.name}: only Excel (.xlsx), CSV (.csv, or .zip of CSVs) and PDF "
                "documents are accepted.",
            )
        if not ref.name.lower().endswith(extension):
            raise ApiError("document_invalid", f"{ref.name}: the name must end in {extension}.")
        try:
            content = base64.b64decode(upload.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError("document_invalid", f"{ref.name}: content is not base64.") from exc
        if len(content) != ref.bytes or sha256_hex(content) != ref.sha256:
            raise ApiError(
                "document_digest_mismatch",
                f"{ref.name} is not the file the signed manifest describes.",
            )
        verified.append((ref, content))
    return verified


def _version_for(
    session: Session, definition: ReturnDefinition, manifest: Manifest, reporting: date
) -> None:
    candidates = [
        v for v in versions_of(session, definition.id) if v.version == manifest.return_version
    ]
    version = next((v for v in candidates if v.status == "published"), None)
    if version is None:
        raise ApiError(
            "return_version_not_applicable",
            f"{definition.code} {manifest.return_version} is not a published version.",
        )
    if reporting < version.first_reporting_date or (
        version.last_reporting_date is not None and reporting > version.last_reporting_date
    ):
        raise ApiError(
            "return_version_not_applicable",
            f"{definition.code} {manifest.return_version} does not apply to reporting date {manifest.reporting_date}.",
        )
    if not is_reporting_date(definition.cadence, reporting):
        raise ApiError(
            "return_version_not_applicable",
            f"{manifest.reporting_date} is not a reporting date of the {definition.cadence} return {definition.code}.",
        )


def current_version(
    session: Session,
    institution_id: str,
    return_code: str,
    reporting: date,
    basis: str,
    environment: str,
) -> Submission | None:
    rows = session.scalars(
        select(Submission)
        .where(
            Submission.institution_id == institution_id,
            Submission.return_code == return_code,
            Submission.reporting_date == reporting,
            Submission.basis == basis,
            Submission.environment == environment,
            Submission.status != "superseded",
        )
        .order_by(Submission.version.desc())
    ).all()
    return rows[0] if rows else None


def submit(
    session: Session,
    ctx: AppContext,
    filer: FilerContext,
    raw: dict[str, JsonValue],
    *,
    path: str,
    idempotency_key: str | None,
    amendment_of: str | None,
    started: float | None = None,
) -> FilingOutcome:
    t0 = started if started is not None else time.monotonic()
    received_at = ctx.clock.now()
    # Idempotency-Key.
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", idempotency_key):
        raise ApiError("idempotency_key_required", "Send an Idempotency-Key header (a UUID).")
    try:
        digest = request_digest(path, raw)
    except CanonicalizationError as exc:
        raise ApiError("invalid_request", f"The body is not canonicalisable JSON: {exc}") from exc
    previous = session.scalars(
        select(IdempotencyRecord).where(
            IdempotencyRecord.client_ref == filer.client_ref,
            IdempotencyRecord.key == idempotency_key,
        )
    ).first()
    if previous is not None:
        if previous.request_sha256 != digest:
            raise ApiError(
                "idempotency_conflict", "This Idempotency-Key was used with a different request."
            )
        receipt_value = previous.response_body.get("receipt")
        replay_ref = receipt_value.get("receipt_id") if isinstance(receipt_value, dict) else None
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=filer.actor,
            category="filing",
            action="Replayed idempotent request",
            object_type="submission",
            object_ref=replay_ref if isinstance(replay_ref, str) else idempotency_key,
            institution_code=filer.institution.code,
            detail={"idempotency_key": idempotency_key},
        )
        return FilingOutcome(previous.response_status, previous.response_body, None, replayed=True)
    try:
        request = FilingRequest.model_validate(raw)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"])
        raise ApiError("invalid_request", f"{location}: {first['msg']}") from exc
    manifest_raw = _json_object(raw, "manifest")
    declaration_raw = _json_object(raw, "declaration")
    payload_raw = _json_object(raw, "payload")
    manifest = request.manifest
    # Institution signature, then the signed manifest.
    signing_key = _check_signature(session, filer, request.signature, manifest_raw)
    if manifest.institution_code != filer.institution.code:
        raise ApiError(
            "institution_mismatch",
            "manifest.institution_code is not the authenticated institution.",
        )
    if jcs_sha256(payload_raw) != manifest.payload_sha256:
        raise ApiError(
            "payload_digest_mismatch", "manifest.payload_sha256 is not sha256(JCS(payload))."
        )
    if jcs_sha256(declaration_raw) != manifest.declaration_sha256:
        raise ApiError(
            "declaration_digest_mismatch",
            "manifest.declaration_sha256 is not sha256(JCS(declaration)).",
        )
    documents = _verified_documents(manifest.documents, request.documents)
    definition = find_definition(session, manifest.return_code)
    if definition is None:
        raise ApiError("return_unknown", f"{manifest.return_code} is not a return in the registry.")
    try:
        reporting = date.fromisoformat(manifest.reporting_date)
    except ValueError as exc:
        raise ApiError("invalid_request", "manifest.reporting_date is not a date.") from exc
    _version_for(session, definition, manifest, reporting)
    version = next(
        v
        for v in versions_of(session, definition.id)
        if v.version == manifest.return_version and v.status == "published"
    )
    errors = schema_errors(version, payload_raw)
    if errors:
        raise ApiError(
            "schema_invalid", f"{len(errors)} schema error(s).", issues=_schema_issues(errors)
        )
    # Versioning: a first version, or an amendment of the current version.
    current = current_version(
        session, filer.institution.id, definition.code, reporting, manifest.basis, filer.environment
    )
    if amendment_of is None:
        if manifest.supersedes_receipt_id is not None:
            raise ApiError(
                "invalid_request",
                "A first version has supersedes_receipt_id null; send amendments to /amendments.",
            )
        if current is not None:
            raise ApiError(
                "duplicate_submission",
                f"{current.receipt_id} is the current version; file an amendment that supersedes it.",
            )
        superseded: Submission | None = None
    else:
        if manifest.supersedes_receipt_id != amendment_of:
            raise ApiError(
                "invalid_request",
                "manifest.supersedes_receipt_id must equal the receipt in the path.",
            )
        target = session.scalars(
            select(Submission).where(Submission.receipt_id == amendment_of)
        ).first()
        if (
            target is None
            or target.institution_id != filer.institution.id
            or target.environment != filer.environment
        ):
            raise ApiError("not_found", f"No filing {amendment_of} for this institution.")
        if (target.return_code, target.reporting_date, target.basis) != (
            definition.code,
            reporting,
            manifest.basis,
        ):
            raise ApiError(
                "invalid_request",
                "An amendment keeps the return, reporting date and basis it supersedes.",
            )
        if current is None or current.id != target.id:
            raise ApiError(
                "supersedes_not_current",
                f"{amendment_of} is not the current version"
                + (f"; the current version is {current.receipt_id}." if current else "."),
            )
        if not (request.declaration.amendment_reason or "").strip():
            raise ApiError(
                "amendment_reason_required",
                "declaration.amendment_reason is required for an amendment.",
            )
        superseded = target
    # Receipt.
    rid = next_receipt_id(session, received_at)
    software = request.declaration.software
    submission = Submission(
        receipt_id=rid,
        institution_id=filer.institution.id,
        return_code=definition.code,
        return_version=version.version,
        reporting_date=reporting,
        basis=manifest.basis,
        version=(superseded.version + 1) if superseded is not None else 1,
        supersedes_id=superseded.id if superseded is not None else None,
        supersedes_receipt_id=superseded.receipt_id if superseded is not None else None,
        status="validating",
        channel=filer.channel,
        environment=filer.environment,
        client_id=filer.client_id,
        certificate_thumbprint=filer.certificate_thumbprint,
        certificate_not_after=filer.certificate_not_after,
        signing_kid=signing_key.kid,
        manifest=manifest_raw,
        payload=payload_raw,
        declaration=declaration_raw,
        signature=request.signature,
        payload_sha256=manifest.payload_sha256,
        manifest_sha256=jcs_sha256(manifest_raw),
        declaration_sha256=manifest.declaration_sha256,
        software_declared=f"{software.name} {software.version}" if software is not None else None,
        received_at=received_at,
        receipt_ms=0,
        ledger_seq=0,
        validation=None,
        validated_at=None,
        reviewer_subject=None,
        decided_at=None,
        decided_by=None,
        superseded_at=None,
    )
    session.add(submission)
    session.flush()
    for ref, content in documents:
        session.add(
            SubmissionDocument(
                submission_id=submission.id,
                name=ref.name,
                media_type=ref.media_type,
                sha256=ref.sha256,
                bytes=ref.bytes,
                content=content,
                received_at=received_at,
            )
        )
    if superseded is not None:
        superseded.status = "superseded"
        superseded.superseded_at = received_at
    label = (
        f"Filed amendment v{submission.version}"
        if superseded
        else f"Filed {definition.code} v{submission.version}"
    )
    entry = append(
        session,
        ctx.keys,
        ctx.clock,
        actor=filer.actor,
        category="filing",
        at=received_at,
        action=label,
        object_type="submission",
        object_ref=rid,
        institution_code=filer.institution.code,
        detail={
            "return_code": definition.code,
            "return_version": version.version,
            "reporting_date": manifest.reporting_date,
            "basis": manifest.basis,
            "version": submission.version,
            "supersedes_receipt_id": submission.supersedes_receipt_id,
            "amendment_reason": request.declaration.amendment_reason,
            "payload_sha256": submission.payload_sha256,
            "manifest_sha256": submission.manifest_sha256,
            "signing_kid": signing_key.kid,
            "signature_verified": True,
            "channel": filer.channel,
            "client_id": filer.client_id,
            "certificate_sha256": filer.certificate_thumbprint,
            "software_declared": submission.software_declared,
        },
    )
    submission.ledger_seq = entry.seq
    receipt = ReceiptBody(
        receipt_id=rid,
        submission_id=submission.id,
        institution_code=filer.institution.code,
        return_code=definition.code,
        return_version=version.version,
        reporting_date=manifest.reporting_date,
        basis=manifest.basis,
        version=submission.version,
        supersedes_receipt_id=submission.supersedes_receipt_id,
        payload_sha256=submission.payload_sha256,
        manifest_sha256=submission.manifest_sha256,
        signing_kid=signing_key.kid,
        received_at=iso(received_at),
        ledger_seq=entry.seq,
    )
    receipt_json: dict[str, JsonValue] = receipt.model_dump(mode="json")  # pyright: ignore[reportAny]
    receipt_key = ctx.keys.current("receipt")
    jws = sign_compact(receipt_key, canonicalize(receipt_json), typ=RECEIPT_TYP)
    session.add(
        Receipt(
            submission_id=submission.id,
            receipt_id=rid,
            receipt=receipt_json,
            receipt_jws=jws,
            kid=receipt_key.kid,
            issued_at=received_at,
        )
    )
    submission.receipt_ms = max(1, int((time.monotonic() - t0) * 1000))
    if superseded is not None:
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=SYSTEM,
            category="filing",
            at=received_at,
            action=f"Superseded by {rid}",
            object_type="submission",
            object_ref=superseded.receipt_id,
            institution_code=filer.institution.code,
            detail={"superseded_by": rid},
        )
    envelope = ReceiptEnvelope(receipt=receipt, receipt_jws=jws, status="validating")
    body: dict[str, JsonValue] = envelope.model_dump(mode="json")  # pyright: ignore[reportAny]
    session.add(
        IdempotencyRecord(
            client_ref=filer.client_ref,
            key=idempotency_key,
            request_sha256=digest,
            response_status=202,
            response_body=body,
            created_at=received_at,
        )
    )
    if software is not None:
        filer.institution.software_declared = f"{software.name} {software.version}"
    if filer.environment == "production" and filer.institution.live_since is None:
        filer.institution.live_since = reporting
    try:
        session.flush()
    except IntegrityError as exc:
        raise ApiError(
            "conflict", "A concurrent request is using this Idempotency-Key or version."
        ) from exc
    return FilingOutcome(202, body, submission.id, replayed=False)


def receipt_envelope(session: Session, submission: Submission) -> ReceiptEnvelope:
    receipt = session.scalars(select(Receipt).where(Receipt.submission_id == submission.id)).one()
    return ReceiptEnvelope(
        receipt=ReceiptBody.model_validate(receipt.receipt),
        receipt_jws=receipt.receipt_jws,
        status=_state(submission.status),
    )


def state_of(value: str) -> SubmissionState:
    return _state(value)
