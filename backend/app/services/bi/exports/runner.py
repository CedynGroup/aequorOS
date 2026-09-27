"""Compile, run and dress one governed export — the half both paths share.

The interactive route and the ``bi_export`` worker handler produce the same
artifact from the same inputs, and the second one runs where there is no
request, no ``Response`` and no ETag. Everything they agree on lives here: the
export caps, the table, and the provenance block.

What is deliberately NOT here: the authorization decision (the route and the
handler each make it, because each has its own principal and its own refusal
shape), the ``bi_query_log`` row (each path writes exactly one, describing the
event it is), and the audit event (``app/services/bi`` writes ``bi_*`` tables
and nothing else — the feature and the job own ``audit_events``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import Catalogue
from app.models import Bank, User
from app.schemas.bi import BiFilter, BiQuery
from app.services import jurisdictions
from app.services.bi import provenance
from app.services.bi.authorization import BiDataScope
from app.services.bi.compiler import CompiledQuery, compile_query
from app.services.bi.execution import QueryResult, execute
from app.services.bi.exports.context import (
    ExportContext,
    ExportTable,
    query_lines,
    table_from,
    window_label,
)
from app.services.bi.exports.policy import ExportClass

#: How a data scope reads on the artifact. Phase 1 authorizes only the whole
#: institution (``authorization.BiDataScope``), and Phase 4's branch and region
#: scopes are named here so a partial book can never print as the whole one.
SCOPE_LABELS: dict[str, str] = {
    "all": "Whole institution",
    "branch": "Branches",
    "region": "Regions",
}

#: What a principal is called on the artifact when the row has gone. Users are
#: never physically deleted, so this is a fallback rather than an expectation.
UNKNOWN_PRINCIPAL = "Unknown user"


def principal_label(db: Session, organization_id: str, principal_user_id: UUID) -> str:
    """The person the artifact is attributed to: their email, tenant-scoped."""

    email = db.scalar(
        select(User.email).where(
            User.id == principal_user_id, User.organization_id == organization_id
        )
    )
    return email or UNKNOWN_PRINCIPAL


def scope_label(scope: BiDataScope) -> str:
    """The caller's slice of the institution, in production copy."""

    heading = SCOPE_LABELS.get(scope.kind, scope.kind)
    if scope.kind == "all" or not scope.values:
        return heading
    return f"{heading}: {', '.join(scope.values)}"


def build_context(  # noqa: PLR0913 - the provenance block's own inputs, all explicit
    db: Session,
    *,
    bank: Bank,
    query: BiQuery,
    cat: Catalogue,
    data_scope: BiDataScope,
    export_class: ExportClass,
    user_label: str,
    window: tuple[date, date] | None = None,
) -> ExportContext:
    """The six provenance fields and everything else the renderers print."""

    covered = window if window is not None else provenance.data_window(query.time)
    verdict = provenance.trust_verdict(
        db, organization_id=bank.organization_id, bank_id=bank.id, window=covered
    )
    return ExportContext(
        institution_id=bank.id,
        institution_name=bank.name,
        unit=jurisdictions.base_currency(bank),
        query_lines=query_lines(cat, query),
        as_of_label=window_label(query),
        trust_status=verdict.status,
        failing_checks=verdict.failing_checks,
        catalogue_version=cat.version,
        data_scope_label=scope_label(data_scope),
        user_label=user_label,
        export_class=export_class,
        build_fingerprint=provenance.build_fingerprint(
            db, organization_id=bank.organization_id, bank_id=bank.id, window=covered
        ),
    )


@dataclass(frozen=True, slots=True)
class ExportRun:
    """One compiled, executed export: the statement's record and its rows."""

    compiled: CompiledQuery
    result: QueryResult

    @property
    def truncated(self) -> bool:
        return self.result.truncated

    @property
    def row_count(self) -> int:
        return len(self.result.rows)


def run_query(  # noqa: PLR0913 - compile and execute take their guards explicitly
    db: Session,
    *,
    cat: Catalogue,
    query: BiQuery,
    organization_id: str,
    bank_id: str,
    injected_filters: tuple[BiFilter, ...],
    row_cap: int,
    timeout_ms: int,
) -> ExportRun:
    """Compile and run under the EXPORT caps: the long timeout, the high cap."""

    compiled = compile_query(
        db,
        cat,
        query,
        organization_id=organization_id,
        bank_id=bank_id,
        injected_filters=injected_filters,
    )
    result = execute(db, compiled, timeout_ms=timeout_ms, row_cap=row_cap)
    return ExportRun(compiled=compiled, result=result)


def to_table(run: ExportRun, ctx: ExportContext) -> ExportTable:
    """The executed rows as an export table, amounts labelled in the bank's unit."""

    return table_from(run.result.columns, run.result.rows, truncated=run.truncated, unit=ctx.unit)


__all__ = [
    "SCOPE_LABELS",
    "UNKNOWN_PRINCIPAL",
    "ExportRun",
    "build_context",
    "principal_label",
    "run_query",
    "scope_label",
    "to_table",
]
