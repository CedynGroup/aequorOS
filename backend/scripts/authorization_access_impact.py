"""Who can see what under binding-derived authority — the cutover gate.

Since 2026-09-08 the dashboard shows a user only the institutions and modules
their scoped grants reach; nothing is inferred from the scalar role. Every
further cutover (a module switching to binding enforcement, a change to how
visibility is derived) can therefore silently remove access from people who
had it yesterday. That happened to the founder's own Owner account.

Run this against the target deployment BEFORE such a change and keep the dated
output with the deployment record. It reports, per active human user, exactly
what the dashboard will show, using the SAME projection the API serves
(``services/authorization.project_effective_authority``). Never copy
production identities into the repository.

    uv run python scripts/authorization_access_impact.py                 # every organization
    uv run python scripts/authorization_access_impact.py --organization OR-XXXXXXXX
    uv run python scripts/authorization_access_impact.py --json > impact.json

Reads only. Connect with a role that can see every tenant (the default is
``WORKER_DATABASE_URL``, then ``DATABASE_URL``); each organization is read in
its own tenant-scoped session so a FORCE-RLS role also works when it is given
``--organization``.

Flags per user:

* ``no_bindings``       — no active binding at all: the user can sign in and
                          reach personal settings only.
* ``no_product_view``   — no institution-level ``view`` capability: no module,
                          no bank, the shell routes them to Settings or shows
                          "No authorized institutions".
* ``account_plane_only`` — administers the account (Owner / Account Admin) but
                          reads no product: the exact shape that locked the
                          Owner out; ``202609160052`` backfills the read row.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.authorization import Module
from app.models import AuthorizationBinding, Bank, Organization, User
from app.services import authorization

ORGANIZATION_MODULES = frozenset({Module.ACCOUNT, Module.AUDIT})
INSTITUTION_MODULES = tuple(module.value for module in Module if module not in ORGANIZATION_MODULES)


@dataclass(frozen=True)
class _Principal:
    """The minimum tenant context the projection needs; nothing is inferred."""

    organization_id: str
    actor_user_id: UUID
    authorization_version: int | None


@dataclass
class UserAccess:
    organization_id: str
    user_id: str
    email: str
    display_name: str | None
    scalar_role: str
    active_bindings: int
    account_administer: bool
    institutions: dict[str, list[str]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def build_report(db: Session, *, organization_id: str) -> list[UserAccess]:
    """Project every active human user of one organization through the evaluator."""

    banks = list(
        db.scalars(
            select(Bank).where(Bank.organization_id == organization_id).order_by(Bank.name, Bank.id)
        )
    )
    users = list(
        db.scalars(
            select(User)
            .where(
                User.organization_id == organization_id,
                User.is_active.is_(True),
                User.auth_provider != "service",
            )
            .order_by(User.email, User.id)
        )
    )
    report: list[UserAccess] = []
    for user in users:
        projection = authorization.project_effective_authority(
            db,
            _Principal(organization_id, user.id, user.authorization_version),
            banks,
            failure_surface="access_impact_report",
        )
        binding_count = len(
            list(
                db.scalars(
                    select(AuthorizationBinding.id).where(
                        AuthorizationBinding.organization_id == organization_id,
                        AuthorizationBinding.principal_user_id == user.id,
                        AuthorizationBinding.status == "active",
                    )
                )
            )
        )
        account_administer = any(
            capability.module == Module.ACCOUNT.value and capability.permission == "administer"
            for capability in projection.organization_capabilities
        )
        institutions: dict[str, list[str]] = {
            entry.institution_id: sorted(
                {
                    str(capability.module.value)
                    for capability in entry.capabilities
                    if capability.permission == "view"
                }
            )
            for entry in projection.institution_capabilities
        }
        institutions = {bank_id: modules for bank_id, modules in institutions.items() if modules}
        flags: list[str] = []
        if binding_count == 0:
            flags.append("no_bindings")
        if not institutions:
            flags.append("no_product_view")
        if account_administer and not institutions:
            flags.append("account_plane_only")
        report.append(
            UserAccess(
                organization_id=organization_id,
                user_id=str(user.id),
                email=user.email,
                display_name=user.display_name,
                scalar_role=user.role,
                active_bindings=binding_count,
                account_administer=account_administer,
                institutions=institutions,
                flags=flags,
            )
        )
    return report


def _modules_label(modules: list[str]) -> str:
    return "all" if len(modules) == len(INSTITUTION_MODULES) else ",".join(modules)


def render_table(rows: list[UserAccess]) -> str:
    """A fixed-width table a reviewer can paste into a deployment record."""

    if not rows:
        return "no active human users"
    header: tuple[str, ...] = (
        "organization",
        "email",
        "role",
        "grants",
        "account",
        "product view",
        "flags",
    )
    lines: list[tuple[str, ...]] = [header]
    for row in rows:
        product = (
            "; ".join(
                f"{bank_id}: {_modules_label(modules)}"
                for bank_id, modules in row.institutions.items()
            )
            or "-"
        )
        lines.append(
            (
                row.organization_id,
                row.email,
                row.scalar_role,
                str(row.active_bindings),
                "administer" if row.account_administer else "-",
                product,
                ",".join(row.flags) or "-",
            )
        )
    widths = [max(len(line[index]) for line in lines) for index in range(len(header))]
    out = []
    for number, line in enumerate(lines):
        out.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)).rstrip())
        if number == 0:
            out.append("  ".join("-" * width for width in widths))
    return "\n".join(out)


def _organizations(engine, organization_id: str | None) -> list[str]:
    if organization_id is not None:
        return [organization_id]
    with Session(engine) as db:
        return list(db.scalars(select(Organization.id).order_by(Organization.id)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database-url", default=None, help="defaults to WORKER_DATABASE_URL, then DATABASE_URL"
    )
    parser.add_argument(
        "--organization", default=None, help="one OR-XXXXXXXX (required for a tenant-scoped role)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    url = (
        args.database_url or os.environ.get("WORKER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    if not url:
        parser.error("no database URL: pass --database-url or set WORKER_DATABASE_URL")
    engine = create_engine(url)
    rows: list[UserAccess] = []
    for organization_id in _organizations(engine, args.organization):
        with Session(engine) as db:
            db.info["organization_id"] = organization_id
            rows.extend(build_report(db, organization_id=organization_id))

    if args.json:
        print(json.dumps([asdict(row) for row in rows], indent=2))
        return 0
    print(render_table(rows))
    flagged = [row for row in rows if row.flags]
    print()
    print(
        f"{len(rows)} active human user(s); {len(flagged)} with no product view or no bindings"
        + (" — these people cannot open a module after the cutover." if flagged else ".")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
