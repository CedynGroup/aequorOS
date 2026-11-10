"""Read-only Signer cutover deny-impact and exact-grant inventory.

Run from backend: uv run python -m scripts.authorization_signoff_impact
  --organization OR-XXXXXXXX --output-dir /outside/repo/dated-signoff-impact

Uses WORKER_DATABASE_URL then DATABASE_URL, or --database-url. Without
--output-dir prints combined JSON. No email, credential or grant reason is
exported. See docs/signer_impact.md for the assessment boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

# Standalone execution must register the tenant RLS listener, as the API does.
import app.db.session  # noqa: F401  # pyright: ignore[reportUnusedImport]
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
from app.models import (
    AuthorizationBinding,
    Bank,
    EnterpriseStressSignoff,
    Organization,
    PackageSignatureRecipient,
    RegulatoryPackage,
    User,
)
from scripts.authorization_configure_impact import ExistingGrant

_PACKAGE_BASE = "/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/attestation"
_STRESS_BASE = "/api/v1/banks/{bank_id}/enterprise-stress/signoffs/{signoff_id}"
SIGNOFF_ROUTES: dict[str, tuple[tuple[str, str], ...]] = {
    Module.REGULATORY.value: (
        ("GET", f"{_PACKAGE_BASE}/preview"),
        ("POST", f"{_PACKAGE_BASE}/step-up"),
        ("POST", f"{_PACKAGE_BASE}/certify"),
        ("POST", f"{_PACKAGE_BASE}/certify-and-send"),
    ),
    Module.RISK.value: (
        ("POST", f"{_STRESS_BASE}/step-up"),
        ("POST", f"{_STRESS_BASE}/attest"),
    ),
}


class ImpactInputError(Exception):
    """Operator-correctable failure whose fixed message is safe to print."""


@dataclass(frozen=True)
class SignOffPrincipal:
    organization_id: str
    principal_id: str
    principal_type: str
    active: bool
    scalar_role: str
    identity_origin: str
    e2e_profile: str | None


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
class SignOffImpact:
    organization_id: str
    principal_id: str
    principal_type: str
    institution_id: str
    module: str
    legacy_cohorts: list[str]
    source_families: list[str]
    named_roles: list[str]
    before: Eligibility
    after: Eligibility
    required_grants: list[RequiredGrant]
    assessment_error: str | None = None
    sensitivity: str = "restricted"
    permission: str = "sign_off"

    @property
    def newly_denied(self) -> bool:
        """A legacy-eligible signer who lacks the new exact Signer sentence."""
        return self.before.allowed and not self.after.allowed

    @property
    def named_or_legacy_signer(self) -> bool:
        """Named pending recipients are inventoried even if already unauthorized."""
        return self.before.allowed or bool(self.named_roles)


@dataclass
class SignOffReport:
    generated_at: str
    principals: list[SignOffPrincipal] = field(default_factory=list)
    existing_grants: list[ExistingGrant] = field(default_factory=list)
    impacts: list[SignOffImpact] = field(default_factory=list)

    @property
    def evaluation_failed(self) -> bool:
        """Any failed decision makes the complete assessment unusable."""
        return any(
            row.assessment_error is not None
            or "evaluation_failed" in (row.before.reason, row.after.reason)
            for row in self.impacts
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the inventory without converting eligibility into execution proof."""
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "assessment": "legacy signing eligibility versus exact-bank Signer eligibility",
            "limitations": [
                "Not HTTP success: routing, maker-checker, digest, step-up, key custody and "
                "object visibility still apply.",
                "Required grants are candidates for operator approval; none are created here.",
                "Machines and inactive principals cannot sign and receive no grant candidates.",
                "Approver-or-executive cohorts share the legacy APPROVE permission; named "
                "pending routing distinguishes the actual signing role where it exists.",
                "Source READ and checker APPROVE are independent grants, never implied by Signer.",
            ],
            "evaluation_failed": self.evaluation_failed,
            "routes": [
                {"module": module, "method": method, "path": path}
                for module, routes in SIGNOFF_ROUTES.items()
                for method, path in routes
            ],
            "principals": [asdict(row) for row in self.principals],
            "existing_grants": [asdict(row) for row in self.existing_grants],
            "impacts": [
                {
                    **asdict(row),
                    "newly_denied": row.newly_denied,
                    "named_or_legacy_signer": row.named_or_legacy_signer,
                }
                for row in self.impacts
            ],
        }


@dataclass
class _NamedRoutes:
    roles: set[str] = field(default_factory=set)
    source_families: set[str] = field(default_factory=set)


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


def _named_routes(db: Session, organization_id: str) -> dict[tuple[UUID, str], _NamedRoutes]:
    """Pending current-cycle recipients, including Board/executive slots."""
    result: dict[tuple[UUID, str], _NamedRoutes] = {}
    rows = db.execute(
        select(
            PackageSignatureRecipient.recipient_user_id,
            RegulatoryPackage.bank_id,
            PackageSignatureRecipient.signing_role,
            RegulatoryPackage.return_family,
        )
        .join(
            RegulatoryPackage,
            (RegulatoryPackage.id == PackageSignatureRecipient.package_id)
            & (RegulatoryPackage.organization_id == PackageSignatureRecipient.organization_id),
        )
        .where(
            PackageSignatureRecipient.organization_id == organization_id,
            PackageSignatureRecipient.status == "pending",
            PackageSignatureRecipient.attestation_cycle == RegulatoryPackage.attestation_cycle,
        )
    ).all()
    for row in rows:
        # SQLAlchemy Row unpacking is untyped; the select's column order is the shape.
        user_id, bank_id, role, family = cast(tuple[UUID, str, str, str], row)
        named = result.setdefault((user_id, bank_id), _NamedRoutes())
        named.roles.add(role)
        named.source_families.add(family)
    return result


def _named_stress_routes(db: Session, organization_id: str) -> dict[tuple[UUID, str], _NamedRoutes]:
    rows = db.execute(
        select(
            EnterpriseStressSignoff.attestation_recipient_user_id,
            EnterpriseStressSignoff.bank_id,
        ).where(
            EnterpriseStressSignoff.organization_id == organization_id,
            EnterpriseStressSignoff.status == "pending_attestation",
            EnterpriseStressSignoff.attestation_recipient_user_id.is_not(None),
        )
    ).all()
    result: dict[tuple[UUID, str], _NamedRoutes] = {}
    for row in rows:
        # SQLAlchemy Row unpacking is untyped; the select's column order is the shape.
        user_id, bank_id = cast(tuple[UUID | None, str], row)
        if user_id is not None:
            result.setdefault((user_id, bank_id), _NamedRoutes()).roles.add("board")
    return result


def _decision(  # noqa: PLR0913 - permission decision carries its complete scope
    db: Session,
    user: User,
    bindings: list[AuthorizationBinding],
    bank_id: str,
    module: Module,
    sensitivity: Sensitivity,
    permission: Permission,
    *,
    now: datetime,
    exact_institution: bool = False,
) -> Eligibility:
    """Evaluate one complete binding, optionally narrowing it to an exact grant."""
    principal = PrincipalLocator(
        user.organization_id,
        user.id,
        PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN,
    )
    resource = ResourceLocator(
        user.organization_id,
        InstitutionScope.INSTITUTION,
        bank_id,
        module,
        sensitivity,
    )
    try:
        decision = authorization.evaluate_structural_permission(
            db, principal, permission, resource, now=now
        )
    except Exception:
        return Eligibility(False, "evaluation_failed")
    matched = set(decision.matching_binding_ids)
    rows = [row for row in bindings if row.id in matched]
    whole = any(row.data_scope_kind == DataScope.ALL.value for row in rows)
    exact = any(
        row.institution_scope == InstitutionScope.INSTITUTION.value
        and row.institution_id == bank_id
        for row in rows
    )
    complete = any(
        row.data_scope_kind == DataScope.ALL.value
        and (
            not exact_institution
            or (
                row.institution_scope == InstitutionScope.INSTITUTION.value
                and row.institution_id == bank_id
            )
        )
        for row in rows
    )
    allowed = decision.allowed and complete
    reason = decision.reason
    if decision.allowed and not whole:
        reason = "institution_grain_requires_whole_institution"
    elif decision.allowed and exact_institution and not exact:
        reason = "exact_institution_signer_required"
    return Eligibility(
        allowed,
        reason,
        sorted(str(value) for value in decision.matching_binding_ids),
    )


def _legacy_reg(  # noqa: PLR0913 - evaluation shares one session and scope
    db: Session,
    user: User,
    bindings: list[AuthorizationBinding],
    bank_id: str,
    named: _NamedRoutes,
    now: datetime,
) -> tuple[Eligibility, list[str], list[str], list[str]]:
    """Reconstruct pre-Signer source-family EDIT/APPROVE/VIEW eligibility."""
    cohorts: list[str] = []
    families: list[str] = []
    matching: set[str] = set()
    failed = False
    for family, module, sensitivity in (
        ("ordinary", Module.REGULATORY, Sensitivity.RESTRICTED),
        ("icaap", Module.CAPITAL, Sensitivity.CONFIDENTIAL),
    ):
        decisions = {
            permission: _decision(
                db, user, bindings, bank_id, module, sensitivity, permission, now=now
            )
            for permission in (Permission.VIEW, Permission.EDIT, Permission.APPROVE)
        }
        failed = failed or any(row.reason == "evaluation_failed" for row in decisions.values())
        view = decisions[Permission.VIEW]
        if view.allowed:
            cohorts.append(f"{family}_witness")
            families.append(family)
            matching.update(view.matching_binding_ids)
        if view.allowed and decisions[Permission.EDIT].allowed:
            cohorts.append(f"{family}_preparer")
            matching.update(decisions[Permission.EDIT].matching_binding_ids)
        if view.allowed and decisions[Permission.APPROVE].allowed:
            cohorts.append(f"{family}_approver_or_executive")
            matching.update(decisions[Permission.APPROVE].matching_binding_ids)
    named_families = [family for family in sorted(named.source_families) if family not in families]
    families.extend(named_families)
    allowed = bool(cohorts) and not failed
    reason = (
        "evaluation_failed"
        if failed
        else "legacy_scoped_signing_authority"
        if allowed
        else "no_legacy_signing_authority"
    )
    return (
        Eligibility(allowed, reason, sorted(matching)),
        sorted(cohorts),
        sorted(families),
        sorted(named.roles),
    )


def _legacy_stress(  # noqa: PLR0913 - evaluation shares one session and scope
    db: Session,
    user: User,
    bindings: list[AuthorizationBinding],
    bank_id: str,
    named: _NamedRoutes,
    now: datetime,
) -> tuple[Eligibility, list[str], list[str], list[str]]:
    """The old attest route combined scalar Approver with confidential source READ."""
    view = _decision(
        db,
        user,
        bindings,
        bank_id,
        Module.RISK,
        Sensitivity.CONFIDENTIAL,
        Permission.VIEW,
        now=now,
    )
    if view.reason == "evaluation_failed":
        return Eligibility(False, "evaluation_failed"), [], [], sorted(named.roles)
    approver = has_role([user.role], "approver")
    allowed = view.allowed and approver
    reason = (
        "legacy_scalar_approver_and_risk_view"
        if allowed
        else "legacy_scalar_approver_required"
        if view.allowed
        else view.reason
    )
    return (
        Eligibility(allowed, reason, view.matching_binding_ids if allowed else []),
        ["enterprise_stress_board"] if allowed else [],
        ["enterprise_stress"] if allowed or named.roles else [],
        sorted(named.roles),
    )


def _required_grants(  # noqa: PLR0913 - full grant and source context is intentional
    db: Session,
    user: User,
    bindings: list[AuthorizationBinding],
    bank_id: str,
    module: Module,
    before: Eligibility,
    after: Eligibility,
    cohorts: list[str],
    families: list[str],
    named_roles: list[str],
    now: datetime,
) -> tuple[list[RequiredGrant], str | None]:
    """Name least-scope Signer plus independent source/checker rows when missing."""
    if (
        not user.is_active
        or user.auth_provider == "service"
        or not (before.allowed or named_roles)
        or "evaluation_failed" in (before.reason, after.reason)
    ):
        return [], None
    grants: list[RequiredGrant] = []
    failed = False

    def add(
        role: RoleBundle, permission: Permission, target: Module, sensitivity: Sensitivity
    ) -> None:
        grants.append(
            RequiredGrant(
                user.organization_id,
                str(user.id),
                PrincipalType.HUMAN.value,
                InstitutionScope.INSTITUTION.value,
                bank_id,
                target.value,
                sensitivity.value,
                role.value,
                permission.value,
                {
                    Permission.SIGN_OFF: "formal_signing_authority",
                    Permission.VIEW: "independent_source_read",
                    Permission.APPROVE: "independent_checker_approval",
                }[permission],
            )
        )

    if not after.allowed:
        add(RoleBundle.SIGNER, Permission.SIGN_OFF, module, Sensitivity.RESTRICTED)
    seen_sources: set[tuple[Module, Sensitivity]] = set()
    for family in families:
        target, sensitivity = {
            "ordinary": (Module.REGULATORY, Sensitivity.RESTRICTED),
            "icaap": (Module.CAPITAL, Sensitivity.CONFIDENTIAL),
            "enterprise_stress": (Module.RISK, Sensitivity.CONFIDENTIAL),
            "liquidity": (Module.REGULATORY, Sensitivity.RESTRICTED),
        }.get(family, (Module.REGULATORY, Sensitivity.RESTRICTED))
        if (target, sensitivity) in seen_sources:
            continue
        seen_sources.add((target, sensitivity))
        view = _decision(db, user, bindings, bank_id, target, sensitivity, Permission.VIEW, now=now)
        failed = failed or view.reason == "evaluation_failed"
        if view.reason != "evaluation_failed" and not view.allowed:
            add(RoleBundle.VIEWER, Permission.VIEW, target, sensitivity)
    checker = any("approver_or_executive" in cohort for cohort in cohorts) or bool(
        {"approver", "board"} & set(named_roles)
    )
    if module is Module.REGULATORY and checker:
        approve = _decision(
            db,
            user,
            bindings,
            bank_id,
            Module.REGULATORY,
            Sensitivity.RESTRICTED,
            Permission.APPROVE,
            now=now,
            exact_institution=True,
        )
        failed = failed or approve.reason == "evaluation_failed"
        if approve.reason != "evaluation_failed" and not approve.allowed:
            add(RoleBundle.APPROVER, Permission.APPROVE, Module.REGULATORY, Sensitivity.RESTRICTED)
    return grants, "evaluation_failed" if failed else None


def _impact(  # noqa: PLR0913 - one assessment needs user, bank, route and clock
    db: Session,
    user: User,
    bindings: list[AuthorizationBinding],
    bank_id: str,
    module: Module,
    named: _NamedRoutes,
    now: datetime,
) -> SignOffImpact:
    kind = PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN
    inactive = Eligibility(False, "principal_not_active")
    machine = Eligibility(False, "human_session_required")
    if not user.is_active:
        before = inactive
        cohorts: list[str] = []
        families: list[str] = []
        named_roles = sorted(named.roles)
    elif kind is PrincipalType.MACHINE:
        before = machine
        cohorts = []
        families = []
        named_roles = sorted(named.roles)
    elif module is Module.REGULATORY:
        before, cohorts, families, named_roles = _legacy_reg(
            db, user, bindings, bank_id, named, now
        )
    else:
        before, cohorts, families, named_roles = _legacy_stress(
            db, user, bindings, bank_id, named, now
        )
    after = (
        inactive
        if not user.is_active
        else machine
        if kind is PrincipalType.MACHINE
        else _decision(
            db,
            user,
            bindings,
            bank_id,
            module,
            Sensitivity.RESTRICTED,
            Permission.SIGN_OFF,
            now=now,
            exact_institution=True,
        )
    )
    required, assessment_error = _required_grants(
        db,
        user,
        bindings,
        bank_id,
        module,
        before,
        after,
        cohorts,
        families,
        named_roles,
        now,
    )
    return SignOffImpact(
        user.organization_id,
        str(user.id),
        kind.value,
        bank_id,
        module.value,
        cohorts,
        families,
        named_roles,
        before,
        after,
        required,
        assessment_error=assessment_error,
    )


def build_signoff_report(
    db: Session, *, organization_id: str, now: datetime | None = None
) -> SignOffReport:
    """Read one tenant's principals, routes and grants without changing state."""
    moment = now or datetime.now(UTC)
    report = SignOffReport(generated_at=_date(moment) or "")
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
        named_reg = _named_routes(db, organization_id)
        named_stress = _named_stress_routes(db, organization_id)
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
            kind = PrincipalType.MACHINE if user.auth_provider == "service" else PrincipalType.HUMAN
            profile = _e2e_profile(user)
            report.principals.append(
                SignOffPrincipal(
                    organization_id,
                    str(user.id),
                    kind.value,
                    user.is_active,
                    user.role,
                    "e2e" if profile is not None else "tenant",
                    profile,
                )
            )
            own = [row for row in bindings if row.principal_user_id == user.id]
            for bank_id in banks:
                for module in (Module.REGULATORY, Module.RISK):
                    named = (named_reg if module is Module.REGULATORY else named_stress).get(
                        (user.id, bank_id), _NamedRoutes()
                    )
                    report.impacts.append(_impact(db, user, own, bank_id, module, named, moment))
    return report


def _impact_fields(row: SignOffImpact) -> dict[str, object]:
    values: dict[str, object] = asdict(row)
    return {
        key: value
        for key, value in values.items()
        if key not in {"before", "after", "required_grants"}
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    def private_opener(name: str, flags: int) -> int:
        return os.open(name, flags, 0o600)

    with open(path, "x", encoding="utf-8", opener=private_opener) as out:
        _ = out.write(json.dumps(payload, indent=2) + "\n")


def write_artifacts(report: SignOffReport, directory: Path) -> None:
    """Create private before/after/exact-grant files; never overwrite evidence."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    common = {key: value for key, value in report.to_dict().items() if key != "impacts"}
    paths = [directory / f"signoff-{stage}.json" for stage in ("before", "after", "exact-grants")]
    if any(path.exists() for path in paths):
        raise ImpactInputError("impact artifacts already exist; choose a new dated directory")
    for stage in ("before", "after"):
        _write_json(
            directory / f"signoff-{stage}.json",
            {
                **common,
                "stage": stage,
                "outcomes": [
                    _impact_fields(row)
                    | {
                        "eligibility": asdict(row.before if stage == "before" else row.after),
                        "newly_denied": row.newly_denied,
                        "named_or_legacy_signer": row.named_or_legacy_signer,
                    }
                    for row in report.impacts
                ],
            },
        )
    _write_json(
        directory / "signoff-exact-grants.json",
        {
            **common,
            "required_grants": [
                {
                    **asdict(grant),
                    "legacy_cohorts": row.legacy_cohorts,
                    "source_families": row.source_families,
                    "named_roles": row.named_roles,
                    "routes": [
                        {"method": method, "path": path}
                        for method, path in SIGNOFF_ROUTES[row.module]
                    ],
                }
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
    report = SignOffReport(generated_at=_date(moment) or "")
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
                tenant = build_signoff_report(db, organization_id=organization_id, now=moment)
                report.principals.extend(tenant.principals)
                report.existing_grants.extend(tenant.existing_grants)
                report.impacts.extend(tenant.impacts)
        if args.output_dir is None:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            write_artifacts(report, args.output_dir)
        return 2 if report.evaluation_failed else 0
    except ImpactInputError as exc:
        print(f"signoff impact failed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Driver exceptions may contain credentials; do not print their details.
        print("signoff impact failed; no complete assessment produced", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
