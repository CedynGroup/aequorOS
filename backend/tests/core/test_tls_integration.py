"""Transport policy failures and real, hermetic certificate handshakes.

This module's other-suite cases live in ``test_tls.py``.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import cast

import pytest

from tests.core.test_tls import certificate
from tests.core.test_tls import (
    production as production,  # noqa: PLC0414 - shared with the split module
)


def test_postgres_plaintext_peer_cannot_receive_startup_credentials(
    production: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    from sqlalchemy.exc import OperationalError  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from tests.support.database import adopt_application_engine  # noqa: PLC0415

    cert, _key = certificate(tmp_path)
    monkeypatch.setenv("TLS_CA_BUNDLE", str(cert))
    received: list[bytes] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        port = cast(int, listener.getsockname()[1])

        def refuse_ssl() -> None:
            with listener.accept()[0] as peer:
                peer.settimeout(5)
                received.append(peer.recv(8))
                peer.sendall(b"N")  # PostgreSQL's explicit refusal of SSLRequest.
                received.append(peer.recv(1024))

        thread = threading.Thread(target=refuse_ssl)
        thread.start()
        engine = adopt_application_engine(
            request,
            get_engine(
                f"postgresql+psycopg://synthetic:synthetic@localhost:{port}/bank?sslmode=verify-full"
            ),
            administrative=True,
        )
        try:
            with pytest.raises(OperationalError, match="does not support SSL"):
                engine.connect()
        finally:
            engine.dispose()
            thread.join(timeout=6)
        assert not thread.is_alive()
    assert received == [b"\x00\x00\x00\x08\x04\xd2\x16\x2f", b""]
