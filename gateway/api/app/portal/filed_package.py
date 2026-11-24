"""Supervisor downloads of a filing: its receipt, its documents and the filed package.

All of it is evidence a supervisor can take out of the portal and verify elsewhere,
without access to the gateway: every file is either signed or bound by digest to
something signed, and the public keys needed to check the signatures travel with
it. Nothing here is recomputed from current state; the package carries the filing
exactly as the gateway received and recorded it.

Each download is recorded in the audit log, because a filing leaving the portal is a
supervisory act on the institution's data.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import entry_body
from app.auth.staff import StaffPrincipal
from app.core.clock import iso, iso_or_none
from app.core.context import AppContext
from app.core.encoding import sha256_hex
from app.core.errors import ApiError
from app.core.jcs import canonicalize
from app.db.models import (
    AuditEntry,
    Institution,
    Receipt,
    SigningKey,
    Submission,
    SubmissionDocument,
)
from app.filing.service import state_of
from app.keys.jws import sign_compact
from app.keys.provider import jwks_document
from app.portal.common import record_view, submission_by_receipt

PACKAGE_INDEX_TYP = "srg-filed-package+jws"


@dataclass(frozen=True, slots=True)
class Download:
    body: bytes
    filename: str
    content_type: str


_VERIFY = """\
How to verify this filed package
================================

Everything here can be checked without access to the AequorOS Reporting Gateway.
manifest.json, payload.json and declaration.json are stored as RFC 8785 (JCS)
canonical bytes, so their SHA-256 digests can be taken directly.

1. The package is complete and unaltered.
   index.jws is a compact JWS (ES256) whose payload is exactly the bytes of
   index.json. Verify it with the key in gateway-audit-keys.json named by its
   header kid. index.json lists the SHA-256 of every other file in the package.

2. The institution signed the filing.
   signature.jws is a compact JWS (ES256) whose payload is exactly the bytes of
   manifest.json. Verify it with the key in institution-signing-key.json; the
   JWS header names that key's kid.

3. The figures and documents are the ones the institution signed.
   SHA-256 of payload.json equals manifest.json "payload_sha256".
   SHA-256 of declaration.json equals manifest.json "declaration_sha256".
   For each file in documents/, its SHA-256 and size equal the entry with the
   same name in manifest.json "documents". The Excel, CSV and PDF are therefore
   covered by the institution's signature as well as the structured payload.

4. The gateway received it, when it says it did.
   receipt.jws is a compact JWS (ES256) whose payload is the canonical form of
   receipt.json. Verify it with the key in gateway-receipt-keys.json named by
   its header kid. The receipt repeats the payload and manifest digests, the
   signing key, the time of receipt and the evidence ledger entry.

5. The filing is on the gateway's tamper-evident ledger.
   ledger-entry.json is the ledger entry the receipt names. Its entry_hash is
   SHA-256 of (prev_hash + "\\n" + JCS(entry without prev_hash, entry_hash,
   signature, signature_kid)); its signature (ES256 over the entry_hash) verifies
   with gateway-audit-keys.json. The gateway's full audit export continues the
   chain from prev_hash.

transport.json records how the filing arrived: the API client, and the mutual-TLS
client certificate's SHA-256 fingerprint and expiry. validation.json is the
gateway's validation result at receipt; it records what the gateway checked and
is not part of what the institution signed.
"""


def _submission(session: Session, receipt_id: str) -> tuple[Submission, Institution, Receipt]:
    sub = submission_by_receipt(session, receipt_id)
    institution = session.get_one(Institution, sub.institution_id)
    receipt = session.scalars(select(Receipt).where(Receipt.submission_id == sub.id)).one()
    return sub, institution, receipt


def _documents(session: Session, sub: Submission) -> list[SubmissionDocument]:
    return list(
        session.scalars(
            select(SubmissionDocument)
            .where(SubmissionDocument.submission_id == sub.id)
            .order_by(SubmissionDocument.name)
        )
    )


def _pretty(document: object) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def receipt_download(
    session: Session, ctx: AppContext, principal: StaffPrincipal, receipt_id: str
) -> Download:
    sub, institution, receipt = _submission(session, receipt_id)
    record_view(
        session,
        ctx,
        principal,
        action="Downloaded receipt",
        object_type="submission",
        object_ref=sub.receipt_id,
        institution_code=institution.code,
    )
    document: dict[str, JsonValue] = {
        "receipt": receipt.receipt,
        "receipt_jws": receipt.receipt_jws,
        "status": state_of(sub.status),
    }
    return Download(
        body=_pretty(document),
        filename=f"{sub.receipt_id}-receipt.json",
        content_type="application/json",
    )


def document_download(
    session: Session, ctx: AppContext, principal: StaffPrincipal, receipt_id: str, name: str
) -> Download:
    """One filed document, byte for byte as received."""
    sub, institution, _ = _submission(session, receipt_id)
    document = next((d for d in _documents(session, sub) if d.name == name), None)
    if document is None:
        raise ApiError("not_found", f"{receipt_id} has no filed document {name}.")
    record_view(
        session,
        ctx,
        principal,
        action="Downloaded filed document",
        object_type="submission",
        object_ref=sub.receipt_id,
        institution_code=institution.code,
        detail={"document": document.name, "sha256": document.sha256},
    )
    return Download(body=document.content, filename=document.name, content_type=document.media_type)


def _ledger_entry(session: Session, sub: Submission) -> dict[str, JsonValue] | None:
    entry = session.get(AuditEntry, sub.ledger_seq)
    if entry is None:
        return None
    return {
        **entry_body(entry),
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
        "signature": entry.signature,
        "signature_kid": entry.signature_kid,
    }


def package_download(
    session: Session, ctx: AppContext, principal: StaffPrincipal, receipt_id: str
) -> Download:
    sub, institution, receipt = _submission(session, receipt_id)
    key = session.scalars(select(SigningKey).where(SigningKey.kid == sub.signing_kid)).first()
    documents = _documents(session, sub)

    files: dict[str, bytes] = {
        "manifest.json": canonicalize(sub.manifest),
        "payload.json": canonicalize(sub.payload),
        "declaration.json": canonicalize(sub.declaration),
        "signature.jws": sub.signature.encode("ascii"),
        "receipt.json": _pretty(receipt.receipt),
        "receipt.jws": receipt.receipt_jws.encode("ascii"),
        "institution-signing-key.json": _pretty(
            {
                "institution_code": institution.code,
                "kid": sub.signing_kid,
                "public_jwk": key.public_jwk if key is not None else None,
                "status": key.status if key is not None else "unknown",
                "registered_at": iso(key.registered_at) if key is not None else None,
                "custody_note": key.custody_note if key is not None else None,
            }
        ),
        "gateway-receipt-keys.json": _pretty(jwks_document(ctx.keys.public_keys("receipt"))),
        "gateway-audit-keys.json": _pretty(jwks_document(ctx.keys.public_keys("audit"))),
        "ledger-entry.json": _pretty(_ledger_entry(session, sub)),
        "transport.json": _pretty(
            {
                "channel": sub.channel,
                "environment": sub.environment,
                "client_id": sub.client_id,
                "client_certificate_sha256": sub.certificate_thumbprint,
                "client_certificate_not_after": iso_or_none(sub.certificate_not_after),
                "received_at": iso(sub.received_at),
            }
        ),
        "validation.json": _pretty(sub.validation),
        "VERIFY.txt": _VERIFY.encode("utf-8"),
    }
    for document in documents:
        files[f"documents/{document.name}"] = document.content
    index: dict[str, JsonValue] = {
        "receipt_id": sub.receipt_id,
        "institution_code": institution.code,
        "institution_name": institution.name,
        "return_code": sub.return_code,
        "return_version": sub.return_version,
        "reporting_date": sub.reporting_date.isoformat(),
        "basis": sub.basis,
        "version": sub.version,
        "supersedes_receipt_id": sub.supersedes_receipt_id,
        "status": state_of(sub.status),
        "received_at": iso(sub.received_at),
        "ledger_seq": sub.ledger_seq,
        "exported_at": iso(ctx.clock.now()),
        "exported_by": principal.short_name,
        "documents": [
            {
                "name": document.name,
                "media_type": document.media_type,
                "sha256": document.sha256,
                "bytes": document.bytes,
            }
            for document in documents
        ],
        "files": {name: sha256_hex(content) for name, content in sorted(files.items())},
        "hash_algorithm": "sha256",
        "canonicalization": "RFC 8785 (JCS)",
    }
    index_bytes = _pretty(index)
    index_jws = sign_compact(ctx.keys.current("audit"), index_bytes, typ=PACKAGE_INDEX_TYP)
    record_view(
        session,
        ctx,
        principal,
        action="Downloaded filed package",
        object_type="submission",
        object_ref=sub.receipt_id,
        institution_code=institution.code,
        detail={"payload_sha256": sub.payload_sha256, "documents": len(documents)},
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("index.json", index_bytes)
        archive.writestr("index.jws", index_jws)
    return Download(
        body=buffer.getvalue(),
        filename=f"{sub.receipt_id}-filed-package.zip",
        content_type="application/zip",
    )
