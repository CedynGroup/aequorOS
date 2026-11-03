"""Designate or revoke one staff row's emergency break-glass credential.

Break-glass exists for the case where the workforce IdP is down or a staff
account was suspended in error. This is the provisioning/rotation tool: it
works without any console sign-in, so it is usable during the incident it is
meant to cover.

    # designate (or rotate) — prints the code ONCE
    uv run python scripts/manage_break_glass.py --email founder@aequoros.com

    # current state (never the code)
    uv run python scripts/manage_break_glass.py --email founder@aequoros.com --status

    # revoke immediately
    uv run python scripts/manage_break_glass.py --email founder@aequoros.com --disable

The API also needs ``OPERATOR_BREAK_GLASS_ENABLED=true`` before the sign-in
route accepts anything. Rotation replaces the stored hash, so a previously
issued code (or a session minted from it) stops working at once. Every use of
the credential is audited and logged; see docs/rbac.md §11.3.

Uses OPERATOR_DATABASE_URL → DATABASE_URL, from the environment or
``backend/.env``. Set OPERATOR_DATABASE_URL to a credential authorized to
write control-plane tables; the tenant-scoped worker cannot.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import OperatorBreakGlassCredential, OperatorUser  # noqa: E402
from app.operator.services import break_glass  # noqa: E402


def _database_url() -> str | None:
    from app.core.config import get_operator_settings, get_settings  # noqa: PLC0415

    settings = get_settings()
    return get_operator_settings().operator_database_url or settings.database.database_url


def _user(session: Session, email: str) -> OperatorUser:
    user = session.scalar(select(OperatorUser).where(OperatorUser.email == email.strip().lower()))
    if user is None:
        msg = f"No operator account for {email.strip().lower()!r}."
        raise SystemExit(msg)
    return user


def _describe(credential: OperatorBreakGlassCredential | None) -> str:
    if credential is None:
        return "no emergency credential (not designated)"
    if not credential.is_enabled:
        return "disabled (revoked)"
    rotated = credential.rotated_at.isoformat() if credential.rotated_at is not None else "unknown"
    used = credential.last_used_at.isoformat() if credential.last_used_at is not None else "never"
    return f"enabled (issued/rotated {rotated}, last used {used})"


class _Args(argparse.Namespace):
    email: str
    status: bool
    disable: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Designate, inspect, rotate or revoke a staff break-glass credential."
    )
    parser.add_argument("--email", required=True, help="Designated operator email.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="Show state without changing it.")
    mode.add_argument("--disable", action="store_true", help="Revoke the credential immediately.")
    return parser


def main() -> int:
    args = cast("_Args", build_parser().parse_args())
    database_url = _database_url()
    if not database_url:
        print(
            "No database configured (set OPERATOR_DATABASE_URL or DATABASE_URL "
            "in the environment or backend/.env).",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(
        database_url,
        connect_args={"application_name": "aequoros-manage-break-glass"}
        if database_url.startswith("postgresql")
        else {},
    )
    try:
        with Session(engine) as session:
            user = _user(session, args.email)
            credential = break_glass.credential_for_user(session, user.id)
            if args.status:
                print(f"{user.email}: {_describe(credential)}")
                return 0
            if args.disable:
                if credential is None:
                    print(f"{user.email}: no emergency credential to disable.")
                    return 0
                break_glass.disable_credential(session, credential)
                session.commit()
                print(f"{user.email}: emergency credential disabled.")
                return 0

            credential, code = break_glass.issue_credential(session, user)
            session.commit()
            print(f"{user.email}: emergency credential issued (role={user.role}).")
            print("\nEmergency access code (store securely, shown once):")
            print(f"  {code}")
            print(
                "\nSet OPERATOR_BREAK_GLASS_ENABLED=true on the operator API to accept it. "
                "Every use is audited and logged."
            )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
