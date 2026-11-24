"""Return definitions registry: versions, drafts and the two-approver publication flow.

Separation of duties: a version's drafter and its two approvers are three different
people. Drafting needs ``returns_policy``. Approving needs ``returns_policy`` or, as the
Supervision approver the design shows, ``senior_supervisor``. Auditors decide nothing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import BaseModel, Field, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, append
from app.auth.staff import StaffPrincipal, require_decider
from app.core.clock import iso_or_none
from app.core.context import AppContext
from app.core.encoding import jcs_sha256
from app.core.errors import ApiError
from app.db.models import ReturnDefinition, ReturnVersion, ReturnVersionApproval
from app.returns.rules import Rule, dump_rules, parse_rules, rule_lines

Cadence = Literal["daily", "weekly", "monthly", "quarterly", "semiannual", "annual"]
CADENCES: tuple[Cadence, ...] = ("daily", "weekly", "monthly", "quarterly", "semiannual", "annual")
APPROVER_ROLES = ("returns_policy", "senior_supervisor")


class ReturnSummary(BaseModel):
    code: str
    name: str
    cadence: Cadence
    current_version: str
    applies_to: list[str]


class ReturnVersionOut(BaseModel):
    version: str
    status: Literal["draft", "published", "retired"]
    first_reporting_date: str
    last_reporting_date: str | None
    schema_sha256: str
    rule_count: int
    published_at: str | None


class ReturnDetail(ReturnSummary):
    versions: list[ReturnVersionOut]


class SchemaAndRules(BaseModel):
    schema_: dict[str, JsonValue] = Field(alias="schema", serialization_alias="schema")
    rules: list[Rule]

    model_config = {"populate_by_name": True}


class DraftIn(BaseModel):
    version: str = Field(pattern=r"^\d{4}\.\d{1,3}$")
    change_summary: str = Field(min_length=1, max_length=2000)
    first_reporting_date: date
    notice_days: int = Field(ge=0, le=730)
    schema_: dict[str, JsonValue] = Field(alias="schema")
    rules: list[Rule]

    model_config = {"populate_by_name": True}


class ReturnIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def cadence_of(value: str) -> Cadence:
    for cadence in CADENCES:
        if cadence == value:
            return cadence
    raise ValueError(f"unknown cadence {value}")


# --- lookups --------------------------------------------------------------------------


def definition(session: Session, code: str) -> ReturnDefinition | None:
    return session.scalars(select(ReturnDefinition).where(ReturnDefinition.code == code)).first()


def versions_of(session: Session, definition_id: str) -> list[ReturnVersion]:
    return list(
        session.scalars(
            select(ReturnVersion)
            .where(ReturnVersion.definition_id == definition_id)
            .order_by(ReturnVersion.first_reporting_date, ReturnVersion.drafted_at)
        )
    )


def published_versions(session: Session, definition_id: str) -> list[ReturnVersion]:
    return [v for v in versions_of(session, definition_id) if v.status == "published"]


def current_published(session: Session, definition_id: str) -> ReturnVersion | None:
    published = published_versions(session, definition_id)
    return published[-1] if published else None


def version_for_date(
    session: Session, definition_id: str, reporting_date: date
) -> ReturnVersion | None:
    for version in reversed(published_versions(session, definition_id)):
        if version.first_reporting_date <= reporting_date and (
            version.last_reporting_date is None or reporting_date <= version.last_reporting_date
        ):
            return version
    return None


def pending_draft(session: Session, definition_id: str) -> ReturnVersion | None:
    drafts = [v for v in versions_of(session, definition_id) if v.status == "draft"]
    return drafts[-1] if drafts else None


def all_definitions(session: Session) -> list[ReturnDefinition]:
    return list(
        session.scalars(
            select(ReturnDefinition).order_by(ReturnDefinition.position, ReturnDefinition.code)
        )
    )


def summary(session: Session, row: ReturnDefinition) -> ReturnSummary | None:
    current = current_published(session, row.id)
    if current is None:
        return None
    return ReturnSummary(
        code=row.code,
        name=row.name,
        cadence=cadence_of(row.cadence),
        current_version=current.version,
        applies_to=list(row.applies_to),
    )


def _version_status(value: str) -> Literal["draft", "published", "retired"]:
    if value == "published":
        return "published"
    if value == "retired":
        return "retired"
    return "draft"


def version_out(version: ReturnVersion) -> ReturnVersionOut:
    return ReturnVersionOut(
        version=version.version,
        status=_version_status(version.status),
        first_reporting_date=version.first_reporting_date.isoformat(),
        last_reporting_date=version.last_reporting_date.isoformat()
        if version.last_reporting_date
        else None,
        schema_sha256=version.schema_sha256,
        rule_count=version.rule_count,
        published_at=iso_or_none(version.published_at),
    )


def detail(
    session: Session, row: ReturnDefinition, *, include_drafts: bool = False
) -> ReturnDetail | None:
    base = summary(session, row)
    if base is None:
        return None
    versions = [
        version_out(v)
        for v in versions_of(session, row.id)
        if v.status in ("published", "retired") or (include_drafts and v.status == "draft")
    ]
    return ReturnDetail(
        code=base.code,
        name=base.name,
        cadence=base.cadence,
        current_version=base.current_version,
        applies_to=base.applies_to,
        versions=versions,
    )


# --- schema and rule handling -----------------------------------------------------------

_validators_lock = threading.Lock()
_validators: dict[str, Draft202012Validator] = {}


def schema_errors(version: ReturnVersion, instance: dict[str, JsonValue]) -> list[ValidationError]:
    """JSON Schema 2020-12 errors for ``instance``, ordered by location."""
    found: list[ValidationError] = list(validator_for(version).iter_errors(instance))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    found.sort(key=_error_path)
    return found


def _error_path(error: ValidationError) -> str:
    parts: list[object] = list(error.absolute_path)  # pyright: ignore[reportAny]
    return "/".join(str(part) for part in parts)


def validator_for(version: ReturnVersion) -> Draft202012Validator:
    with _validators_lock:
        cached = _validators.get(version.schema_sha256)
        if cached is None:
            cached = Draft202012Validator(version.schema)
            _validators[version.schema_sha256] = cached
        return cached


def rules_of(version: ReturnVersion) -> list[Rule]:
    return parse_rules(version.rules)


def line_meta(schema: dict[str, JsonValue]) -> list[tuple[str, str, bool]]:
    """``(code, label, is_total)`` in display order, from ``x-srg-lines`` or the schema."""
    meta = schema.get("x-srg-lines")
    out: list[tuple[str, str, bool]] = []
    if isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict):
                code = item.get("code")
                label = item.get("label")
                total = item.get("total")
                if isinstance(code, str):
                    out.append((code, label if isinstance(label, str) else code, total is True))
        if out:
            return out
    properties = schema.get("properties")
    lines = properties.get("lines") if isinstance(properties, dict) else None
    line_props = lines.get("properties") if isinstance(lines, dict) else None
    if isinstance(line_props, dict):
        for code, prop in line_props.items():
            title = prop.get("title") if isinstance(prop, dict) else None
            out.append((code, title if isinstance(title, str) else code, False))
    return out


def check_draft(schema: dict[str, JsonValue], rules: list[Rule]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ApiError(
            "invalid_request", f"The schema is not valid JSON Schema 2020-12: {exc.message}"
        ) from exc
    codes = {code for code, _, _ in line_meta(schema)}
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ApiError("invalid_request", "Rule ids must be unique.")
    for rule in rules:
        unknown = [line for line in rule_lines(rule) if line not in codes]
        if unknown:
            raise ApiError(
                "invalid_request", f"Rule {rule.id} refers to unknown line {unknown[0]}."
            )


# --- writes -------------------------------------------------------------------------------


def create_draft(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    code: str,
    body: DraftIn,
    *,
    at: datetime | None = None,
) -> ReturnVersion:
    require_decider(principal, "returns_policy")
    row = definition(session, code)
    if row is None:
        raise ApiError("not_found", f"No return {code}.")
    check_draft(body.schema_, body.rules)
    for existing in versions_of(session, row.id):
        if existing.version == body.version and existing.status != "returned":
            raise ApiError("conflict", f"{code} {body.version} already exists.")
    if pending_draft(session, row.id) is not None:
        raise ApiError("conflict", f"{code} already has a draft awaiting approval.")
    now = at or ctx.clock.now()
    rules_json = dump_rules(body.rules)
    version = ReturnVersion(
        definition_id=row.id,
        version=body.version,
        status="draft",
        first_reporting_date=body.first_reporting_date,
        last_reporting_date=None,
        schema=body.schema_,
        rules=rules_json,
        schema_sha256=jcs_sha256(body.schema_),
        rules_sha256=jcs_sha256(rules_json),
        rule_count=len(body.rules),
        change_summary=body.change_summary,
        notice_days=body.notice_days,
        breaking=_is_breaking(session, row.id, body.schema_),
        drafted_by=principal.subject,
        drafted_at=now,
        published_at=None,
        returned_reason=None,
    )
    session.add(version)
    session.flush()
    session.add(
        ReturnVersionApproval(
            version_id=version.id,
            step="drafted",
            actor_subject=principal.subject,
            actor_name=principal.short_name,
            role_label=_role_label(principal),
            at=now,
        )
    )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="configuration",
        at=now,
        action=f"Drafted {code} {body.version}",
        object_type="return_version",
        object_ref=f"{code} {body.version}",
        detail={
            "change_summary": body.change_summary,
            "first_reporting_date": body.first_reporting_date.isoformat(),
            "schema_sha256": version.schema_sha256,
            "rules_sha256": version.rules_sha256,
            "rule_count": version.rule_count,
        },
    )
    return version


def _role_label(principal: StaffPrincipal) -> str:
    return "Policy" if principal.has("returns_policy") else "Supervision"


def _is_breaking(session: Session, definition_id: str, schema: dict[str, JsonValue]) -> bool:
    """Breaking for API v1 when a previously accepted line disappears (additive is not)."""
    current = current_published(session, definition_id)
    if current is None:
        return False
    old = {code for code, _, _ in line_meta(current.schema)}
    new = {code for code, _, _ in line_meta(schema)}
    return not old <= new


def _find_version(
    session: Session, code: str, version: str
) -> tuple[ReturnDefinition, ReturnVersion]:
    row = definition(session, code)
    if row is None:
        raise ApiError("not_found", f"No return {code}.")
    for candidate in reversed(versions_of(session, row.id)):
        if candidate.version == version and candidate.status != "returned":
            return row, candidate
    raise ApiError("not_found", f"No version {version} of {code}.")


def approvals(session: Session, version_id: str) -> list[ReturnVersionApproval]:
    return list(
        session.scalars(
            select(ReturnVersionApproval)
            .where(ReturnVersionApproval.version_id == version_id)
            .order_by(ReturnVersionApproval.at)
        )
    )


@dataclass(frozen=True)
class ApprovalResult:
    published: bool


#: Who publishes the AequorOS catalogue baseline: the gateway itself, never a person.
CATALOGUE_SUBJECT = "aequoros-catalogue"
CATALOGUE_NAME = "AequorOS return catalogue"
CATALOGUE_ROLE = "Catalogue baseline"


def publish_baseline(
    session: Session,
    ctx: AppContext,
    code: str,
    body: DraftIn,
    *,
    at: datetime | None = None,
) -> ReturnVersion:
    """Publish ``body`` as the AequorOS catalogue baseline, recorded as exactly that.

    A gateway starts with the AequorOS-aligned definitions so institutions can file at
    once. No supervisor approved them, so none is named: the version is drafted and
    published by the catalogue, and the audit log says so. Every later version goes
    through the Bank's own drafting and two-approver workflow (:func:`create_draft`,
    :func:`approve`).
    """
    row = definition(session, code)
    if row is None:
        raise ApiError("not_found", f"No return {code}.")
    now = at or ctx.clock.now()
    rules_json = dump_rules(body.rules)
    version = ReturnVersion(
        definition_id=row.id,
        version=body.version,
        status="draft",
        first_reporting_date=body.first_reporting_date,
        last_reporting_date=None,
        schema=body.schema_,
        rules=rules_json,
        schema_sha256=jcs_sha256(body.schema_),
        rules_sha256=jcs_sha256(rules_json),
        rule_count=len(body.rules),
        change_summary=body.change_summary,
        notice_days=body.notice_days,
        breaking=_is_breaking(session, row.id, body.schema_),
        drafted_by=CATALOGUE_SUBJECT,
        drafted_at=now,
        published_at=None,
        returned_reason=None,
    )
    session.add(version)
    session.flush()
    session.add(
        ReturnVersionApproval(
            version_id=version.id,
            step="drafted",
            actor_subject=CATALOGUE_SUBJECT,
            actor_name=CATALOGUE_NAME,
            role_label=CATALOGUE_ROLE,
            at=now,
        )
    )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=SYSTEM,
        category="configuration",
        at=now,
        action=f"Loaded {code} {body.version} from the AequorOS return catalogue",
        object_type="return_version",
        object_ref=f"{code} {body.version}",
        detail={
            "basis": "AequorOS catalogue baseline; no supervisor approval is recorded",
            "change_summary": body.change_summary,
            "schema_sha256": version.schema_sha256,
            "rules_sha256": version.rules_sha256,
            "rule_count": version.rule_count,
        },
    )
    _publish(session, ctx, row, version, now)
    return version


def approve(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    code: str,
    version_text: str,
    *,
    at: datetime | None = None,
) -> ApprovalResult:
    require_decider(principal, *APPROVER_ROLES)
    row, version = _find_version(session, code, version_text)
    if version.status != "draft":
        raise ApiError("conflict", f"{code} {version_text} is not awaiting approval.")
    if version.drafted_by == principal.subject:
        raise ApiError("forbidden", "The drafter of a version cannot approve it.")
    given = [a for a in approvals(session, version.id) if a.step == "approved"]
    if any(a.actor_subject == principal.subject for a in given):
        raise ApiError("forbidden", "The two approvers must be different people.")
    now = at or ctx.clock.now()
    session.add(
        ReturnVersionApproval(
            version_id=version.id,
            step="approved",
            actor_subject=principal.subject,
            actor_name=principal.short_name,
            role_label=_role_label(principal),
            at=now,
        )
    )
    published = len(given) + 1 >= 2
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="configuration",
        at=now,
        action=f"Approved {code} {version_text}" + (" and published" if published else ""),
        object_type="return_version",
        object_ref=f"{code} {version_text}",
        detail={"approval": len(given) + 1, "schema_sha256": version.schema_sha256},
    )
    if published:
        _publish(session, ctx, row, version, now)
    return ApprovalResult(published=published)


def _publish(
    session: Session, ctx: AppContext, row: ReturnDefinition, version: ReturnVersion, now: datetime
) -> None:
    for other in published_versions(session, row.id):
        if other.id == version.id:
            continue
        if other.first_reporting_date < version.first_reporting_date and (
            other.last_reporting_date is None
            or other.last_reporting_date >= version.first_reporting_date
        ):
            other.last_reporting_date = version.first_reporting_date - timedelta(days=1)
    version.status = "published"
    version.published_at = now
    signers = [
        f"{a.actor_name} ({a.role_label})"
        for a in approvals(session, version.id)
        if a.step == "approved"
    ]
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=SYSTEM,
        category="configuration",
        at=now,
        action=f"Published {row.code} {version.version}",
        object_type="return_version",
        object_ref=f"{row.code} {version.version}",
        detail={
            "approvers": list[JsonValue](signers),
            "schema_sha256": version.schema_sha256,
            "rules_sha256": version.rules_sha256,
            "first_reporting_date": version.first_reporting_date.isoformat(),
            "notice_days": version.notice_days,
        },
    )


def return_to_drafter(
    session: Session,
    ctx: AppContext,
    principal: StaffPrincipal,
    code: str,
    version_text: str,
    reason: str,
) -> None:
    require_decider(principal, *APPROVER_ROLES)
    _, version = _find_version(session, code, version_text)
    if version.status != "draft":
        raise ApiError("conflict", f"{code} {version_text} is not awaiting approval.")
    if version.drafted_by == principal.subject:
        raise ApiError("forbidden", "The drafter cannot return their own draft; an approver does.")
    version.status = "returned"
    version.returned_reason = reason
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=principal.actor,
        category="configuration",
        action=f"Returned {code} {version_text} to the drafter",
        object_type="return_version",
        object_ref=f"{code} {version_text}",
        detail={"reason": reason},
    )
