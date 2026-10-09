"""Read-only TLS negotiation evidence; run from the deployed client's network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.tls import TransportSecurityError, client_context, database_connect_args

DATABASE_ENV_NAMES = (
    "DATABASE_URL",
    "WORKER_DATABASE_URL",
    "BI_DATABASE_URL",
    "OPERATOR_DATABASE_URL",
)


def probe_https(url: str) -> dict[str, str | int | bool | None]:
    """Authenticate the peer and record negotiation without sending application data."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise TransportSecurityError("Evidence target must be HTTPS without credentials.")
    host = parts.hostname
    port = parts.port or 443
    with (
        socket.create_connection((host, port), timeout=10) as raw,
        client_context().wrap_socket(raw, server_hostname=host) as connection,
    ):
        certificate = connection.getpeercert(binary_form=True)
        cipher = connection.cipher()
        return {
            "hostname": host,
            "port": port,
            "certificate_hostname_verified": True,
            "minimum_version": "TLSv1.2",
            "negotiated_version": connection.version(),
            "cipher": cipher[0] if cipher else None,
            "certificate_sha256": hashlib.sha256(certificate or b"").hexdigest(),
        }


def probe_database(url: str) -> dict[str, str | bool | None]:
    settings = get_settings()
    secure = settings.model_copy(
        update={"tls": settings.tls.model_copy(update={"allow_plaintext": False})}
    )
    args = database_connect_args(url, settings=secure)
    if args.get("sslmode") != "verify-full":
        raise TransportSecurityError("Database evidence requires verify-full, even in local mode.")
    engine = create_engine(url, connect_args={**args, "connect_timeout": 10})
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            ).one()
            encrypted = cast(bool, row[0])
            version = cast(str | None, row[1])
            cipher = cast(str | None, row[2])
            if not encrypted or version not in {"TLSv1.2", "TLSv1.3"}:
                raise TransportSecurityError("Database did not negotiate approved TLS.")
            return {
                "certificate_hostname_verified": True,
                "minimum_version": args["ssl_min_protocol_version"],
                "negotiated_version": version,
                "cipher": cipher,
            }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--https", action="append", default=[], help="HTTPS endpoint; repeat for every hop"
    )
    parser.add_argument(
        "--databases", action="store_true", help="Probe configured service database roles"
    )
    args = parser.parse_args()
    results: list[dict[str, object]] = []
    failed = False
    for index, url in enumerate(cast(list[str], args.https)):
        try:
            results.append({"target": f"https-{index + 1}", "result": probe_https(url)})
        except (OSError, ValueError, TransportSecurityError) as exc:
            results.append({"target": f"https-{index + 1}", "error": type(exc).__name__})
            failed = True
    if cast(bool, args.databases):
        for name in DATABASE_ENV_NAMES:
            url = os.getenv(name)
            if not url:
                continue
            try:
                results.append({"target": name, "result": probe_database(url)})
            except Exception as exc:  # noqa: BLE001 - never output credential-bearing driver errors
                results.append({"target": name, "error": type(exc).__name__})
                failed = True
    print(json.dumps({"captured_at": datetime.now(UTC).isoformat(), "results": results}, indent=2))
    if failed or not results:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
