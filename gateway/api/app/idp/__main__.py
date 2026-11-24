"""``python -m app.idp``: manage supervisor accounts of the gateway's identity provider.

    python -m app.idp create --email ... --name ... [--given-name ...] [--family-name ...]
        --group srg-senior-supervisors [--group ...] --password-stdin
    python -m app.idp set-password --email ... --password-stdin
    python -m app.idp disable --email ...
    python -m app.idp enable --email ...      (also clears a lockout)
    python -m app.idp list

Passwords are read from standard input only, never from an argument, so they stay out
of shell history and process listings. Every change is written to the audit log.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.context import app_context
from app.core.errors import ApiError
from app.core.settings import get_settings
from app.db.models import StaffAccount
from app.idp.accounts import account_by_email, create_account, set_password, set_status
from app.main import create_app


class Args(argparse.Namespace):
    command: str = ""
    email: str = ""
    name: str = ""
    given_name: str | None = None
    family_name: str | None = None
    group: list[str] | None = None
    password_stdin: bool = False


def _password(args: Args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != first:
        raise ApiError("invalid_request", "The passwords do not match.")
    return first


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.idp", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create an account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help="full name as shown in the portal")
    create.add_argument("--given-name")
    create.add_argument("--family-name")
    create.add_argument("--group", action="append", required=True)
    create.add_argument("--password-stdin", action="store_true")
    for name in ("set-password", "disable", "enable"):
        sub = commands.add_parser(name)
        sub.add_argument("--email", required=True)
        if name == "set-password":
            sub.add_argument("--password-stdin", action="store_true")
    commands.add_parser("list", help="list accounts")
    args = parser.parse_args(argv, namespace=Args())

    settings = get_settings()
    if not settings.uses_builtin_idp:
        print(
            "refusing: an external identity provider is federated (GATEWAY_OIDC_ISSUER)",
            file=sys.stderr,
        )
        return 2
    ctx = app_context(create_app(settings, run_scheduler=False))
    by = f"cli:{getpass.getuser()}"
    try:
        with ctx.db.session() as session:
            if args.command == "list":
                for account in session.scalars(select(StaffAccount).order_by(StaffAccount.email)):
                    print(
                        f"{account.email}\t{account.name}\t{account.status}\t"
                        f"{','.join(account.groups)}"
                    )
                return 0
            if args.command == "create":
                account = create_account(
                    session,
                    ctx,
                    email=args.email,
                    name=args.name,
                    given_name=args.given_name,
                    family_name=args.family_name,
                    groups=args.group or [],
                    password=_password(args),
                    created_by=by,
                )
                print(f"created {account.email} ({account.subject})")
                return 0
            account = account_by_email(session, args.email)
            if account is None:
                print(f"no account for {args.email}", file=sys.stderr)
                return 1
            if args.command == "set-password":
                set_password(session, ctx, account, _password(args), by=by)
            else:
                set_status(
                    session,
                    ctx,
                    account,
                    "active" if args.command == "enable" else "disabled",
                    by=by,
                )
            print(f"{args.command}: {account.email}")
            return 0
    except ApiError as exc:
        print(f"refused: {exc.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
