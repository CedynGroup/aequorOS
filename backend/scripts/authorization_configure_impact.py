"""Read-only configure cutover eligibility and exact-grant inventory.

Run from backend: uv run python -m scripts.authorization_configure_impact
  --organization OR-XXXXXXXX --output-dir /outside/repo/dated-impact

Uses WORKER_DATABASE_URL then DATABASE_URL, or --database-url. Without --output-dir
prints the combined JSON. No email, credential, grant reason or secret is exported.
See docs/configurator_impact.md for baseline and eligibility limitations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    InstitutionScope,
    Module,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
)
from app.core.security import has_role
from app.identity.service import authorization
from app.models import AuthorizationBinding, Bank, Organization, User
from scripts.authorization_configure_routes import CONFIGURE_ROUTES, ConfigureRoute


def _date(value: datetime | None) -> str | None:
    """Serialize optional validity timestamps consistently in UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()


@dataclass(frozen=True)
class PrincipalInventory:
    organization_id: str
    principal_id: str
    principal_type: str
    active: bool
    scalar_role: str


@dataclass(frozen=True)
class ExistingGrant:
    organization_id: str
    principal_id: str
    principal_type: str
    binding_id: str
    role_bundle: str
    institution_scope: str
    institution_id: str | None
    module_scope: str
    sensitivity_scope: str
    data_scope_kind: str
    data_scope_values: list[str]
    status: str
    valid_from: str | None
    valid_until: str | None
    revoked_at: str | None
    effective: bool


@dataclass(frozen=True)
class RequiredGrant:
    organization_id: str
    principal_id: str
    principal_type: str
    institution_scope: str
    institution_id: str | None
    module_scope: str
    sensitivity_scope: str = "restricted"
    role_bundle: str = "configurator"
    data_scope_kind: str = "all"
    status: str = "active"
    approval_required: bool = True


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str
    matching_binding_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigureImpact:
    organization_id: str
    principal_id: str
    principal_type: str
    method: str
    path: str
    institution_scope: str
    institution_id: str | None
    module: str
    before: Eligibility
    after: Eligibility
    required_grant: RequiredGrant | None
    sensitivity: str = "restricted"
    permission: str = "configure"

    @property
    def newly_denied(self) -> bool:
        """Identify a lost eligibility, not a failed HTTP request."""
        return self.before.allowed and not self.after.allowed


@dataclass
class ConfigureReport:
    generated_at: str
    principals: list[PrincipalInventory] = field(default_factory=list)
    existing_grants: list[ExistingGrant] = field(default_factory=list)
    impacts: list[ConfigureImpact] = field(default_factory=list)

    @property
    def evaluation_failed(self) -> bool:
        """Keep any incomplete evaluator result visible in the aggregate."""
        return any(
            "evaluation_failed" in (row.before.reason, row.after.reason) for row in self.impacts
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize inventory and decisions with their assessment limitations."""
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "assessment": "pre-cutover dependency versus proposed configure eligibility",
            "limitations": [
                "Not HTTP success: visibility, entitlements and workflow conditions still apply.",
                "Required grants need operator approval. No grants are created or backfilled.",
                "Machines cannot hold Configurator or call these human-only routes.",
            ],
            "evaluation_failed": self.evaluation_failed,
            "routes": [asdict(route) for route in CONFIGURE_ROUTES],
            "principals": [asdict(row) for row in self.principals],
            "existing_grants": [asdict(row) for row in self.existing_grants],
            "impacts": [{**asdict(row), "newly_denied": row.newly_denied} for row in self.impacts],
        }


def _eligibility(
    principal: PrincipalLocator,
    resource: ResourceLocator,
    bindings: list[AuthorizationBinding],
    now: datetime,
    *,
    owner_only: bool = False,
) -> Eligibility:
    """Evaluate complete bindings and whole-institution scope, failing closed."""
    try:
        decision = authorization.evaluate_prefetched_permission(
            principal,
            Permission.ADMINISTER if owner_only else Permission.CONFIGURE,
            resource,
            [row for row in bindings if row.role_bundle == RoleBundle.ORG_OWNER]
            if owner_only
            else bindings,
            principal_active=True,
            now=now,
        )
        # Match runtime's whole-institution requirement, using only THIS
        # resource's matches. A broad grant on a different resource cannot help.
        matched = set(decision.matching_binding_ids)
        whole = any(row.id in matched and row.data_scope_kind == DataScope.ALL for row in bindings)
        allowed = decision.allowed and whole
        return Eligibility(
            allowed,
            decision.reason
            if not decision.allowed or whole
            else "institution_grain_requires_whole_institution",
            sorted(str(value) for value in decision.matching_binding_ids),
        )
    except Exception:
        # An incomplete assessment must be visibly failed, never an inferred allow.
        return Eligibility(False, "evaluation_failed")


def _impact(
    user: User,
    route: ConfigureRoute,
    bank_id: str | None,
    bindings: list[AuthorizationBinding],
    now: datetime,
) -> ConfigureImpact:
    """Compare baseline and proposed eligibility for one principal and target."""
    kind = PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN
    principal = PrincipalLocator(user.organization_id, user.id, kind)
    resource = ResourceLocator(
        user.organization_id,
        route.institution_scope,
        bank_id,
        route.module,
        Sensitivity.RESTRICTED,
    )
    if not user.is_active or kind is PrincipalType.MACHINE:
        before = after = Eligibility(
            False, "principal_not_active" if not user.is_active else "human_session_required"
        )
    else:
        if route.baseline == "org_owner":
            account = ResourceLocator(
                user.organization_id,
                InstitutionScope.ORGANIZATION,
                None,
                Module.ACCOUNT,
                Sensitivity.RESTRICTED,
            )
            before = _eligibility(principal, account, bindings, now, owner_only=True)
        else:
            before = Eligibility(
                has_role([user.role], route.baseline), "legacy_scalar_" + route.baseline
            )
        after = _eligibility(principal, resource, bindings, now)
    required = None
    if (
        user.is_active
        and kind is PrincipalType.HUMAN
        and not after.allowed
        and after.reason != "evaluation_failed"
        and (
            not after.matching_binding_ids
            or after.reason == "institution_grain_requires_whole_institution"
        )
    ):
        required = RequiredGrant(
            user.organization_id,
            str(user.id),
            kind.value,
            route.institution_scope.value,
            bank_id,
            route.module.value,
        )
    return ConfigureImpact(
        user.organization_id,
        str(user.id),
        kind.value,
        route.method,
        route.path,
        route.institution_scope.value,
        bank_id,
        route.module.value,
        before,
        after,
        required,
    )


def build_configure_report(
    db: Session,
    *,
    organization_id: str,
    now: datetime | None = None,
) -> ConfigureReport:
    """Read all tenant principals and lifecycle rows; evaluate complete bindings."""
    moment = now or datetime.now(UTC)
    report = ConfigureReport(generated_at=_date(moment) or "")
    with db.no_autoflush:
        if db.scalar(select(Organization.id).where(Organization.id == organization_id)) is None:
            raise ValueError("organization not found")
        banks = list(
            db.scalars(
                select(Bank.id).where(Bank.organization_id == organization_id).order_by(Bank.id)
            )
        )
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
        for user in users:
            report.principals.append(
                PrincipalInventory(
                    organization_id,
                    str(user.id),
                    "machine" if user.auth_provider == "service" else "human",
                    user.is_active,
                    user.role,
                )
            )
            own_bindings = [row for row in bindings if row.principal_user_id == user.id]
            for route in CONFIGURE_ROUTES:
                targets: list[str | None] = (
                    list(banks)
                    if route.institution_scope is InstitutionScope.INSTITUTION
                    else [None]
                )
                report.impacts.extend(
                    _impact(user, route, bank, own_bindings, moment) for bank in targets
                )
    return report


def write_artifacts(report: ConfigureReport, directory: Path) -> None:
    """Create three dated, private artifacts; refuse to overwrite prior evidence."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    common = {key: value for key, value in report.to_dict().items() if key != "impacts"}
    paths = [directory / f"configure-{stage}.json" for stage in ("before", "after", "exact-grants")]
    if any(path.exists() for path in paths):
        raise FileExistsError("impact artifacts already exist; choose a new dated directory")
    for stage in ("before", "after"):
        payload = {
            **common,
            "stage": stage,
            "outcomes": [
                _target_fields(row)
                | {
                    "eligibility": asdict(row.before if stage == "before" else row.after),
                    "newly_denied": row.newly_denied,
                }
                for row in report.impacts
            ],
        }
        _write_json(directory / f"configure-{stage}.json", payload)
    _write_json(
        directory / "configure-exact-grants.json",
        {
            "generated_at": report.generated_at,
            "evaluation_failed": report.evaluation_failed,
            "principals": common["principals"],
            "existing_grants": common["existing_grants"],
            "required_grants": [
                {"method": row.method, "path": row.path, **asdict(row.required_grant)}
                for row in report.impacts
                if row.required_grant is not None
            ],
        },
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Create private evidence without overwriting an existing artifact."""

    def private_opener(name: str, flags: int) -> int:
        return os.open(name, flags, 0o600)

    with open(path, "x", encoding="utf-8", opener=private_opener) as out:
        _ = out.write(json.dumps(payload, indent=2) + "\n")


def _target_fields(row: ConfigureImpact) -> dict[str, object]:
    """Select resource identity without embedding either eligibility decision."""
    values: dict[str, object] = asdict(row)
    return {
        key: value
        for key, value in values.items()
        if key not in ("before", "after", "required_grant")
    }


class _Args(argparse.Namespace):
    database_url: str | None = None
    organization: str | None = None
    output_dir: Path | None = None


def _organizations(engine: Engine, organization_id: str | None) -> list[str]:
    """Select the requested tenant or fail if enumeration cannot see any tenant."""
    if organization_id is not None:
        return [organization_id]
    with Session(engine, autoflush=False) as db:
        organizations = list(db.scalars(select(Organization.id).order_by(Organization.id)))
    if not organizations:
        raise ValueError("no organizations visible to this database role")
    return organizations


def main(argv: list[str] | None = None) -> int:
    """Assess visible tenants read-only and report incomplete runs without secrets."""
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
    report = ConfigureReport(generated_at=_date(moment) or "")
    try:
        engine = create_engine(url)
        for organization_id in _organizations(engine, args.organization):
            with Session(engine, autoflush=False) as db:
                db.info["organization_id"] = organization_id
                if engine.dialect.name == "postgresql":
                    _ = db.execute(text("SET TRANSACTION READ ONLY"))
                tenant = build_configure_report(db, organization_id=organization_id, now=moment)
                report.principals.extend(tenant.principals)
                report.existing_grants.extend(tenant.existing_grants)
                report.impacts.extend(tenant.impacts)
        if args.output_dir is None:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            write_artifacts(report, args.output_dir)
        return 2 if report.evaluation_failed else 0
    except Exception:
        # Driver exceptions can contain credentials. Never print their message or URL.
        print("configure impact failed; no complete assessment produced", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
