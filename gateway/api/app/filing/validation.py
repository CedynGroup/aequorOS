"""Validation after receipt: the version's rules, an amendment diff note, then the status
transition (``rejected`` / ``in_review`` / automatic acceptance) and reviewer assignment."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, Actor, append
from app.core.clock import add_business_days, iso
from app.core.context import AppContext
from app.core.errors import Issue
from app.db.models import Institution, ReturnDefinition, StaffUser, Submission
from app.filing.obligations import counts_as_filed, previous_reporting_date
from app.queries.service import open_automatic_resubmission
from app.returns.rules import (
    GROUP_ORDER,
    GROUPS,
    CrossEqualRule,
    Evaluation,
    RuleContext,
    evaluate,
    fmt,
    group_label,
)
from app.returns.service import line_meta, rules_of, versions_of

AMENDMENT_RULE = "AMENDMENT"
ASSIGNMENT_ACTOR = Actor("system", "assignment-rule", "System", "Assignment rule")
VALIDATION_ACTOR = Actor("system", "validation-service", "System", "Validation service")


_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def figure(value: JsonValue) -> Decimal | None:
    """A line's numeric value: an integer, a decimal string, or blank (``null``) as zero.

    ``None`` for anything else (text, which a definition may allow on a line that
    no arithmetic reads).
    """
    if value is None:
        return Decimal(0)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str) and _DECIMAL.match(value):
        return Decimal(value)
    return None


def lines_of(payload: Mapping[str, JsonValue]) -> dict[str, Decimal]:
    """Every numeric line of a payload, blank lines as zero."""
    raw = payload.get("lines")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Decimal] = {}
    for code, value in raw.items():
        number = figure(value)
        if number is not None:
            out[code] = number
    return out


def codes_of(payload: Mapping[str, JsonValue]) -> frozenset[str]:
    raw = payload.get("lines")
    return frozenset(raw) if isinstance(raw, dict) else frozenset()


def labels_of(payload: Mapping[str, JsonValue]) -> dict[str, str]:
    """Filer-supplied labels for itemised rows (``<section>:<row>``)."""
    raw = payload.get("labels")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str)}


def _current_filed(
    session: Session, sub: Submission, return_code: str, reporting: date
) -> Submission | None:
    rows = session.scalars(
        select(Submission)
        .where(
            Submission.institution_id == sub.institution_id,
            Submission.return_code == return_code,
            Submission.reporting_date == reporting,
            Submission.basis == sub.basis,
            Submission.environment == sub.environment,
            Submission.status != "superseded",
            Submission.id != sub.id,
        )
        .order_by(Submission.version.desc())
    ).all()
    for row in rows:
        if counts_as_filed(row):
            return row
    return None


def _date_label(day: date) -> str:
    return f"{day.day} {day:%b %Y}"


def _amendment_issue(
    sub: Submission, previous: Submission, labels: Mapping[str, str]
) -> Issue | None:
    now_lines = lines_of(sub.payload)
    before = lines_of(previous.payload)
    changed = [code for code in now_lines if before.get(code) != now_lines[code]]
    if not changed:
        return None
    zero = Decimal(0)
    parts = [
        f"{code} {labels.get(code, '')} "
        f"{'+' if now_lines[code] - before.get(code, zero) >= 0 else '−'}"
        f"{fmt(abs(now_lines[code] - before.get(code, zero)))}".replace("  ", " ")
        for code in changed[:6]
    ]
    more = f" and {len(changed) - 6} more" if len(changed) > 6 else ""
    return Issue(
        rule_id=AMENDMENT_RULE,
        severity="warning",
        pointer=f"/lines/{changed[0]}",
        message=f"{len(changed)} line{'s' if len(changed) != 1 else ''} differ from version "
        f"{previous.version}: {'; '.join(parts)}{more}. Not blocking; the institution's reason is "
        "attached to the amendment.",
    )


def _groups(evaluation: Evaluation, rules_other: dict[str, set[str]]) -> list[JsonValue]:
    out: list[JsonValue] = []
    for group in GROUP_ORDER:
        members = [o for o in evaluation.outcomes if o.group == group]
        if not members:
            continue
        note = GROUPS[group][1] if group in GROUPS else ""
        others = sorted(rules_other.get(group, set()))
        if group == "XR" and others:
            note = (
                "Against " + " and ".join(others)
                if len(others) <= 2
                else "Against " + ", ".join(others)
            )
        out.append(
            {
                "label": group_label(group),
                "passed": sum(1 for o in members if o.status == "passed"),
                "total": len(members),
                "note": note,
            }
        )
    return out


def run_validation(
    session: Session, ctx: AppContext, sub: Submission, *, at: datetime | None = None
) -> None:
    started = time.monotonic()
    now = at or ctx.clock.now()
    definition = session.scalars(
        select(ReturnDefinition).where(ReturnDefinition.code == sub.return_code)
    ).one()
    version = next(
        v for v in versions_of(session, definition.id) if v.version == sub.return_version
    )
    rules = rules_of(version)
    meta = line_meta(version.schema)
    labels = {code: label for code, label, _ in meta}
    previous_date = previous_reporting_date(definition.cadence, sub.reporting_date)
    previous_filing = _current_filed(session, sub, sub.return_code, previous_date)

    def other_lines(code: str) -> Mapping[str, Decimal] | None:
        other = _current_filed(session, sub, code, sub.reporting_date)
        return lines_of(other.payload) if other is not None else None

    labels = {**labels_of(sub.payload), **labels}
    rule_ctx = RuleContext(
        lines=lines_of(sub.payload),
        labels=labels,
        codes=codes_of(sub.payload),
        unit_label="",
        other_lines=other_lines,
        previous_lines=lines_of(previous_filing.payload) if previous_filing is not None else None,
        previous_label=_date_label(previous_date),
        reporting_label=_date_label(sub.reporting_date),
    )
    evaluation = evaluate(rules, rule_ctx)
    issues = evaluation.issues
    if sub.supersedes_id is not None:
        superseded = session.get(Submission, sub.supersedes_id)
        if superseded is not None:
            note = _amendment_issue(sub, superseded, labels)
            if note is not None:
                issues.append(note)
    other_returns: dict[str, set[str]] = {}
    for rule in rules:
        if isinstance(rule, CrossEqualRule):
            other_returns.setdefault("XR", set()).add(rule.other_return)
    failed = evaluation.failed
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    duration_ms = max(1, int((time.monotonic() - started) * 1000))
    sub.validation = {
        "rule_set": f"{definition.code} {version.version}",
        "ran_at": iso(now),
        "duration_ms": duration_ms,
        "total": evaluation.total,
        "passed": evaluation.passed,
        "failed": failed,
        "warnings": warnings,
        "groups": _groups(evaluation, other_returns),
        "issues": [cast("JsonValue", issue.model_dump()) for issue in issues],
    }
    sub.validated_at = now
    institution = session.get(Institution, sub.institution_id)
    assert institution is not None
    if sub.status == "superseded":
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=VALIDATION_ACTOR,
            category="system",
            at=now,
            action="Validated a superseded version",
            object_type="submission",
            object_ref=sub.receipt_id,
            institution_code=institution.code,
            detail={"failed": failed, "warnings": warnings},
        )
        return
    if failed:
        sub.status = "rejected"
        sub.decided_at = now
        sub.decided_by = "system"
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=VALIDATION_ACTOR,
            category="system",
            at=now,
            action=f"Rejected filing, {failed} rule{'s' if failed != 1 else ''}",
            object_type="submission",
            object_ref=sub.receipt_id,
            institution_code=institution.code,
            detail={
                "failed": failed,
                "warnings": warnings,
                "passed": evaluation.passed,
                "total": evaluation.total,
                "failed_rules": [i.rule_id for i in issues if i.severity == "error"],
            },
        )
        failed_messages = [i.message for i in issues if i.severity == "error"]
        due = datetime.combine(
            add_business_days(now.date(), ctx.settings.resubmission_business_days),
            datetime.min.time().replace(hour=17),
            tzinfo=now.tzinfo,
        )
        open_automatic_resubmission(session, ctx, sub, institution, failed_messages, due, at=now)
        return
    summary = (
        f"Validation passed with {warnings} warning{'s' if warnings != 1 else ''}"
        if warnings
        else "Validation passed"
    )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=VALIDATION_ACTOR,
        category="system",
        at=now,
        action=summary,
        object_type="submission",
        object_ref=sub.receipt_id,
        institution_code=institution.code,
        detail={
            "passed": evaluation.passed,
            "total": evaluation.total,
            "warnings": warnings,
            "rule_set": f"{definition.code} {version.version}",
            "duration_ms": duration_ms,
        },
    )
    if definition.auto_accept and warnings == 0:
        sub.status = "accepted"
        sub.decided_at = now
        sub.decided_by = "system"
        sub.reviewer_subject = "system"
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=SYSTEM,
            category="decision",
            at=now,
            action="Accepted filing automatically",
            object_type="submission",
            object_ref=sub.receipt_id,
            institution_code=institution.code,
            detail={"rule": f"{definition.code} clean filings are accepted automatically"},
        )
        return
    sub.status = "in_review"
    assign_relationship_supervisor(session, ctx, sub, institution, at=now + timedelta(seconds=0))


def assign_relationship_supervisor(
    session: Session,
    ctx: AppContext,
    sub: Submission,
    institution: Institution,
    *,
    at: datetime | None = None,
) -> None:
    subject = institution.supervisor_subject
    if not subject or sub.reviewer_subject:
        return
    sub.reviewer_subject = subject
    user = session.get(StaffUser, subject)
    name = user.short_name if user is not None else subject
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=ASSIGNMENT_ACTOR,
        category="system",
        at=at,
        action="Assigned reviewer",
        object_type="submission",
        object_ref=sub.receipt_id,
        institution_code=institution.code,
        detail={
            "reviewer": name,
            "reviewer_subject": subject,
            "rule": f"relationship supervisor for {institution.name}",
        },
    )


def validate_submission(ctx: AppContext, submission_id: str) -> None:
    """Background task: validate one receipted filing in its own transaction."""
    with ctx.db.session() as session:
        sub = session.get(Submission, submission_id, with_for_update=ctx.db.postgres)
        if sub is None or sub.validated_at is not None:
            return
        run_validation(session, ctx, sub)


def validate_pending(ctx: AppContext, *, older_than_seconds: int) -> int:
    cutoff = ctx.clock.now() - timedelta(seconds=older_than_seconds)
    with ctx.db.session() as session:
        ids = list(
            session.scalars(
                select(Submission.id).where(
                    Submission.status == "validating",
                    Submission.validated_at.is_(None),
                    Submission.received_at <= cutoff,
                )
            )
        )
    for submission_id in ids:
        validate_submission(ctx, submission_id)
    return len(ids)
