"""Egress guard for tenant-configurable outbound targets (SSRF defence).

Several surfaces let a bank's own operator name *where* AequorOS should connect:
the Database-Direct connection ``host``/``read_replicas``, the Temenos
connection ``endpoint``, and the ORASS API channel ``api_base_url``. Without a
guard those fields let any ``analyst`` aim the backend's socket at the
deployment's own network — a container's loopback services, an RFC1918
neighbour, or the cloud metadata service at ``169.254.169.254`` (whose IAM
credentials would then be reachable through a connection-test error or a staged
"pull"). This module is the ONE place that decides whether a destination is
permitted, so every connector answers the question identically.

Two layers, deliberately different:

- :func:`check_host_syntax` / :func:`check_url_syntax` — **no DNS**. Cheap,
  deterministic, offline. Used by the pydantic schemas so an operator typing
  ``127.0.0.1`` gets an immediate 422 instead of a mystery failure later. A
  syntax check alone is NOT a security control: ``2130706433`` and
  ``core.attacker.example`` both sail through it.
- :func:`check_host` / :func:`check_host_port` / :func:`check_url` — **resolving
  and authoritative**. They resolve the name and validate EVERY address it
  answers with (a name with one public and one loopback A record is blocked),
  and they are called immediately before the connector opens its socket. This
  is the layer that actually stops egress.

Classification is :mod:`ipaddress`-driven (``is_loopback``/``is_private``/
``is_link_local``/``is_multicast``/``is_reserved``/``is_unspecified`` plus a
``not is_global`` backstop) rather than string matching, so notation tricks —
IPv4-mapped IPv6 ``::ffff:127.0.0.1``, 6to4/Teredo embeddings, decimal or octal
IPv4 literals that only the resolver expands — cannot walk past it.

Failure is closed: a name that does not resolve is blocked, because "does not
resolve" is indistinguishable from a resolver that will answer ``127.0.0.1`` on
the second query. The bank-facing message is identical for every rejection
reason so the guard cannot be used as an internal-DNS existence oracle; the
distinguishing detail lives in ``reason``/``internal_detail`` for the logs.

Escape hatch: ``OUTBOUND_ALLOW_PRIVATE_TARGETS=1`` relaxes the address rules for
local development against a core running on ``localhost``. It is OFF by default
and is ignored outright on any DEPLOYED environment — the test is the allow-list
in :func:`app.core.config.is_undeployed_environment`, not ``== "production"``.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core import observability
from app.core.config import SETTINGS_CONFIG, is_undeployed_environment

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "DEFAULT_ALLOWED_SCHEMES",
    "OutboundTarget",
    "OutboundTargetBlocked",
    "check_host",
    "check_host_port",
    "check_host_syntax",
    "check_url",
    "check_url_syntax",
    "redirect_guard",
    "resolve_host",
]

type _Address = ipaddress.IPv4Address | ipaddress.IPv6Address

# Only TLS by default. ``http`` is never in a default allow-list: a caller that
# genuinely needs it must pass it explicitly and justify it at the call site.
DEFAULT_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"https"})

# The single bank-facing rejection message. Identical for every reason so the
# guard never becomes an internal-DNS/topology oracle.
_BLOCKED_MESSAGE: Final = (
    "{field} {value!r} is not a permitted destination for an outbound "
    "connection. Use a routable public hostname or address for this "
    "institution's system."
)

# Names that are blocked whatever they resolve to (or fail to resolve to).
_BLOCKED_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        # Cloud instance-metadata names.
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data",  # legacy EC2 alias
        "instance-data.ec2.internal",
    }
)

# Suffixes that are link-local / host-local by definition (mDNS ``.local``,
# ``foo.localhost``). ``.internal`` is deliberately NOT here: banks legitimately
# name their core ``corebank.<bank>.internal`` on their own resolver.
_BLOCKED_HOSTNAME_SUFFIXES: Final[tuple[str, ...]] = (".localhost", ".local")

# Instance-metadata addresses, named explicitly so the audit trail says
# "cloud_metadata" rather than the incidental "link_local"/"private".
_METADATA_ADDRESSES: Final[frozenset[str]] = frozenset(
    {
        "169.254.169.254",  # AWS IMDS / Azure IMDS / GCP / DigitalOcean / Oracle
        "169.254.169.253",  # AWS VPC DNS
        "169.254.169.123",  # AWS time sync
        "169.254.170.2",  # ECS task metadata / task IAM role
        "100.100.100.200",  # Alibaba Cloud metadata
        "192.0.0.192",  # Oracle Cloud legacy metadata
        "fd00:ec2::254",  # AWS IMDS over IPv6
    }
)

# Ranges that are not routable on the public internet. Most are already caught
# by the ipaddress predicates; they are listed explicitly because this set is
# the reviewable statement of intent (RFC1918, RFC6598 shared address space,
# benchmarking, documentation, IETF protocol assignments, 240/4).
_BLOCKED_NETWORKS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # RFC6598 carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique-local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
)


# Checked in order; the first match names the block reason. Ordered so the most
# specific/most alarming classification wins the audit line.
_ADDRESS_PREDICATES: Final[tuple[tuple[str, Callable[[_Address], bool]], ...]] = (
    ("unspecified", lambda ip: ip.is_unspecified),
    ("loopback", lambda ip: ip.is_loopback),
    ("link_local", lambda ip: ip.is_link_local),
    ("multicast", lambda ip: ip.is_multicast),
    ("private", lambda ip: ip.is_private),
    ("reserved", lambda ip: ip.is_reserved),
)


class OutboundSettings(BaseSettings):
    """Local-development escape hatch for the egress guard.

    ``OUTBOUND_ALLOW_PRIVATE_TARGETS=1`` permits loopback/private destinations
    so a developer can point a connection at a core running on their own
    machine. Off by default, and :func:`_private_targets_allowed` refuses it
    outright on every deployed environment — there is no in-band way to turn the
    guard off on a host somebody else can reach.
    """

    model_config = SETTINGS_CONFIG

    allow_private_targets: bool = Field(default=False, alias="OUTBOUND_ALLOW_PRIVATE_TARGETS")


@lru_cache
def get_outbound_settings() -> OutboundSettings:
    return OutboundSettings()


def _private_targets_allowed() -> bool:
    """The hatch applies only where the developer's own machine IS the
    deployment (``local`` / ``test``).

    This asked ``app_env == "production"`` until 2026-08-23 (audit finding
    D-29). "Not production" is an unbounded set whose default branch is the
    dangerous one, so ``staging`` — the same containers, the same primary
    database, a host somebody else can reach, and ``127.0.0.1`` there is the
    operator control plane (:8100) and OpenBao (:8200) — honoured the flag.
    The second opt-in flag makes that a misconfiguration rather than a default,
    which is why it is D-29 and not a P0; it is still the pattern the loopback
    OIDC carve-out and the operator dev token were both moved off, and the
    three now read the same rule from one place."""
    if not is_undeployed_environment():
        return False
    return get_outbound_settings().allow_private_targets


class OutboundTargetBlocked(ValueError):
    """A tenant-supplied outbound target is not permitted.

    A ``ValueError`` so a pydantic field validator raising it becomes a clean
    422 with the bank-facing ``message`` and nothing else; services translate it
    into their own typed HTTP error. ``reason`` and ``internal_detail`` are for
    logs and audit — never for a response body, because they can carry the
    resolved address.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        field: str = "host",
        internal_detail: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.field = field
        self.internal_detail = internal_detail
        # Reported from the constructor rather than from a factory or the call
        # sites, because constructing this exception *is* the block. Two
        # construction paths already exist (:func:`_blocked` and the
        # scheme-allow-list refusal, which needs its own message), and the six
        # callers that catch it each used to log their own prose line — so
        # coverage depended on every one of them remembering. Here it cannot be
        # lost. ``internal_detail`` can carry a resolved address, so it stays in
        # the log and never in a response body.
        observability.ssrf_blocked(reason=reason, field=field, detail=internal_detail or reason)


@dataclass(frozen=True)
class OutboundTarget:
    """A destination that passed the guard.

    ``addresses`` are every address the name resolved to at validation time —
    all of them checked. They are returned so a transport that can pin its
    socket to a validated address does so; the drivers used today cannot (see
    the TOCTOU note in the module docstring of the callers).
    """

    field: str
    host: str
    port: int | None
    scheme: str | None
    addresses: tuple[str, ...]


def _blocked(field: str, value: str, *, reason: str, detail: str = "") -> OutboundTargetBlocked:
    """The standard refusal. Reporting happens in the exception's constructor."""
    return OutboundTargetBlocked(
        _BLOCKED_MESSAGE.format(field=_label(field), value=value),
        reason=reason,
        field=field,
        internal_detail=detail or reason,
    )


def _label(field: str) -> str:
    return field.replace("_", " ").capitalize()


def normalize_host(value: str) -> str:
    """Lower-case, de-bracket, and strip the root dot from a host token."""
    host = (value or "").strip().strip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    # An IPv6 scope id (``fe80::1%eth0``) is not part of the address to check.
    host, _, _scope = host.partition("%")
    return host.lower()


def split_host_port(value: str) -> tuple[str, int | None]:
    """Split a ``host``/``host:port``/``[v6]:port`` token.

    A bare IPv6 literal (which contains colons of its own) is returned whole;
    only a bracketed literal may carry a port.
    """
    raw = (value or "").strip()
    if raw.startswith("["):
        closing = raw.find("]")
        if closing == -1:
            return normalize_host(raw), None
        host = raw[: closing + 1]
        rest = raw[closing + 1 :]
        port = rest[1:] if rest.startswith(":") else ""
        return normalize_host(host), int(port) if port.isdigit() else None
    if raw.count(":") > 1:  # bare IPv6 literal
        return normalize_host(raw), None
    host, _, port = raw.partition(":")
    return normalize_host(host), int(port) if port.isdigit() else None


def _literal_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 form carries, if any.

    ``::ffff:127.0.0.1`` (IPv4-mapped) is the classic bypass; 6to4 and Teredo
    embed a v4 address too, and all three must be judged on it.
    """
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    teredo = address.teredo
    return teredo[1] if teredo else None


def classify_address(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return the block reason for an address, or ``None`` when it is routable.

    IPv4-mapped/6to4/Teredo IPv6 forms are unwrapped and judged on the embedded
    IPv4 address — ``::ffff:127.0.0.1`` is loopback, not "some IPv6 host".
    """
    address = value if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else None
    if address is None:
        parsed = _literal_address(normalize_host(str(value)))
        if parsed is None:
            return "not_an_address"
        address = parsed

    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            return classify_address(embedded)

    if str(address) in _METADATA_ADDRESSES:
        return "cloud_metadata"
    for reason, predicate in _ADDRESS_PREDICATES:
        if predicate(address):
            return reason
    if any(
        address in network for network in _BLOCKED_NETWORKS if network.version == address.version
    ):
        return "non_routable"
    # Backstop: anything the stdlib does not consider globally routable.
    return None if address.is_global else "non_global"


def resolve_host(host: str) -> tuple[str, ...]:
    """Every address ``host`` resolves to. The DNS seam tests monkeypatch.

    A literal address resolves to itself. Raises :class:`OSError` (``gaierror``)
    when the name does not resolve — callers translate that into a block.
    """
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    seen: dict[str, None] = {}
    for info in infos:
        address = str(info[4][0])
        seen.setdefault(normalize_host(address), None)
    return tuple(seen)


# --- Layer 1: syntax (no DNS) ----------------------------------------------


def check_host_syntax(host: str, *, field: str = "host", allow_empty: bool = False) -> str:
    """Reject an obviously-blocked host WITHOUT resolving. Returns the host.

    Fast feedback for a schema boundary only. It catches literal blocked
    addresses and the well-known local/metadata names; it cannot catch a name
    that merely *resolves* to a blocked address, which is why every connector
    also calls the resolving check immediately before it connects.
    """
    normalized = normalize_host(host)
    if not normalized:
        if allow_empty:
            return host
        raise _blocked(field, host, reason="empty")
    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(_BLOCKED_HOSTNAME_SUFFIXES):
        raise _blocked(field, host, reason="blocked_hostname")
    literal = _literal_address(normalized)
    if literal is not None:
        reason = classify_address(literal)
        if reason is not None and not _private_targets_allowed():
            raise _blocked(field, host, reason=reason)
    return host


def check_host_port_syntax(value: str, *, field: str = "host") -> str:
    """:func:`check_host_syntax` for a ``host``/``host:port`` token."""
    host, _port = split_host_port(value)
    check_host_syntax(host, field=field)
    return value


def check_url_syntax(
    url: str,
    *,
    allowed_schemes: Iterable[str] = DEFAULT_ALLOWED_SCHEMES,
    field: str = "endpoint",
) -> str:
    """Scheme allow-list + :func:`check_host_syntax` on a URL. Returns the URL.

    A value with no ``scheme://`` is treated as a bare ``host``/``host:port``.
    """
    raw = (url or "").strip()
    if not raw:
        raise _blocked(field, url, reason="empty")
    if "://" not in raw:
        check_host_port_syntax(raw, field=field)
        return url
    parts = urlsplit(raw)
    schemes = {scheme.lower() for scheme in allowed_schemes}
    if parts.scheme.lower() not in schemes:
        raise OutboundTargetBlocked(
            f"{_label(field)} must use one of: {', '.join(sorted(schemes))}.",
            reason="scheme_not_allowed",
            field=field,
            internal_detail=f"scheme {parts.scheme!r} not in {sorted(schemes)}",
        )
    host = parts.hostname or ""
    if not host:
        raise _blocked(field, url, reason="no_host")
    check_host_syntax(host, field=field)
    return url


# --- Layer 2: resolving (authoritative, pre-connect) ------------------------


def check_host(
    host: str,
    *,
    port: int | None = None,
    field: str = "host",
    scheme: str | None = None,
) -> OutboundTarget:
    """Resolve ``host`` and validate EVERY address it answers with.

    This is the authoritative check and the one that must run immediately
    before a connector opens its socket. Fails closed: an unresolvable name is
    blocked, since a resolver that answers ``NXDOMAIN`` now may answer
    ``127.0.0.1`` on the query the transport itself makes.
    """
    check_host_syntax(host, field=field)
    normalized = normalize_host(host)
    allow_private = _private_targets_allowed()
    try:
        addresses = resolve_host(normalized)
    except OSError as exc:
        if allow_private:
            # Development against a name only the developer's machine knows.
            return OutboundTarget(
                field=field, host=normalized, port=port, scheme=scheme, addresses=()
            )
        raise _blocked(
            field, host, reason="unresolvable", detail=f"{type(exc).__name__}: {exc}"
        ) from exc
    if not addresses:
        raise _blocked(field, host, reason="unresolvable", detail="no addresses returned")
    if not allow_private:
        for address in addresses:
            reason = classify_address(address)
            if reason is not None:
                raise _blocked(
                    field,
                    host,
                    reason=reason,
                    detail=f"{normalized} resolved to {address} ({reason})",
                )
    return OutboundTarget(
        field=field, host=normalized, port=port, scheme=scheme, addresses=addresses
    )


def check_host_port(value: str, *, field: str = "host") -> OutboundTarget:
    """:func:`check_host` for a ``host``/``host:port``/``[v6]:port`` token."""
    host, port = split_host_port(value)
    return check_host(host, port=port, field=field)


def check_url(
    url: str,
    *,
    allowed_schemes: Iterable[str] = DEFAULT_ALLOWED_SCHEMES,
    field: str = "endpoint",
) -> OutboundTarget:
    """Scheme allow-list + :func:`check_host` on a URL's host.

    A value with no ``scheme://`` is treated as a bare ``host``/``host:port``,
    which is how the Temenos OFS endpoints are written.
    """
    raw = (url or "").strip()
    check_url_syntax(raw, allowed_schemes=allowed_schemes, field=field)
    if "://" not in raw:
        return check_host_port(raw, field=field)
    parts = urlsplit(raw)
    return check_host(
        parts.hostname or "",
        port=parts.port,
        field=field,
        scheme=parts.scheme.lower(),
    )


def redirect_guard(
    *,
    allowed_schemes: Iterable[str] = DEFAULT_ALLOWED_SCHEMES,
    field: str = "endpoint",
) -> Callable[[object], None]:
    """An ``httpx`` response event hook that validates every redirect target.

    A permitted host can still answer ``302 Location: http://169.254.169.254/``,
    so each hop is re-checked against the same rules as the initial URL. Use it
    together with ``follow_redirects=False``: the hook is the defence for a
    client that must follow them.
    """

    def _hook(response: object) -> None:
        status_code = getattr(response, "status_code", 0)
        if not (300 <= int(status_code) < 400):  # noqa: PLR2004 - HTTP redirect class
            return
        headers = getattr(response, "headers", {})
        location = ""
        if hasattr(headers, "get"):
            location = str(headers.get("location") or "")
        if not location:
            return
        request_url = getattr(response, "url", None)
        target = location
        if "://" not in location and request_url is not None:
            joined = getattr(request_url, "join", None)
            target = str(joined(location)) if callable(joined) else location
        check_url(target, allowed_schemes=allowed_schemes, field=field)

    return _hook
