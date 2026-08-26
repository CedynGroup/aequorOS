"""Provision (or update) an AequorOS staff (operator) account.

Staff auth mirrors the client model: email+password is the primary sign-in
(Argon2id, the same hash scheme as tenant accounts), workforce SSO secondary.
There is no self-serve signup and no committed seed: an operator is a row in
the GLOBAL ``operator_users`` table (no RLS — control-plane precedent). This
CLI creates or updates one idempotently, keyed on lowercased email.
Workforce SSO also requires this row to be active and takes its role from it;
belonging to the configured workforce email domain alone grants no access.

PRODUCTION BOOTSTRAP: dev-token auth cannot exist in production (the operator
app refuses to boot with it enabled), so the first ``operator_admin`` there is
created by running THIS script inside the backend container:

    uv run python scripts/create_operator.py \
        --email founder@aequoros.com --display-name "Founder" --role operator_admin

Every subsequent account should be created through the console (Operators
surface) or ``POST /operator/v1/operators`` so the action is audit-logged.
Locally, dev-token sessions carry ``operator_admin`` and can create the first
operator through the API instead.

Connects with the operator database resolution order (OPERATOR_DATABASE_URL →
WORKER_DATABASE_URL → DATABASE_URL, from the environment or ``backend/.env``)
— on the RLS-forced primary the operator/worker BYPASSRLS role is the natural
fit, though ``operator_users`` itself is not RLS-forced.

The generated password (or ``--password`` value) is printed ONCE. Only the
hash is stored.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import security  # noqa: E402
from app.models.operator import OPERATOR_ROLES, OperatorUser  # noqa: E402


def _database_url() -> str | None:
    """OPERATOR_DATABASE_URL → WORKER_DATABASE_URL → DATABASE_URL (the
    operator app's own resolution order, minus the HTTP error envelope)."""
    from app.core.config import get_operator_settings, get_settings  # noqa: PLC0415

    settings = get_settings()
    return (
        get_operator_settings().operator_database_url
        or settings.worker.worker_database_url
        or settings.database.database_url
    )


def provision(session: Session, args: argparse.Namespace) -> tuple[OperatorUser, str | None]:
    email = args.email.strip().lower()
    user = session.scalar(select(OperatorUser).where(OperatorUser.email == email))
    created = user is None
    if user is None:
        user = OperatorUser(email=email, display_name=args.display_name, role=args.role)
        session.add(user)

    user.display_name = args.display_name
    user.role = args.role
    user.is_active = True

    generated_password: str | None = None
    password = args.password or secrets.token_urlsafe(18)
    if args.password is None:
        generated_password = password
    user.password_hash = security.hash_password(password)

    session.flush()
    print(
        f"{'Created' if created else 'Updated'} operator {email} (role={args.role}, active=True)."
    )
    return user, generated_password


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create or update a staff operator account.")
    p.add_argument("--email", required=True, help="Operator email (login identity).")
    p.add_argument("--display-name", required=True, help="Human-readable display name.")
    p.add_argument(
        "--role",
        choices=OPERATOR_ROLES,
        default="developer",
        help="Staff role (default: developer; operator_admin manages accounts).",
    )
    p.add_argument(
        "--password",
        nargs="?",
        const=None,
        default=None,
        help="Password. Give a value, or omit/pass the bare flag to generate one.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    database_url = _database_url()
    if not database_url:
        print(
            "No database configured (set OPERATOR_DATABASE_URL, WORKER_DATABASE_URL, "
            "or DATABASE_URL in the environment or backend/.env).",
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
