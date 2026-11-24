"""Supervisor submissions: inbox, detail (with evidence re-verification) and decisions."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, JsonValue
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.audit.chain import AuditEntry, append, compute_entry_hash, entry_body, signature_valid
from app.auth.staff import StaffPrincipal, require_decider
from app.core.clock import iso, utc
from app.core.context import AppContext
from app.core.errors import ApiError, Issue
from app.core.jcs import canonicalize
from app.db.models import (
    AuditVerification,
    Institution,
    Query,
    Receipt,
    ReturnDefinition,
    SigningKey,
    StaffUser,
    Submission,
    SubmissionDocument,
)
from app.filing.obligations import compute_obligations, counts_as_filed, previous_reporting_date
from app.filing.service import RECEIPT_TYP, SubmissionState, state_of
from app.filing.validation import figure, labels_of
from app.keys.jwk import JwkError, public_key_from_jwk
from app.keys.jws import JwsError, verify_compact
from app.portal.common import (
    Lookups,
    SubmissionRow,
    int_field,
    record_view,
    rows,
    submission_by_receipt,
    submission_row,
)
from app.queries.service import can_supervise, open_query, require_supervision
from app.returns.service import line_meta, versions_of

Action = Literal["accept", "query", "request_resubmission", "assign"]
TABS: tuple[str, ...] = ("all", "needs_review", "validating", "rejected", "queried", "accepted")


class SubmissionList(BaseModel):
    items: list[SubmissionRow]
    next_cursor: str | None
    counts: dict[str, int]


class Amendment(BaseModel):
    supersedes_receipt_id: str
    supersedes_received_at: str
    reason: str


class ValidationGroup(BaseModel):
    label: str
    passed: int
    total: int
    note: str


class ValidationDetail(BaseModel):
    rule_set: str
    ran_at: str
    duration_ms: int
    groups: list[ValidationGroup]
    issues: list[Issue]


class ContentLine(BaseModel):
    """One filed line. Figures are exact decimal strings; ``null`` is a blank cell."""

    code: str
    label: str
    value: str | None
    previous_version_value: str | None
    prior_period_value: str | None
    is_total: bool
    changed: bool
    #: The section (official sheet or table) the line belongs to.
    section: str | None = None


class ContentSection(BaseModel):
    code: str
    title: str
    unit: str


class Content(BaseModel):
    currency: str
    #: The whole return's unit, for returns that declare one; otherwise each section
    #: states its own.
    unit: str
    sections: list[ContentSection]
    lines: list[ContentLine]


class SignatureEvidence(BaseModel):
    valid: bool
    alg: str
    kid: str
    key_registered_at: str
    custody_note: str


class CertificateEvidence(BaseModel):
    valid: bool
    not_after: str


class ReceiptSignature(BaseModel):
    valid: bool
    kid: str


class Evidence(BaseModel):
    receipt_id: str
    payload_sha256: str
    signature: SignatureEvidence
    certificate: CertificateEvidence | None
    receipt_signature: ReceiptSignature
    received_at: str
    ledger_seq: int
    chain_intact: bool


class DeclarationOut(BaseModel):
    prepared_by_role: str
    approved_by_role: str
    approved_at: str
    software: str | None


class InstitutionPanel(BaseModel):
    code: str
    name: str
    type: str
    supervisor_name: str | None
    cycle_received: int
    cycle_due: int
    outstanding: list[str]


class VersionRow(BaseModel):
    version: int
    receipt_id: str
    received_at: str
    status: SubmissionState
    current: bool


class ActivityItem(BaseModel):
    at: str
    title: str
    detail: str


class FiledDocument(BaseModel):
    """A document filed with the return; its digest is in the signed manifest."""

    name: str
    media_type: str
    sha256: str
    bytes: int


class SubmissionDetail(BaseModel):
    row: SubmissionRow
    return_version: str
    basis: Literal["solo", "consolidated"]
    amendment: Amendment | None
    validation: ValidationDetail | None
    content: Content
    evidence: Evidence
    declaration: DeclarationOut
    institution: InstitutionPanel
    versions: list[VersionRow]
    activity: list[ActivityItem]
    allowed_actions: list[Action]
    documents: list[FiledDocument] = Field(default_factory=list)


class AcceptIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class ResubmissionIn(BaseModel):
    reason: str = Field(min_length=1, max_length=20000)
    due_at: datetime


class AssignIn(BaseModel):
    reviewer_subject: str = Field(min_length=1, max_length=120)


class NumberOut(BaseModel):
    number: str


# --- list ------------------------------------------------------------------------------


def _filtered(
    *,
    return_code: str | None,
    institution_code: str | None,
    reporting_date: str | None,
    channel: str | None,
    q: str | None,
) -> Select[Submission]:
    statement = select(Submission).where(Submission.environment == "production")
    if return_code:
        statement = statement.where(Submission.return_code == return_code)
    if reporting_date:
        statement = statement.where(
            Submission.reporting_date == datetime.fromisoformat(reporting_date).date()
        )
    if channel:
        statement = statement.where(Submission.channel == channel)
    if institution_code or q:
        statement = statement.join(Institution, Institution.id == Submission.institution_id)
        if institution_code:
            statement = statement.where(Institution.code == institution_code)
        if q:
            like = f"%{q.strip()}%"
            statement = statement.where(
                or_(
                    Institution.name.ilike(like),
                    Submission.receipt_id.ilike(like),
                    Submission.return_code.ilike(like),
                )
            )
    return statement


def _status_filter(statement: Select[Submission], status: str | None) -> Select[Submission]:
    if not status or status == "all":
        return statement
    if status == "needs_review":
        return statement.where(Submission.status == "in_review")
    return statement.where(Submission.status == status)


def _cursor(sub: Submission) -> str:
    return base64.urlsafe_b64encode(f"{iso(sub.received_at)}|{sub.receipt_id}".encode()).decode()


def list_submissions(
    session: Session,
    *,
    status: str | None,
    return_code: str | None,
    institution_code: str | None,
    reporting_date: str | None,
    channel: str | None,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> SubmissionList:
    base = _filtered(
        return_code=return_code,
        institution_code=institution_code,
        reporting_date=reporting_date,
        channel=channel,
        q=q,
    )
    counts: dict[str, int] = {}
    for tab in TABS:
        counted = (
            _status_filter(base, tab).with_only_columns(func.count(Submission.id)).order_by(None)
        )
        counts[tab] = int(session.scalar(counted) or 0)
    statement = _status_filter(base, status)
    if cursor:
        try:
            when_text, receipt = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            when = utc(datetime.fromisoformat(when_text.replace("Z", "+00:00")))
        except ValueError as exc:
            raise ApiError("invalid_request", "The cursor is not valid.") from exc
        statement = statement.where(
            or_(
                Submission.received_at < when,
                (Submission.received_at == when) & (Submission.receipt_id < receipt),
            )
        )
    page = list(
        session.scalars(
            statement.order_by(Submission.received_at.desc(), Submission.receipt_id.desc()).limit(
                limit + 1
            )
        )
    )
    items = page[:limit]
    return SubmissionList(
        items=rows(session, items),
        next_cursor=_cursor(items[-1]) if len(page) > limit and items else None,
        counts=counts,
    )


# --- detail ------------------------------------------------------------------------------


def _family(session: Session, sub: Submission) -> list[Submission]:
    return list(
        session.scalars(
            select(Submission)
            .where(
                Submission.institution_id == sub.institution_id,
                Submission.return_code == sub.return_code,
                Submission.reporting_date == sub.reporting_date,
                Submission.basis == sub.basis,
                Submission.environment == sub.environment,
            )
            .order_by(Submission.version)
        )
    )


def _prior_period(
    session: Session, sub: Submission, definition: ReturnDefinition
) -> Submission | None:
    previous = previous_reporting_date(definition.cadence, sub.reporting_date)
    candidates = session.scalars(
        select(Submission)
        .where(
            Submission.institution_id == sub.institution_id,
            Submission.return_code == sub.return_code,
            Submission.reporting_date == previous,
            Submission.basis == sub.basis,
            Submission.environment == sub.environment,
            Submission.status != "superseded",
        )
        .order_by(Submission.version.desc())
    ).all()
    return next((c for c in candidates if counts_as_filed(c)), None)


def _raw_lines(payload: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    if payload is None:
        return None
    raw = payload.get("lines")
    return raw if isinstance(raw, dict) else {}


def _shown(value: JsonValue) -> str | None:
    """A filed value as the portal shows it: exact text, ``None`` for a blank cell."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | str):
        return str(value)
    return canonicalize(value).decode()


def _same(left: JsonValue, right: JsonValue) -> bool:
    a, b = figure(left), figure(right)
    return a == b if a is not None and b is not None else left == right


def _content(
    schema: dict[str, JsonValue],
    payload: dict[str, JsonValue],
    previous: dict[str, JsonValue] | None,
    prior: dict[str, JsonValue] | None,
) -> tuple[list[ContentSection], list[ContentLine]]:
    """Every filed line, in the definition's order, grouped by section.

    Fixed lines come from the version's schema (label, total, section). Itemised rows
    (``<section>:<row>``, other columns ``<section>:<row>:<column>``) are the filer's own,
    labelled by the payload's ``labels``, and follow in section order.
    """
    now = _raw_lines(payload) or {}
    before = _raw_lines(previous)
    earlier = _raw_lines(prior)
    labels = labels_of(payload)
    sections: list[ContentSection] = []
    order: dict[str, int] = {}
    meta_sections = schema.get("x-srg-sections")
    for item in meta_sections if isinstance(meta_sections, list) else []:
        if isinstance(item, dict) and isinstance(item.get("code"), str):
            code = str(item.get("code"))
            order[code] = len(order)
            sections.append(
                ContentSection(
                    code=code,
                    title=str(item.get("title") or code),
                    unit=str(item.get("unit") or ""),
                )
            )
    line_section: dict[str, str] = {}
    meta_lines = schema.get("x-srg-lines")
    for item in meta_lines if isinstance(meta_lines, list) else []:
        if isinstance(item, dict) and isinstance(item.get("section"), str):
            line_section[str(item.get("code"))] = str(item.get("section"))

    def line(code: str, label: str, total: bool, section: str | None) -> ContentLine:
        value = now.get(code)
        previous_value = before.get(code) if before is not None else None
        return ContentLine(
            code=code,
            label=label,
            value=_shown(value),
            previous_version_value=_shown(previous_value),
            prior_period_value=_shown(earlier.get(code)) if earlier is not None else None,
            is_total=total,
            changed=before is not None and code in before and not _same(previous_value, value),
            section=section,
        )

    lines: list[ContentLine] = []
    fixed: set[str] = set()
    for code, label, total in line_meta(schema):
        fixed.add(code)
        lines.append(line(code, label, total, line_section.get(code)))
    itemised = [code for code in now if code not in fixed and ":" in code]
    itemised.sort(key=lambda code: order.get(code.split(":", 1)[0], len(order)))
    for code in itemised:
        section, rest = code.split(":", 1)
        row, _, column = rest.partition(":")
        label = labels.get(f"{section}:{row}", row)
        lines.append(
            line(code, f"{label} ({column.replace('_', ' ')})" if column else label, False, section)
        )
    return sections, lines


def _str(data: dict[str, JsonValue], name: str) -> str:
    value = data.get(name)
    return value if isinstance(value, str) else ""


def _evidence(session: Session, ctx: AppContext, sub: Submission) -> Evidence:
    key = session.scalars(select(SigningKey).where(SigningKey.kid == sub.signing_kid)).first()
    signature_ok = False
    if key is not None:
        try:
            parts = verify_compact(
                sub.signature, public_key_from_jwk(key.public_jwk), expected_typ="srg-manifest+jws"
            )
            signature_ok = parts.payload == canonicalize(sub.manifest)
        except (JwsError, JwkError):
            signature_ok = False
    receipt = session.scalars(select(Receipt).where(Receipt.submission_id == sub.id)).first()
    receipt_ok = False
    receipt_kid = receipt.kid if receipt else ""
    if receipt is not None:
        gateway_key = ctx.keys.find("receipt", receipt.kid)
        if gateway_key is not None:
            try:
                verify_compact(
                    receipt.receipt_jws, gateway_key.public_key, expected_typ=RECEIPT_TYP
                )
                receipt_ok = True
            except JwsError:
                receipt_ok = False
    entry = session.get(AuditEntry, sub.ledger_seq)
    entry_ok = False
    if entry is not None:
        previous = session.get(AuditEntry, entry.seq - 1) if entry.seq > 1 else None
        expected_prev = previous.entry_hash if previous is not None else "0" * 64
        entry_ok = (
            entry.prev_hash == expected_prev
            and compute_entry_hash(entry.prev_hash, entry_body(entry)) == entry.entry_hash
            and signature_valid(ctx.keys, entry)
            and entry.object_ref == sub.receipt_id
        )
    latest = session.scalars(
        select(AuditVerification).order_by(AuditVerification.verified_at.desc()).limit(1)
    ).first()
    chain_ok = entry_ok and (latest is None or latest.intact)
    certificate = None
    if sub.certificate_not_after is not None:
        certificate = CertificateEvidence(
            valid=sub.received_at <= sub.certificate_not_after,
            not_after=iso(sub.certificate_not_after),
        )
    return Evidence(
        receipt_id=sub.receipt_id,
        payload_sha256=sub.payload_sha256,
        signature=SignatureEvidence(
            valid=signature_ok,
            alg="ES256",
            kid=sub.signing_kid,
            key_registered_at=iso(key.registered_at) if key else "",
            custody_note=key.custody_note if key else "",
        ),
        certificate=certificate,
        receipt_signature=ReceiptSignature(valid=receipt_ok, kid=receipt_kid),
        received_at=iso(sub.received_at),
        ledger_seq=sub.ledger_seq,
        chain_intact=chain_ok,
    )


def _activity(
    session: Session, sub: Submission, institution: Institution, key: SigningKey | None
) -> list[ActivityItem]:
    query_numbers = list(session.scalars(select(Query.number).where(Query.submission_id == sub.id)))
    refs = [sub.receipt_id, *query_numbers]
    entries = session.scalars(
        select(AuditEntry)
        .where(AuditEntry.object_ref.in_(refs))
        .order_by(AuditEntry.seq.desc())
        .limit(200)
    ).all()
    items: list[ActivityItem] = []
    last_view_actor: str | None = None
    for entry in entries:
        at = iso(entry.occurred_at)
        detail = entry.detail
        if entry.category == "view":
            if entry.object_ref != sub.receipt_id or entry.actor_ref == last_view_actor:
                continue
            last_view_actor = entry.actor_ref
            items.append(
                ActivityItem(
                    at=at, title=f"Opened by {entry.actor_label}", detail="Submission detail"
                )
            )
            continue
        last_view_actor = None
        action = entry.action
        if (
            entry.category == "filing"
            and action.startswith("Filed")
            and entry.object_ref == sub.receipt_id
        ):
            amendment = sub.supersedes_receipt_id is not None
            items.append(
                ActivityItem(
                    at=at,
                    title="Receipt issued",
                    detail=f"Signed receipt returned to the institution in {_duration(sub.receipt_ms)}",
                )
            )
            registered = (
                f" · registered {key.registered_at.day} {key.registered_at:%b %Y}" if key else ""
            )
            items.append(
                ActivityItem(
                    at=at,
                    title="Signature and certificate verified"
                    if sub.certificate_thumbprint
                    else "Signature verified",
                    detail=f"Institution key {sub.signing_kid}{registered}",
                )
            )
            items.append(
                ActivityItem(
                    at=at,
                    title="Amendment received" if amendment else "Filing received",
                    detail=f"Supersedes {sub.supersedes_receipt_id} with stated reason"
                    if amendment
                    else f"{sub.return_code} v{sub.version} via {'the API' if sub.channel == 'api' else 'portal upload'}",
                )
            )
            continue
        if action == "Assigned reviewer":
            reviewer = _str(detail, "reviewer")
            rule = _str(detail, "rule")
            items.append(
                ActivityItem(
                    at=at,
                    title=f"Assigned to {reviewer}",
                    detail=f"Rule: {rule}" if rule else f"By {entry.actor_label}",
                )
            )
            continue
        if action.startswith("Validation passed") or action.startswith("Rejected filing"):
            passed = detail.get("passed")
            total = detail.get("total")
            rule_set = (sub.validation or {}).get("rule_set")
            items.append(
                ActivityItem(
                    at=at,
                    title=action
                    if action.startswith("Validation")
                    else f"Rejected at validation ({action.split(', ', 1)[-1]} failed)",
                    detail=f"{passed} of {total} rules · rule set {rule_set}",
                )
            )
            continue
        if action in ("Accepted filing", "Accepted filing automatically"):
            note = _str(detail, "note")
            items.append(
                ActivityItem(
                    at=at,
                    title=f"Accepted by {entry.actor_label}"
                    if entry.actor_type == "staff"
                    else "Accepted automatically",
                    detail=note,
                )
            )
            continue
        items.append(
            ActivityItem(
                at=at,
                title=action,
                detail=_str(detail, "subject") or _str(detail, "superseded_by") or "",
            )
        )
    return items


def _duration(ms: int) -> str:
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def _allowed(principal: StaffPrincipal, institution: Institution, sub: Submission) -> list[Action]:
    if principal.is_auditor or not principal.has("supervisor", "senior_supervisor"):
        return []
    allowed: list[Action] = []
    supervises = can_supervise(principal, institution, sub)
    if supervises and sub.status == "in_review":
        allowed.append("accept")
    if supervises and sub.status not in ("superseded", "validating", "received"):
        allowed.append("query")
    if supervises and sub.status in ("in_review", "queried", "rejected", "accepted"):
        allowed.append("request_resubmission")
    # The portal's assign action is "Assign to me": once the filing is the caller's,
    # there is nothing left for it to do.
    if (
        principal.has("senior_supervisor")
        and sub.status in ("in_review", "queried", "validating", "received")
        and sub.reviewer_subject != principal.subject
    ):
        allowed.append("assign")
    return allowed


def submission_detail(
    session: Session, ctx: AppContext, principal: StaffPrincipal, receipt_id: str
) -> SubmissionDetail:
    sub = submission_by_receipt(session, receipt_id)
    institution = session.get(Institution, sub.institution_id)
    assert institution is not None
    definition = session.scalars(
        select(ReturnDefinition).where(ReturnDefinition.code == sub.return_code)
    ).one()
    version = next(
        v for v in versions_of(session, definition.id) if v.version == sub.return_version
    )
    lookups = Lookups(session, [sub])
    family = _family(session, sub)
    superseded = next((f for f in family if f.id == sub.supersedes_id), None)
    prior = _prior_period(session, sub, definition)
    content_sections, content_lines = _content(
        version.schema,
        sub.payload,
        superseded.payload if superseded is not None else None,
        prior.payload if prior is not None else None,
    )
    validation = None
    if sub.validation is not None:
        groups_raw = sub.validation.get("groups")
        issues_raw = sub.validation.get("issues")
        validation = ValidationDetail(
            rule_set=_str(sub.validation, "rule_set"),
            ran_at=_str(sub.validation, "ran_at"),
            duration_ms=int_field(sub.validation, "duration_ms"),
            groups=[ValidationGroup.model_validate(g) for g in groups_raw]
            if isinstance(groups_raw, list)
            else [],
            issues=[Issue.model_validate(i) for i in issues_raw]
            if isinstance(issues_raw, list)
            else [],
        )
    declaration = sub.declaration
    software = declaration.get("software")
    software_text = None
    if isinstance(software, dict):
        software_text = f"{software.get('name', '')} {software.get('version', '')}".strip()
    cycle = compute_obligations(
        session,
        ctx.settings,
        [institution],
        sub.reporting_date,
        sub.reporting_date,
        ctx.clock.now(),
    )
    supervisor_name = None
    if institution.supervisor_subject:
        user = session.get(StaffUser, institution.supervisor_subject)
        supervisor_name = user.short_name if user else institution.supervisor_subject
    key = session.scalars(select(SigningKey).where(SigningKey.kid == sub.signing_kid)).first()
    detail = SubmissionDetail(
        row=submission_row(sub, lookups),
        return_version=sub.return_version,
        basis="consolidated" if sub.basis == "consolidated" else "solo",
        amendment=Amendment(
            supersedes_receipt_id=superseded.receipt_id,
            supersedes_received_at=iso(superseded.received_at),
            reason=_str(declaration, "amendment_reason"),
        )
        if superseded is not None
        else None,
        validation=validation,
        content=Content(
            currency=_str(sub.payload, "currency"),
            unit=_str(sub.payload, "unit"),
            sections=content_sections,
            lines=content_lines,
        ),
        evidence=_evidence(session, ctx, sub),
        declaration=DeclarationOut(
            prepared_by_role=_str(declaration, "prepared_by_role"),
            approved_by_role=_str(declaration, "approved_by_role"),
            approved_at=_str(declaration, "approved_at"),
            software=software_text,
        ),
        institution=InstitutionPanel(
            code=institution.code,
            name=institution.name,
            type=institution.type,
            supervisor_name=supervisor_name,
            cycle_received=sum(1 for r in cycle if r.status in ("filed", "filed_late")),
            cycle_due=len(cycle),
            outstanding=[r.definition.code for r in cycle if r.status in ("overdue", "due")],
        ),
        versions=[
            VersionRow(
                version=f.version,
                receipt_id=f.receipt_id,
                received_at=iso(f.received_at),
                status=state_of(f.status),
                current=f.status != "superseded",
            )
            for f in reversed(family)
        ],
        activity=[],
        allowed_actions=_allowed(principal, institution, sub),
        documents=[
            FiledDocument(
                name=document.name,
                media_type=document.media_type,
                sha256=document.sha256,
                bytes=document.bytes,
            )
            for document in session.scalars(
                select(SubmissionDocument)
                .where(SubmissionDocument.submission_id == sub.id)
                .order_by(SubmissionDocument.name)
            )
        ],
    )
    record_view(
        session,
        ctx,
        principal,
        action="Opened submission",
        object_type="submission",
        object_ref=sub.receipt_id,
        institution_code=institution.code,
    )
    detail.activity = _activity(session, sub, institution, key)
    return detail


# --- decisions ---------------------------------------------------------------------------


def accept(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    receipt_id: str,
    note: str | None,
    *,
    at: datetime | None = None,
) -> None:
    sub = submission_by_receipt(session, receipt_id)
    institution = session.get(Institution, sub.institution_id)
    assert institution is not None
    require_supervision(principal, institution, sub)
    if sub.status != "in_review":
        raise ApiError(
            "conflict",
            f"Only a filing in review can be accepted; {receipt_id} is {sub.status.replace('_', ' ')}.",
        )
    now = at or ctx.clock.now()
    sub.status = "accepted"
    sub.decided_at = now
    sub.decided_by = principal.subject
    sub.reviewer_subject = principal.subject
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="decision",
        at=now,
        action="Accepted filing",
        object_type="submission",
        object_ref=receipt_id,
        institution_code=institution.code,
        detail={"note": note},
    )


def request_resubmission(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    receipt_id: str,
    body: ResubmissionIn,
    *,
    at: datetime | None = None,
) -> str:
    sub = submission_by_receipt(session, receipt_id)
    institution = session.get(Institution, sub.institution_id)
    assert institution is not None
    require_supervision(principal, institution, sub)
    if sub.status in ("superseded", "validating", "received"):
        raise ApiError(
            "conflict", f"A resubmission cannot be requested for a filing that is {sub.status}."
        )
    query = open_query(
        session,
        ctx,
        submission=sub,
        institution=institution,
        kind="resubmission",
        subject=f"Resubmission requested: {sub.return_code} for {sub.reporting_date.day} {sub.reporting_date:%b %Y}",
        body=body.reason,
        due_at=body.due_at,
        actor=principal.actor,
        author="supervisor",
        author_label=principal.name,
        author_role="Supervisor",
        at=at,
    )
    return query.number


def assign(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    receipt_id: str,
    reviewer_subject: str,
) -> None:
    require_decider(principal, "senior_supervisor")
    sub = submission_by_receipt(session, receipt_id)
    institution = session.get(Institution, sub.institution_id)
    assert institution is not None
    reviewer = session.get(StaffUser, reviewer_subject)
    if reviewer is None or not ({"supervisor", "senior_supervisor"} & set(reviewer.roles)):
        raise ApiError(
            "invalid_request", "The reviewer must be a supervisor who has signed in to the portal."
        )
    if sub.status in ("accepted", "rejected", "superseded"):
        raise ApiError("conflict", f"{receipt_id} is {sub.status}; there is nothing to review.")
    sub.reviewer_subject = reviewer_subject
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="decision",
        action="Assigned reviewer",
        object_type="submission",
        object_ref=receipt_id,
        institution_code=institution.code,
        detail={"reviewer": reviewer.short_name, "reviewer_subject": reviewer_subject, "rule": ""},
    )
