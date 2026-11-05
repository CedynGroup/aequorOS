"""Egress guard on the Temenos connector (audit finding P0-6).

``endpoint`` is a destination an ``analyst`` chooses. Two layers are pinned
here: the payload screen (a clean 422 before anything is stored) and the
authoritative pre-connect check on the live ``/test`` path, which resolves the
stored endpoint and refuses a name whose DNS answer is internal.

Hermetic: DNS is the stubbed :func:`app.core.outbound.resolve_host` seam and the
T24 sign-on is the offline simulated provider, so nothing here touches a
network.

This module's other-suite cases live in
``test_temenos_connections_outbound_integration.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data_engine.schemas.temenos_connections import (
    TemenosConnectionCreate,
    TemenosConnectionUpdate,
)

CREDS = {"username": "SVC.AEQUOROS", "password": "must-never-leak"}
BLOCKED_ENDPOINTS = [
    "https://127.0.0.1/api",
    "https://localhost/api",
    "https://[::1]/api",
    "https://169.254.169.254/latest/meta-data/",
    "https://metadata.google.internal/computeMetadata/v1/",
    "https://10.0.0.5/api",
    "https://192.168.1.1/api",
    "https://172.16.0.1/api",
    "https://100.64.0.1/api",
    "https://0.0.0.0/api",
    "https://[::ffff:127.0.0.1]/api",
    "ofs://localhost",
    "ofs://127.0.0.1",
]


@pytest.mark.parametrize("endpoint", BLOCKED_ENDPOINTS)
def test_create_payload_rejects_a_blocked_endpoint(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        TemenosConnectionCreate(
            connection_mode="OFS",
            display_name="Core OFS",
            endpoint=endpoint,
            credentials=CREDS,
            # Required by the schema. Without it the payload is invalid for a second
            # reason and the ValidationError below would no longer prove the
            # endpoint screen fired.
            default_currency="GHS",
        )


@pytest.mark.parametrize("endpoint", BLOCKED_ENDPOINTS)
def test_update_payload_rejects_a_blocked_endpoint(endpoint: str) -> None:
    """The update path is screened too: an ACTIVE connection cannot be
    re-pointed at loopback or the metadata service."""
    with pytest.raises(ValidationError):
        TemenosConnectionUpdate(endpoint=endpoint)


def test_payload_rejects_a_non_tls_scheme() -> None:
    for endpoint in ("http://core.bank.example", "file:///etc/passwd"):
        with pytest.raises(ValidationError):
            TemenosConnectionUpdate(endpoint=endpoint)


def test_payload_accepts_the_ofs_and_https_forms() -> None:
    assert TemenosConnectionUpdate(endpoint="ofs://sample-bank").endpoint == "ofs://sample-bank"
    assert TemenosConnectionUpdate(endpoint="https://core.bank.example/iris").endpoint
