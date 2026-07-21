"""Provision (or update) a dashboard user account.

There is no self-serve signup and no committed admin seed: a user is a row in
``users`` scoped to an organization, authenticating either by Argon2id password
or OIDC SSO. This CLI creates or updates one idempotently (keyed on
organization + email), setting the ``app.organization_id`` RLS GUC so the write
succeeds against the FORCE-RLS production Postgres.

Connects with the app's configured ``DATABASE_URL`` (environment or
``backend/.env``) — the same database the running service uses. It never falls
back to a local database, so you always know which DB you are writing to.

Usage:
    # Password account in an existing org (prints a generated temp password):
    uv run python scripts/create_user.py \
        --email lawrenceaddo@gmail.com --org-id <ORG_UUID> --role admin --password

    # SSO account (links on first OIDC login by matching email):
    uv run python scripts/create_user.py \
        --email lawrenceaddo@gmail.com --org-id <ORG_UUID> --role admin --sso

    # Create a brand-new organization at the same time:
    uv run python scripts/create_user.py \
        --email lawrenceaddo@gmail.com --create-org --org-name "Cedyn Group" \
        --role admin --sso

Tip: to place someone in the SAME org as an existing user, that user's
organization id is the ``org`` claim in their access token, or ``GET
/api/v1/auth/me`` returns ``organizationId``.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import security  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import USER_ROLES, User  # noqa: E402


def _set_tenant_context(session: Session, organization_id: UUID) -> None:
    """Set the RLS GUC so writes/reads are scoped to this tenant (Postgres only)."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        sql_text("SELECT set_config('app.organization_id', :org, true)"),
        {"org": str(organization_id)},
    )


def _resolve_org(session: Session, args: argparse.Namespace) -> UUID:
    if args.create_org:
        org_id = UUID(args.org_id) if args.org_id else uuid4()
        _set_tenant_context(session, org_id)
        if session.scalar(select(Organization.id).where(Organization.id == org_id)) is None:
            session.add(Organization(id=org_id, name=args.org_name))
            session.flush()
            print(f"Created organization {args.org_name!r} ({org_id}).")
        return org_id

    org_id = UUID(args.org_id)
    _set_tenant_context(session, org_id)
    if session.scalar(select(Organization.id).where(Organization.id == org_id)) is None:
        raise SystemExit(
            f"Organization {org_id} not found (or not visible under RLS). "
            "Pass --create-org --org-name to create it, or check the org id."
        )
    return org_id


def provision(session: Session, args: argparse.Namespace) -> tuple[User, str | None]:
    org_id = _resolve_org(session, args)

    user = session.scalar(
        select(User).where(User.organization_id == org_id, User.email == args.email)
    )
    created = user is None
    if user is None:
        user = User(organization_id=org_id, email=args.email)
        session.add(user)

    user.role = args.role
    if args.display_name:
        user.display_name = args.display_name
    user.is_active = True

    generated_password: str | None = None
    if args.sso:
        user.auth_provider = "oidc"
        user.password_hash = None
        if args.sso_subject:
            user.sso_subject = args.sso_subject
    else:
        password = args.password or secrets.token_urlsafe(12)
        if args.password is None:
            generated_password = password
        user.auth_provider = "password"
        user.password_hash = security.hash_password(password)
        user.sso_subject = None

    # Clear any lockout state on (re)provision.
    user.failed_login_attempts = 0
    user.locked_until = None

    session.flush()
    print(f"{'Created' if created else 'Updated'} user {args.email} "
          f"(role={args.role}, auth={user.auth_provider}, org={org_id}).")
    return user, generated_password


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create or update a dashboard user account.")
    p.add_argument("--email", required=True, help="User email (login identity).")
    p.add_argument("--org-id", help="Organization UUID the user belongs to.")
    p.add_argument("--org-name", help="Organization name (used with --create-org).")
    p.add_argument(
        "--create-org",
        action="store_true",
        help="Create the organization if it does not exist (requires --org-name).",
    )
    p.add_argument(
        "--role",
        choices=USER_ROLES,
        default="viewer",
        help="Authorization role (default: viewer).",
    )
    p.add_argument("--display-name", help="Human-readable display name.")
    auth = p.add_mutually_exclusive_group(required=True)
    auth.add_argument(
        "--password",
        nargs="?",
        const=None,
        default=argparse.SUPPRESS,
        help="Password login. Give a value, or pass the flag alone to generate one.",
    )
    auth.add_argument(
        "--sso",
        action="store_true",
        help="Auth0 SSO login (no password; links on first Auth0 sign-in by email).",
    )
    p.add_argument("--sso-subject", help="Optional Auth0 subject to pre-link (with --sso).")
    return p


def main() -> int:
    args = build_parser().parse_args()
    # Normalize: --sso path has no `password` attribute; make it explicit.
    args.sso = getattr(args, "sso", False)
    args.password = getattr(args, "password", None)
    if args.create_org and not args.org_name:
        raise SystemExit("--create-org requires --org-name.")
    if not args.create_org and not args.org_id:
        raise SystemExit("--org-id is required (or use --create-org --org-name).")

    database_url = get_settings().database.database_url
    if not database_url:
        print(
            "DATABASE_URL is not configured (set it in the environment or backend/.env).",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            _, generated_password = provision(session, args)
            session.commit()
    finally:
        engine.dispose()

    if generated_password is not None:
        print("\nTemporary password (store securely, shown once):")
        print(f"  {generated_password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
