"""Deterministic, offline DNS for the outbound egress guard.

Every connector resolves its tenant-supplied destination through
:func:`app.core.outbound.resolve_host` before it opens a socket, and the guard
fails closed on a name that does not resolve. Test hostnames
(``core-db.internal``, ``ofs://sample-bank``, ``orass.example.test``) resolve
nowhere, so a suite that exercises a connect path must stub the resolver — which
also keeps the suite hermetic: no test ever performs a real DNS lookup.

``stub_dns`` patches the seam with an explicit mapping. Unmapped names raise
``OSError`` exactly as a real NXDOMAIN would, so a test cannot silently rely on
a name it did not declare.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import outbound

if TYPE_CHECKING:
    import pytest

# Documentation-range addresses would themselves be blocked, so the stand-in
# "public" address is a real routable one (example.com's).
PUBLIC_IP = "93.184.216.34"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"


def stub_dns(
    monkeypatch: pytest.MonkeyPatch,
    mapping: dict[str, tuple[str, ...]] | None = None,
    *,
    public_hosts: tuple[str, ...] = (),
) -> None:
    """Point the egress guard's resolver at a fixed table.

    ``mapping`` gives exact answers; ``public_hosts`` is the shorthand for
    "resolves to one routable public address". Anything else raises ``OSError``.
    """
    table: dict[str, tuple[str, ...]] = {host: (PUBLIC_IP,) for host in public_hosts}
    table.update(mapping or {})

    def _resolve(host: str) -> tuple[str, ...]:
        normalized = outbound.normalize_host(host)
        if normalized in table:
            return table[normalized]
        literal = outbound._literal_address(normalized)  # noqa: SLF001 - test seam
        if literal is not None:
            return (normalized,)
        msg = f"stub resolver: {host!r} does not resolve"
        raise OSError(msg)

    monkeypatch.setattr(outbound, "resolve_host", _resolve)


def stub_public_dns(monkeypatch: pytest.MonkeyPatch, *hosts: str) -> None:
    """``stub_dns`` for the common case: these names are routable and public."""
    stub_dns(monkeypatch, public_hosts=hosts)
