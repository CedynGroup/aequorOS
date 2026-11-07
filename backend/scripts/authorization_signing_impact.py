"""Read-only Signer cutover deny-impact and exact-grant inventory.

Run from backend: uv run python -m scripts.authorization_signing_impact
  --organization OR-XXXXXXXX --output-dir /outside/repo/dated-signing-impact

Uses WORKER_DATABASE_URL then DATABASE_URL, or --database-url. Without
--output-dir prints combined JSON. No email, display name, grant reason or secret
is exported. Reserved synthetic E2E namespaces are reduced to role labels.
See docs/signer_impact.md for baseline and eligibility limitations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

# Standalone execution must register the same tenant RLS listener as the API.
import app.db.session  # noqa: F401  # pyright: ignore[reportUnusedImport]
from app.core.authorization import (
    DataScope,
    InstitutionScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
)
from app.identity.service import authorization
from app.models import AuthorizationBinding, Bank, Organization, User
from scripts.authorization_signing_routes import SIGNING_ROUTES, SigningRoute


class ImpactInputError(Exception):
    """Operator-correctable failure whose fixed message is safe to print."""


def _date(value: datetime | None) -> str | None:
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
    synthetic_role: str | None = None


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
class Eligibility:
    allowed: bool
    reason: str
    matching_binding_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RequiredGrant:
    organization_id: str
    principal_id: str
    principal_type: str
    institution_scope: str
    institution_id: str
    module_scope: str
    sensitivity_scope: str
    role_bundle: str
    permission: str
    purpose: str
    data_scope_kind: str = "all"
    status: str = "active"
    approval_required: bool = True


@dataclass(frozen=True)
class SigningImpact:
    organization_id: str
    principal_id: str
    principal_type: str
    route_key: str
    method: str
    path: str
    institution_id: str
    signing_role: str
    source_family: str
    module: str
    before: Eligibility
    after: Eligibility
    source_read: Eligibility
    sign_off: Eligibility
    checker_approve: Eligibility | None
    required_grants: list[RequiredGrant]
    sensitivity: str = "restricted"
    permission: str = "sign_off"

    @property
    def newly_denied(self) -> bool:
        """Identify lost policy eligibility, not a claimed HTTP response."""
        return self.before.allowed and not self.after.allowed


@dataclass
class SigningReport:
    generated_at: str
    principals: list[PrincipalInventory] = field(default_factory=list)
    existing_grants: list[ExistingGrant] = field(default_factory=list)
    impacts: list[SigningImpact] = field(default_factory=list)

    @property
    def evaluation_failed(self) -> bool:
        for row in self.impacts:
            decisions = (
                row.before,
                row.after,
                row.source_read,
                row.sign_off,
                row.checker_approve,
            )
            if any(
                decision is not None and decision.reason == "evaluation_failed"
                for decision in decisions
            ):
                return True
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "assessment": "pre-Signer policy versus exact-bank signing eligibility",
            "limitations": [
                "Not HTTP success: routing, entitlements, signing state and payload "
                "validity still apply.",
                "Structural SIGN_OFF and APPROVE do not fabricate maker/checker, "
                "digest or step-up evidence.",
                "Required grants need operator approval and SoD review. No grants "
                "are created or backfilled.",
                "Machines cannot call these human-only routes and receive no grant "
                "recommendations.",
                "E2E labels cover only the reserved aequoros.example bootstrap and "
                "journey namespaces.",
            ],
            "evaluation_failed": self.evaluation_failed,
            "routes": [asdict(route) for route in SIGNING_ROUTES],
            "principals": [asdict(row) for row in self.principals],
            "existing_grants": [asdict(row) for row in self.existing_grants],
            "impacts": [{**asdict(row), "newly_denied": row.newly_denied} for row in self.impacts],
        }


def _eligibility(  # noqa: PLR0913 - one complete policy sentence is explicit
    principal: PrincipalLocator,
    permission: Permission,
    resource: ResourceLocator,
    bindings: list[AuthorizationBinding],
    now: datetime,
    *,
    exact_institution: bool,
) -> Eligibility:
    """Evaluate one complete structural sentence and its whole-bank scope."""
    try:
        decision = authorization.evaluate_prefetched_structural_permission(
            principal,
            permission,
            resource,
            bindings,
            principal_active=True,
            now=now,
        )
        matched = set(decision.matching_binding_ids)
        whole = any(row.id in matched and row.data_scope_kind == DataScope.ALL for row in bindings)
        exact = any(
            row.id in matched
            and row.institution_scope == InstitutionScope.INSTITUTION.value
            and row.institution_id == resource.institution_id
            for row in bindings
        )
        allowed = decision.allowed and whole and (exact or not exact_institution)
        reason = decision.reason
        if decision.allowed and not whole:
            reason = "institution_grain_requires_whole_institution"
        elif decision.allowed and exact_institution and not exact:
            reason = "signing_requires_exact_institution_binding"
        return Eligibility(
            allowed,
            reason,
            sorted(str(value) for value in decision.matching_binding_ids),
        )
    except Exception:
        return Eligibility(False, "evaluation_failed")


def _combined(
    source: Eligibility,
    action: Eligibility,
    checker: Eligibility | None = None,
) -> Eligibility:
    decisions = (source, action, checker)
    if any(
        decision is not None and decision.reason == "evaluation_failed" for decision in decisions
    ):
        return Eligibility(False, "evaluation_failed")
    for label, decision in zip(
        ("source_read", "signing_action", "checker_approve"), decisions, strict=True
    ):
        if decision is not None and not decision.allowed:
            return Eligibility(False, f"{label}:{decision.reason}", decision.matching_binding_ids)
    matching = sorted(
        {
            binding_id
            for decision in decisions
            if decision is not None
            for binding_id in decision.matching_binding_ids
        }
    )
    return Eligibility(True, "structurally_eligible", matching)


def _required_grant(
    user: User,
    route: SigningRoute,
    bank_id: str,
    *,
    purpose: str,
) -> RequiredGrant:
    if purpose == "source_read":
        return RequiredGrant(
            user.organization_id,
            str(user.id),
            PrincipalType.HUMAN.value,
            InstitutionScope.INSTITUTION.value,
            bank_id,
            route.source_module.value,
            route.source_sensitivity.value,
            RoleBundle.VIEWER.value,
            Permission.VIEW.value,
            purpose,
        )
    checker = purpose == "checker_approve"
    return RequiredGrant(
        user.organization_id,
        str(user.id),
        PrincipalType.HUMAN.value,
        InstitutionScope.INSTITUTION.value,
        bank_id,
        route.module.value,
        Sensitivity.RESTRICTED.value,
        RoleBundle.APPROVER.value if checker else RoleBundle.SIGNER.value,
        Permission.APPROVE.value if checker else Permission.SIGN_OFF.value,
        purpose,
    )


def _impact(
    user: User,
    route: SigningRoute,
    bank_id: str,
    bindings: list[AuthorizationBinding],
    now: datetime,
) -> SigningImpact:
    kind = PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN
    principal = PrincipalLocator(user.organization_id, user.id, kind)
    source_resource = ResourceLocator(
        user.organization_id,
        InstitutionScope.INSTITUTION,
        bank_id,
        route.source_module,
        route.source_sensitivity,
    )
    signing_resource = ResourceLocator(
        user.organization_id,
        InstitutionScope.INSTITUTION,
        bank_id,
        route.module,
        Sensitivity.RESTRICTED,
    )
    if not user.is_active or kind is PrincipalType.MACHINE:
        denied = Eligibility(
            False, "principal_not_active" if not user.is_active else "human_session_required"
        )
        before = after = source_read = sign_off = denied
        checker = denied if route.requires_checker_approval else None
        required: list[RequiredGrant] = []
    else:
        source_read = _eligibility(
            principal,
            Permission.VIEW,
            source_resource,
            bindings,
            now,
            exact_institution=False,
        )
        if route.baseline == "route_not_present":
            before = Eligibility(False, "route_not_present_before_this_change")
        elif route.baseline == "source_view":
            before = _combined(source_read, Eligibility(True, "legacy_source_view"))
        else:
            legacy_permission = route.legacy_permission
            assert legacy_permission is not None
            legacy = _eligibility(
                principal,
                legacy_permission,
                source_resource,
                bindings,
                now,
                exact_institution=False,
            )
            before = _combined(source_read, legacy)
        sign_off = _eligibility(
            principal,
            Permission.SIGN_OFF,
            signing_resource,
            bindings,
            now,
            exact_institution=True,
        )
        checker = (
            _eligibility(
                principal,
                Permission.APPROVE,
                signing_resource,
                bindings,
                now,
                exact_institution=True,
            )
            if route.requires_checker_approval
            else None
        )
        after = _combined(source_read, sign_off, checker)
        required = []
        for purpose, decision in (
            ("source_read", source_read),
            ("sign_off", sign_off),
            ("checker_approve", checker),
        ):
            if (
                decision is not None
                and not decision.allowed
                and decision.reason != "evaluation_failed"
            ):
                required.append(_required_grant(user, route, bank_id, purpose=purpose))
    return SigningImpact(
        user.organization_id,
        str(user.id),
        kind.value,
        route.route_key,
        route.method,
        route.path,
        bank_id,
        route.signing_role,
        route.source_family,
        route.module.value,
        before,
        after,
        source_read,
        sign_off,
        checker,
        required,
    )


def _synthetic_role(email: str) -> str | None:
    """Label only known disposable identity namespaces; never export the email."""
    local, separator, domain = email.rpartition("@")
    if not separator or domain != "aequoros.example":
        return None
    if local.startswith("e2e."):
        role = local.removeprefix("e2e.")
        return role or None
    if not local.startswith("journey."):
        return None
    role, separator, suffix = local.removeprefix("journey.").rpartition(".")
    if not separator or len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
        return None
    return role or None


def build_signing_report(
    db: Session,
    *,
    organization_id: str,
    now: datetime | None = None,
) -> SigningReport:
    """Read one tenant's principals and grants without changing persisted state."""
    moment = now or datetime.now(UTC)
    report = SigningReport(generated_at=_date(moment) or "")
    with db.no_autoflush:
        if db.scalar(select(Organization.id).where(Organization.id == organization_id)) is None:
            raise ImpactInputError("organization not found")
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
                    _synthetic_role(user.email),
                )
            )
            own_bindings = [row for row in bindings if row.principal_user_id == user.id]
            for route in SIGNING_ROUTES:
                report.impacts.extend(
                    _impact(user, route, bank_id, own_bindings, moment) for bank_id in banks
                )
    return report


def _target_fields(row: SigningImpact) -> dict[str, object]:
    values: dict[str, object] = asdict(row)
    excluded = {
        "before",
        "after",
        "source_read",
        "sign_off",
        "checker_approve",
        "required_grants",
    }
    return {key: value for key, value in values.items() if key not in excluded}


def write_signing_artifacts(report: SigningReport, directory: Path) -> None:
    """Create private before/after/grant evidence without overwriting prior files."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    common = {key: value for key, value in report.to_dict().items() if key != "impacts"}
    paths = [directory / f"signing-{stage}.json" for stage in ("before", "after", "exact-grants")]
    if any(path.exists() for path in paths):
        raise ImpactInputError("impact artifacts already exist; choose a new dated directory")
    for stage in ("before", "after"):
        payload = {
            **common,
            "stage": stage,
            "outcomes": [
                _target_fields(row)
                | {
                    "eligibility": asdict(row.before if stage == "before" else row.after),
                    "source_read": asdict(row.source_read),
                    "sign_off": asdict(row.sign_off),
                    "checker_approve": (
                        asdict(row.checker_approve) if row.checker_approve is not None else None
                    ),
                    "newly_denied": row.newly_denied,
                }
                for row in report.impacts
            ],
        }
        _write_json(directory / f"signing-{stage}.json", payload)
    _write_json(
        directory / "signing-exact-grants.json",
        {
            "generated_at": report.generated_at,
            "evaluation_failed": report.evaluation_failed,
            "principals": common["principals"],
            "existing_grants": common["existing_grants"],
            "routes": common["routes"],
            "required_grants": [
                {
                    "route_key": row.route_key,
                    "method": row.method,
                    "path": row.path,
                    "signing_role": row.signing_role,
                    "source_family": row.source_family,
                    **asdict(grant),
                }
                for row in report.impacts
                for grant in row.required_grants
            ],
        },
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    def private_opener(name: str, flags: int) -> int:
        return os.open(name, flags, 0o600)

    with open(path, "x", encoding="utf-8", opener=private_opener) as out:
        _ = out.write(json.dumps(payload, indent=2) + "\n")


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
    """Assess visible tenants read-only and never print credentials or exceptions."""
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
    report = SigningReport(generated_at=_date(moment) or "")
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
                tenant = build_signing_report(db, organization_id=organization_id, now=moment)
                report.principals.extend(tenant.principals)
                report.existing_grants.extend(tenant.existing_grants)
                report.impacts.extend(tenant.impacts)
        if args.output_dir is None:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            write_signing_artifacts(report, args.output_dir)
        return 2 if report.evaluation_failed else 0
    except ImpactInputError as exc:
        print(f"signing impact failed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("signing impact failed; no complete assessment produced", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
