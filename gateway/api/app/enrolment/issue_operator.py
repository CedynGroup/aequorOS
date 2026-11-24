"""``python -m app.enrolment.issue_operator``: issue a channel operator's credentials.

    python -m app.enrolment.issue_operator --client-id aequoros-platform \\
        --organisation "AequorOS platform" > operator-bundle.tar

Writes a tar archive to standard output with one folder, named after the client id,
holding the operator's private key (``client.key.pem``), the client certificate the
gateway's CA issued for it, the CA certificate, ``operator.json`` (the layout a client
such as AequorOS reads from its operator directory) and ``gateway-operator-client.json``,
the entry to add to ``GATEWAY_OPERATOR_CLIENTS``. The key is created in a temporary
directory and exists afterwards only in the archive: the gateway keeps no copy. The
issue is recorded in the audit log.

Only where the gateway's own CA issues certificates (``local``, ``test``, ``sandbox``);
in ``production`` the CA is the one held in the Bank's key service.
"""

from __future__ import annotations

import argparse
import getpass
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

from app.audit.chain import Actor, append
from app.core.context import app_context
from app.core.settings import get_settings
from app.enrolment.ca import require_ca
from app.enrolment.operators import configured_entry, issue_operator_bundle
from app.keys.provider import KeyConfigurationError
from app.main import create_app


class Args(argparse.Namespace):
    client_id: str = ""
    organisation: str = ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.enrolment.issue_operator", description=__doc__
    )
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--organisation", required=True, help="the operator's organisation")
    args = parser.parse_args(argv, namespace=Args())

    if sys.stdout.isatty():
        print(
            "refusing: the archive holds a private key; redirect standard output to a file",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    if not settings.uses_local_keys:
        print(
            "refusing: in production operator certificates come from the CA in the Bank's "
            "key service",
            file=sys.stderr,
        )
        return 2
    ctx = app_context(create_app(settings, run_scheduler=False))
    try:
        ca = require_ca(ctx.ca)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / args.client_id
            client = issue_operator_bundle(
                directory,
                ca,
                ctx.clock,
                client_id=args.client_id,
                organisation=args.organisation,
            )
            entry = configured_entry(client)
            (directory / "gateway-operator-client.json").write_text(
                json.dumps([entry], indent=2) + "\n"
            )
            with ctx.db.session() as session:
                append(
                    session,
                    ctx.keys,
                    ctx.clock,
                    actor=Actor(
                        "system",
                        f"cli:{getpass.getuser()}",
                        "Deployment administrator",
                        "Gateway command line",
                    ),
                    category="credential",
                    action="Issued operator client credentials",
                    object_type="operator_client",
                    object_ref=client.client_id,
                    detail={
                        "organisation": args.organisation,
                        "certificate_sha256": client.thumbprint,
                    },
                )
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as tar:
                tar.add(directory, arcname=client.client_id)
            sys.stdout.buffer.write(archive.getvalue())
            sys.stdout.buffer.flush()
    except KeyConfigurationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(
        f"issued {client.client_id} (certificate {client.thumbprint}). Add the entry in "
        f"{client.client_id}/gateway-operator-client.json to GATEWAY_OPERATOR_CLIENTS and "
        "restart the gateway.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
