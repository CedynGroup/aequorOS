"""Live network transports for each connection mode (portal-gated seams).

Each mode's live transport builds a faithful request and confines the actual
network submission to a single ``_submit``/``_get`` hook that is completed with
Temenos developer-portal access. Until then every hook classifies as
CORE_UNAVAILABLE, and the framework defaults to the fixture/unavailable
transport, so no fabricated endpoint or auth flow can reach a bank.
"""

from __future__ import annotations

from app.adapters.temenos_t24.transports.iris import IrisTransport
from app.adapters.temenos_t24.transports.ofs import OfsTransport
from app.adapters.temenos_t24.transports.open_api import OpenApiTransport

__all__ = ["IrisTransport", "OfsTransport", "OpenApiTransport", "live_transport_for"]

_LIVE_TRANSPORTS = {
    "OFS": OfsTransport,
    "IRIS": IrisTransport,
    "OPEN_API": OpenApiTransport,
}


def live_transport_for(mode: str, *, core_system: str = "your core banking system"):
    """The live transport for a connection mode (portal-gated until completed)."""
    try:
        cls = _LIVE_TRANSPORTS[mode]
    except KeyError:
        known = ", ".join(sorted(_LIVE_TRANSPORTS))
        raise ValueError(f"No live transport for mode {mode!r}. Known modes: {known}.") from None
    return cls(core_system=core_system)
