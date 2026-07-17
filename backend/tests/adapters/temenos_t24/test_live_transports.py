"""Live transport seams: each builds a faithful request and confines the
network submission to a portal-gated hook that classifies as CORE_UNAVAILABLE
without leaking core internals to the bank-facing surface."""

from __future__ import annotations

from datetime import date

import pytest

from app.adapters.temenos_t24.auth import SimulatedSessionProvider, TemenosCredentials
from app.adapters.temenos_t24.catalog import load_mode_catalog
from app.adapters.temenos_t24.domains import CoreBankingDomain
from app.adapters.temenos_t24.errors import TemenosError, TemenosErrorCode
from app.adapters.temenos_t24.transport import build_domain_request
from app.adapters.temenos_t24.transports import (
    IrisTransport,
    OfsTransport,
    OpenApiTransport,
    live_transport_for,
)

_AS_OF = date(2026, 6, 30)


def _request(mode: str, domain: CoreBankingDomain):
    entry = load_mode_catalog(mode).entries[domain]
    return build_domain_request(entry, as_of=_AS_OF, company="GH0010001", mode=mode)


def _session(mode: str):
    return SimulatedSessionProvider().sign_on(
        mode, f"{mode.lower()}://sample-bank", TemenosCredentials(username="SVC"), company="GH01"
    )


@pytest.mark.parametrize(
    ("mode", "transport"),
    [
        ("OFS", OfsTransport()),
        ("IRIS", IrisTransport()),
        ("OPEN_API", OpenApiTransport()),
    ],
)
def test_live_transport_is_unavailable_until_portal_validation(mode, transport) -> None:
    request = _request(mode, CoreBankingDomain.COUNTERPARTY_MASTER)
    with pytest.raises(TemenosError) as excinfo:
        transport.fetch(_session(mode), request)
    assert excinfo.value.code is TemenosErrorCode.CORE_UNAVAILABLE


@pytest.mark.parametrize("mode", ["OFS", "IRIS", "OPEN_API"])
def test_live_transport_fault_does_not_leak_internals(mode) -> None:
    request = _request(mode, CoreBankingDomain.GL_BALANCES)
    try:
        live_transport_for(mode).fetch(_session(mode), request)
    except TemenosError as err:
        # bank-facing message names neither the enquiry/endpoint nor the wire
        assert "AEQ." not in str(err)
        assert "iris/" not in str(err)
        assert "pending" not in str(err).lower()
        # the completion detail is retained internally only
        assert "pending Temenos portal validation" in err.internal_detail


def test_live_transport_for_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="No live transport"):
        live_transport_for("SOAP")
