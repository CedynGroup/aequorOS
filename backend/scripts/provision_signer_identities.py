"""Provision permanent signer identities for existing users.

Deliberately a script rather than an alembic migration. Derivation needs
``SIGNER_ID_PEPPER``, which is deployment configuration, not schema: a migration
that required it would fail ``alembic upgrade head`` on every environment where
signing is not yet configured — including CI. Identities are also provisioned
lazily on first use and eagerly on SSO approval, so this script is a
convenience for pre-populating a roster, never a correctness requirement.

Usage (idempotent — safe to re-run):

    cd backend
    SIGNER_ID_PEPPER=... DATABASE_URL=... uv run python scripts/provision_signer_identities.py
    #   --org OR-XXXXXXXX   restrict to one organization
    #   --dry-run           report what would be minted, write nothing

Against a FORCE-RLS Postgres the script sets the tenant GUC per organization,
exactly as ``scripts/create_user.py`` does.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Organization, SignerIdentity, User
from app.services.attestation.identity import SignerIdentityError, ensure_signer_identity


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", help="Organization platform id (OR-XXXXXXXX). Default: all.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report only; make no changes."
    )
    return parser.parse_args(argv)


def _organizations(session: Session, org: str | None) -> list[str]:
    stmt = select(Organization.id).order_by(Organization.id)
    if org:
        stmt = stmt.where(Organization.id == org)
    return list(session.scalars(stmt))


def _provision_for_org(session: Session, organization_id: str, *, dry_run: bool) -> tuple[int, int]:
    """``(minted, skipped)`` for one organization."""
    session.execute(
        text("SELECT set_config('app.organization_id', :org, true)"),
        {"org": organization_id},
    )
    ctx = TenantContext(organization_id=organization_id)
    # Humans only: service accounts never attest.
    candidates = list(
        session.scalars(
            select(User)
            .where(
                User.organization_id == organization_id,
                User.is_active.is_(True),
                User.auth_provider != "service",
            )
            .order_by(User.email)
        )
    )
    existing = set(
        session.scalars(
            select(SignerIdentity.user_id).where(
                SignerIdentity.organization_id == organization_id
            )
        )
    )

    minted = 0
    skipped = 0
    for user in candidates:
        if user.id in existing:
            skipped += 1
            continue
        if dry_run:
            print(f"  would mint for {user.email}")
            minted += 1
            continue
        try:
            identity = ensure_signer_identity(session, ctx, user.id)
        except SignerIdentityError as exc:
            print(f"  SKIPPED {user.email}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        session.commit()
        print(f"  {identity.signer_id}  {user.email}")
        minted += 1
    return minted, skipped


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not os.environ.get("SIGNER_ID_PEPPER"):
        print(
            "SIGNER_ID_PEPPER is not set. Signer identities are derived under a "
            "server-side pepper; refusing to mint without it.",
            file=sys.stderr,
        )
        return 2
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    engine = create_engine(database_url)
    total_minted = 0
    total_skipped = 0
    with Session(engine) as session:
        organizations = _organizations(session, args.org)
        if not organizations:
            print("No matching organizations.", file=sys.stderr)
            return 1
        for organization_id in organizations:
            print(f"{organization_id}:")
            minted, skipped = _provision_for_org(
                session, organization_id, dry_run=args.dry_run
            )
            total_minted += minted
            total_skipped += skipped

    verb = "would mint" if args.dry_run else "minted"
    print(f"\n{verb} {total_minted}; already provisioned or skipped {total_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
