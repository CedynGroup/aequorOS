"""Database-Direct connection service: credential sealing, live test/discover
over the offline fixture driver, and the sync path through the ingestion spine.

No live database is ever required: the driver is injected as an
:class:`OfflineDumpDriver` backed by a synthetic dump, so the whole pull path
(query build, normalization, bundle staging) runs offline. The load-bearing
concern is that sealed credentials round-trip the vault and never leak.

The egress guard resolves ``host`` before every connect, so DNS is stubbed too
(``tests.support.factories.outbound``) — no test performs a real lookup. The guard's own
behaviour on this service is pinned at the bottom of this module.

This module's other-suite cases live in ``test_database_connections_integration.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data_engine.schemas.database_connection import (
    DatabaseConnectionUpdate,
)
from tests.data_engine.service.test_database_connections_integration import _create_payload

BLOCKED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "::1",
    "169.254.169.254",
    "metadata.google.internal",
    "10.0.0.5",
    "192.168.1.1",
    "172.16.0.1",
    "100.64.0.1",
    "0.0.0.0",
    "::ffff:127.0.0.1",
]


@pytest.mark.parametrize("host", BLOCKED_HOSTS)
def test_create_payload_rejects_a_blocked_host(host: str) -> None:
    with pytest.raises(ValidationError):
        _create_payload(host=host)


@pytest.mark.parametrize("host", BLOCKED_HOSTS)
def test_update_payload_rejects_a_blocked_host(host: str) -> None:
    """The update path is screened too — an ACTIVE connection cannot be
    re-pointed at loopback or the metadata service."""
    with pytest.raises(ValidationError):
        DatabaseConnectionUpdate(host=host)


def test_update_payload_rejects_a_blocked_read_replica() -> None:
    with pytest.raises(ValidationError):
        DatabaseConnectionUpdate(read_replicas=["replica-db.internal:1433", "127.0.0.1:1433"])
