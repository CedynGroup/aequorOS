"""TLS listener entrypoint for aequorOS API and staff services."""

from __future__ import annotations

import argparse
import os
import ssl
from pathlib import Path
from typing import cast

import uvicorn

from app.core.config import get_settings
from app.core.tls import TransportSecurityError, plaintext_allowed, validate_service_transports


def listener_options() -> dict[str, str | int | bool]:
    settings = get_settings()
    validate_service_transports(settings)
    cert = os.getenv("TLS_CERT_FILE")
    key = os.getenv("TLS_KEY_FILE")
    if not cert or not key:
        if plaintext_allowed(settings):
            return {"proxy_headers": False}
        raise TransportSecurityError(
            "TLS_CERT_FILE and TLS_KEY_FILE are required for the listener."
        )
    if not Path(cert).is_file() or not Path(key).is_file():
        raise TransportSecurityError("Listener certificate or private key is missing.")
    return {
        "ssl_certfile": cert,
        "ssl_keyfile": key,
        "ssl_version": ssl.PROTOCOL_TLS_SERVER,
        # The origin terminates TLS itself. Never trust a forwarded scheme.
        "proxy_headers": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("api", "operator"))
    args = parser.parse_args()
    service = cast(str, args.service)
    options = listener_options()
    uvicorn.run(
        "app.main:app" if service == "api" else "app.operator.main:app",
        host="0.0.0.0",
        port=8000 if service == "api" else 8100,
        workers=int(os.getenv("WEB_CONCURRENCY", "4")) if service == "api" else 1,
        **options,  # type: ignore[arg-type] - uvicorn's heterogeneous keyword arguments
    )


if __name__ == "__main__":
    main()
