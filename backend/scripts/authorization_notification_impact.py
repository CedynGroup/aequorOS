"""Read-only notifications/jobs cutover deny-impact and exact-grant inventory.

Run from backend: uv run python -m scripts.authorization_notification_impact
  --organization OR-XXXXXXXX --output-dir /outside/repo/dated-notif-impact

Uses WORKER_DATABASE_URL then DATABASE_URL, or --database-url. Without
--output-dir prints combined JSON. No email, credential or grant reason is
exported. The report counts the stored rows each principal would have seen
before #186 and sees under the current read filters — the new locator-view
filter plus the pre-existing return-family factor for filing notifications,
both applied exactly as the production read paths apply them — then names
the least-scope VIEWER grants an operator could approve to restore visibility
where a grant can actually do that. Legacy locator-less rows and worker/system
provenance jobs are inventoried but never receive a grant candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Engine, create_engine, false, func, or_, select, true
from sqlalchemy.orm import Session

# Standalone execution must register the tenant RLS listener, as the API does.
import app.db.session  # noqa: F401  # pyright: ignore[reportUnusedImport]
from app.core.authorization import (
    BindingGrant,
    BindingStatus,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.locator_visibility import locator_visibility
from app.core.tenancy import TenantContext
from app.identity.public import load_permission_grants
from app.identity.service import authorization
from app.models import AuthorizationBinding, Job, Notification, Organization, User
from app.notifications.public import notifications as notifications_service
from app.services.jobs import tenant_visibility_condition
from scripts.authorization_configure_impact import ExistingGrant

NOTIF_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/notifications"),
    ("POST", "/api/v1/notifications/{notification_id}/read"),
    ("POST", "/api/v1/notifications/read-all"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs/{job_id}"),
)


class ImpactInputError(Exception):
    """Operator-correctable failure whose fixed message is safe to print."""


@dataclass(frozen=True)
class NotifPrincipal:
    organization_id: str
    principal_id: str
    principal_type: str
    active: bool
    scalar_role: str
    identity_origin: str
    e2e_profile: str | None


@dataclass(frozen=True)
class RequiredGrant:
    organization_id: str
    principal_id: str
    principal_type: str
    institution_scope: str
    institution_id: str | None
    module_scope: str
    sensitivity_scope: str
    role_bundle: str
    permission: str
    purpose: str
    data_scope_kind: str = "all"
    status: str = "active"
    approval_required: bool = True


@dataclass(frozen=True)
class VisibilityImpact:
    organization_id: str
    principal_id: str
    principal_type: str
    surface: str
    institution_scope: str | None
    bank_id: str | None
    module: str | None
    sensitivity: str | None
    before_count: int
    after_count: int
    required_grants: list[RequiredGrant] = field(default_factory=list)
    assessment_error: str | None = None

    @property
    def newly_hidden(self) -> bool:
        """Rows this principal previously saw that the locator filter now refuses."""
        return self.assessment_error is None and self.before_count > self.after_count


@dataclass
class NotifImpactReport:
    generated_at: str
    principals: list[NotifPrincipal] = field(default_factory=list)
    existing_grants: list[ExistingGrant] = field(default_factory=list)
    impacts: list[VisibilityImpact] = field(default_factory=list)

    @property
    def evaluation_failed(self) -> bool:
        """Any failed evaluation makes the complete assessment unusable."""
        return any(row.assessment_error is not None for row in self.impacts)

    def to_dict(self) -> dict[str, object]:
        """Serialize the inventory without turning counts into disclosure proof."""
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "assessment": (
                "stored notification and job visibility before versus after the #186 "
                "locator-view read filter, with the pre-existing return-family factor "
                "for filing notifications applied on both sides"
            ),
            "limitations": [
                "Not HTTP evidence: session and tenant RLS still apply on the live API. "
                "The measured factors are the new locator-view filter and the "
                "pre-existing return-family factor for filing notifications, each "
                "applied exactly as the production read paths apply them.",
                "Required grants are candidates for operator approval; none are created here.",
                "Legacy locator-less rows and worker/system-provenance jobs can never be "
                "granted back into visibility; no backfill by inference.",
                "Organization-scoped rows become visible only through organization-scope "
                "view bindings; institution-scoped rows need a grant naming that bank.",
                "Machine principals and inactive users receive no grant candidates.",
            ],
            "evaluation_failed": self.evaluation_failed,
            "routes": [{"method": method, "path": path} for method, path in NOTIF_ROUTES],
            "principals": [asdict(row) for row in self.principals],
            "existing_grants": [asdict(row) for row in self.existing_grants],
            "impacts": [{**asdict(row), "newly_hidden": row.newly_hidden} for row in self.impacts],
        }


@dataclass(frozen=True)
class _Locator:
    """One distinct persisted locator tuple; every-None means a legacy row."""

    institution_scope: str | None
    bank_id: str | None
    module: str | None
    sensitivity: str | None

    @property
    def grantable(self) -> bool:
        """Whether a least-scope grant matching exactly this locator can exist."""
        if self.institution_scope is None or self.module is None or self.sensitivity is None:
            return False
        return (
            self.institution_scope == InstitutionScope.ORGANIZATION.value
            or self.bank_id is not None
        )


def _date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()


def _e2e_profile(user: User) -> str | None:
    """Recognize only the repository's synthetic namespaces; export no address."""
    email = user.email
    if email.startswith("e2e.") and email.endswith("@aequoros.example"):
        return email.removeprefix("e2e.").split("@", maxsplit=1)[0]
    if email.startswith("journey.") and email.endswith("@aequoros.example"):
        parts = email.split("@", maxsplit=1)[0].split(".")
        return parts[1] if len(parts) >= 3 else None
    return None


def _locator_conditions(
    model: type[Notification] | type[Job], locator: _Locator
) -> list[ColumnElement[bool]]:
    """Exact equality for every locator dimension; None matches SQL NULL."""
    conditions: list[ColumnElement[bool]] = []
    for column, value in (
        (model.institution_scope, locator.institution_scope),
        (model.bank_id, locator.bank_id),
        (model.module, locator.module),
        (model.sensitivity, locator.sensitivity),
    ):
        conditions.append(column.is_(None) if value is None else column == value)
    return conditions


def _notification_count(  # noqa: PLR0913 - recipient, tenant, family and locator are one count
    db: Session,
    organization_id: str,
    principal_id: UUID,
    locator: _Locator,
    grants: tuple[BindingGrant, ...] | None,
    family: ColumnElement[bool],
) -> int:
    """Rows visible under the recipient, tenant and family factors, plus the filter."""
    conditions: list[ColumnElement[bool]] = [
        Notification.organization_id == organization_id,
        or_(
            Notification.recipient_user_id.is_(None),
            Notification.recipient_user_id == principal_id,
        ),
        # The pre-existing return-family factor, exactly as the production read
        # path applies it (notifications service visibility_conditions): a row
        # it hides was never API-visible, on either side of the cutover.
        family,
        *_locator_conditions(Notification, locator),
    ]
    if grants is not None:
        conditions.append(
            locator_visibility(
                grants,
                Permission.VIEW,
                institution_scope=Notification.institution_scope,
                bank_id=Notification.bank_id,
                module=Notification.module,
                sensitivity=Notification.sensitivity,
            )
        )
    return db.scalar(select(func.count()).select_from(Notification).where(*conditions)) or 0


def _job_count(
    db: Session,
    organization_id: str,
    locator: _Locator,
    grants: tuple[BindingGrant, ...] | None,
) -> int:
    """Tenant queue rows with this locator, plus the tenant-visibility filter if given."""
    conditions: list[ColumnElement[bool]] = [
        Job.organization_id == organization_id,
        *_locator_conditions(Job, locator),
    ]
    if grants is not None:
        conditions.append(tenant_visibility_condition(grants))
    return db.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0


def _candidate_grant(user: User, locator: _Locator) -> BindingGrant:
    """One synthetic least-scope VIEWER grant, used only to simulate visibility."""
    institution_scope = InstitutionScope(locator.institution_scope)
    return BindingGrant(
        binding_id=uuid4(),
        organization_id=user.organization_id,
        principal_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        institution_scope=institution_scope,
        institution_id=(
            locator.bank_id if institution_scope is InstitutionScope.INSTITUTION else None
        ),
        module_scope=ModuleScope(locator.module),
        sensitivity_scope=SensitivityScope(locator.sensitivity),
        status=BindingStatus.ACTIVE,
        valid_from=datetime.now(UTC) - timedelta(days=1),
        valid_until=None,
        revoked_at=None,
    )


def _required_grants(  # noqa: PLR0913 - one candidate needs user, locator and both counts
    db: Session,
    organization_id: str,
    user: User,
    surface: str,
    locator: _Locator,
    grants: tuple[BindingGrant, ...],
    before_count: int,
    after_count: int,
    family: ColumnElement[bool],
) -> list[RequiredGrant]:
    """Name the least-scope viewer grant only when it would actually restore rows."""
    if (
        not user.is_active
        or user.auth_provider == "service"
        or not locator.grantable
        or not before_count > after_count
    ):
        return []
    candidate = _candidate_grant(user, locator)
    if surface == "notifications":
        restored = _notification_count(
            db, organization_id, user.id, locator, (*grants, candidate), family
        )
    else:
        restored = _job_count(db, organization_id, locator, (*grants, candidate))
    if restored <= after_count:
        return []
    return [
        RequiredGrant(
            organization_id,
            str(user.id),
            PrincipalType.HUMAN.value,
            InstitutionScope(locator.institution_scope).value,
            locator.bank_id
            if InstitutionScope(locator.institution_scope) is InstitutionScope.INSTITUTION
            else None,
            locator.module or "",
            locator.sensitivity or "",
            RoleBundle.VIEWER.value,
            Permission.VIEW.value,
            f"{surface}_visibility",
        )
    ]


def _tenant_locators(
    db: Session, model: type[Notification] | type[Job], organization_id: str
) -> list[_Locator]:
    """Every persisted locator tuple of one tenant, legacy NULL tuples included."""
    rows = db.execute(
        select(
            model.institution_scope,
            model.bank_id,
            model.module,
            model.sensitivity,
        )
        .where(model.organization_id == organization_id)
        .distinct()
    ).all()
    # SQLAlchemy row objects unpack positionally; the tuple shape is the locator.
    return [
        _Locator(*cast("tuple[str | None, str | None, str | None, str | None]", row))
        for row in rows
    ]


def build_notification_impact_report(
    db: Session, *, organization_id: str, now: datetime | None = None
) -> NotifImpactReport:
    """Read one tenant's principals, rows and grants without changing state."""
    moment = now or datetime.now(UTC)
    report = NotifImpactReport(generated_at=_date(moment) or "")
    with db.no_autoflush:
        if db.scalar(select(Organization.id).where(Organization.id == organization_id)) is None:
            raise ImpactInputError("organization not found")
        users = list(
            db.scalars(
                select(User).where(User.organization_id == organization_id).order_by(User.id)
            )
        )
        bindings = list(
            db.scalars(
                select(AuthorizationBinding)
                .where(AuthorizationBinding.organization_id == organization_id)
                .order_by(AuthorizationBinding.id)
            )
        )
        for row in bindings:
            report.existing_grants.append(
                ExistingGrant(
                    row.organization_id,
                    str(row.principal_user_id),
                    row.principal_type,
                    str(row.id),
                    row.role_bundle,
                    row.institution_scope,
                    row.institution_id,
                    row.module_scope,
                    row.sensitivity_scope,
                    row.data_scope_kind,
                    sorted(row.data_scope_values or []),
                    row.status,
                    _date(row.valid_from),
                    _date(row.valid_until),
                    _date(row.revoked_at),
                    authorization.binding_is_effective(row, now=moment),
                )
            )
        notif_locators = _tenant_locators(db, Notification, organization_id)
        job_locators = _tenant_locators(db, Job, organization_id)
        for user in users:
            kind = PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN
            profile = _e2e_profile(user)
            report.principals.append(
                NotifPrincipal(
                    organization_id,
                    str(user.id),
                    kind.value,
                    user.is_active,
                    user.role,
                    "e2e" if profile is not None else "tenant",
                    profile,
                )
            )
            grants: tuple[BindingGrant, ...]
            family: ColumnElement[bool]
            error: str | None = None
            try:
                principal = TenantContext(
                    organization_id=organization_id,
                    actor_user_id=user.id,
                    authorization_version=None if kind is PrincipalType.MACHINE else 1,
                )
                grants = load_permission_grants(db, principal, Permission.VIEW)
                # The production family factor for this principal (the same
                # function the notification read path applies), so family-hidden
                # rows are hidden on both sides of the cutover and candidates are
                # only recommended when the simulated recount truly restores.
                family = notifications_service.family_visibility_condition(db, principal)
            except Exception:
                grants = ()
                family = false()
                error = "evaluation_failed"
            for locator in notif_locators:
                before = _notification_count(db, organization_id, user.id, locator, None, family)
                after = _notification_count(db, organization_id, user.id, locator, grants, family)
                required = (
                    []
                    if error is not None
                    else _required_grants(
                        db,
                        organization_id,
                        user,
                        "notifications",
                        locator,
                        grants,
                        before,
                        after,
                        family,
                    )
                )
                report.impacts.append(
                    VisibilityImpact(
                        organization_id,
                        str(user.id),
                        kind.value,
                        "notifications",
                        locator.institution_scope,
                        locator.bank_id,
                        locator.module,
                        locator.sensitivity,
                        before,
                        after,
                        required,
                        assessment_error=error,
                    )
                )
            for locator in job_locators:
                before = _job_count(db, organization_id, locator, None)
                after = _job_count(db, organization_id, locator, grants)
                required = (
                    []
                    if error is not None
                    else _required_grants(
                        db,
                        organization_id,
                        user,
                        "jobs",
                        locator,
                        grants,
                        before,
                        after,
                        # Queue rows carry no return-family factor.
                        true(),
                    )
                )
                report.impacts.append(
                    VisibilityImpact(
                        organization_id,
                        str(user.id),
                        kind.value,
                        "jobs",
                        locator.institution_scope,
                        locator.bank_id,
                        locator.module,
                        locator.sensitivity,
                        before,
                        after,
                        required,
                        assessment_error=error,
                    )
                )
    return report


def _impact_fields(row: VisibilityImpact) -> dict[str, object]:
    values: dict[str, object] = asdict(row)
    return {key: value for key, value in values.items() if key != "required_grants"}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    def private_opener(name: str, flags: int) -> int:
        return os.open(name, flags, 0o600)

    with open(path, "x", encoding="utf-8", opener=private_opener) as out:
        _ = out.write(json.dumps(payload, indent=2) + "\n")


def write_artifacts(report: NotifImpactReport, directory: Path) -> None:
    """Create private before/after/exact-grant files; never overwrite evidence."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    common = {key: value for key, value in report.to_dict().items() if key != "impacts"}
    paths = [directory / f"notif-{stage}.json" for stage in ("before", "after", "exact-grants")]
    if any(path.exists() for path in paths):
        raise ImpactInputError("impact artifacts already exist; choose a new dated directory")
    for stage in ("before", "after"):
        _write_json(
            directory / f"notif-{stage}.json",
            {
                **common,
                "stage": stage,
                "outcomes": [
                    _impact_fields(row)
                    | {
                        "before_count": row.before_count,
                        "after_count": row.after_count,
                        "newly_hidden": row.newly_hidden,
                    }
                    for row in report.impacts
                ],
            },
        )
    _write_json(
        directory / "notif-exact-grants.json",
        {
            **common,
            "required_grants": [
                {**asdict(grant), "surface": row.surface}
                for row in report.impacts
                for grant in row.required_grants
            ],
        },
    )


class _Args(argparse.Namespace):
    database_url: str | None = None
    organization: str | None = None
    output_dir: Path | None = None


def _organizations(engine: Engine, organization_id: str | None) -> list[str]:
    if organization_id is not None:
        return [organization_id]
    with Session(engine, autoflush=False) as db:
        organizations = list(db.scalars(select(Organization.id).order_by(Organization.id)))
    if not organizations:
        raise ImpactInputError("no organizations visible to this database role")
    return organizations


def main(argv: list[str] | None = None) -> int:
    """Assess visible tenants read-only; never approve an incomplete inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--database-url")
    _ = parser.add_argument("--organization")
    _ = parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv, namespace=_Args())
    url = args.database_url or os.getenv("WORKER_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        parser.error("no database URL: pass --database-url or set WORKER_DATABASE_URL")
    engine: Engine | None = None
    moment = datetime.now(UTC)
    report = NotifImpactReport(generated_at=_date(moment) or "")
    try:
        engine = create_engine(url)
        assessment_engine = (
            engine.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            if engine.dialect.name == "postgresql"
            else engine
        )
        for organization_id in _organizations(engine, args.organization):
            with Session(assessment_engine, autoflush=False) as db:
                db.info["organization_id"] = organization_id
                tenant = build_notification_impact_report(
                    db, organization_id=organization_id, now=moment
                )
                report.principals.extend(tenant.principals)
                report.existing_grants.extend(tenant.existing_grants)
                report.impacts.extend(tenant.impacts)
        if args.output_dir is None:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            write_artifacts(report, args.output_dir)
        return 2 if report.evaluation_failed else 0
    except ImpactInputError as exc:
        print(f"notification impact failed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Driver exceptions may contain credentials; do not print their details.
        print("notification impact failed; no complete assessment produced", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
