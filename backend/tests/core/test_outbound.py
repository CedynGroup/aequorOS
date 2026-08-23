"""The outbound egress guard (SSRF defence) — ``app.core.outbound``.

Audit finding P0-6: tenant-configurable connection endpoints let an ``analyst``
aim the backend's socket at loopback, an RFC1918 neighbour, or the cloud
metadata service. This suite pins the primitive that every connector now calls.

Nothing here touches the network: :func:`app.core.outbound.resolve_host` is the
DNS seam and is stubbed with a fixed table, so the "resolves to a private
address" and "resolves to a public address" cases are both deterministic and
offline.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from app.core.outbound import (
    OutboundTargetBlocked,
    check_host,
    check_host_port,
    check_host_port_syntax,
    check_host_syntax,
    check_url,
    check_url_syntax,
    classify_address,
    get_outbound_settings,
    redirect_guard,
)
from tests.factories.outbound import PUBLIC_IP, stub_dns, stub_public_dns

# Every address form the finding names, plus the classic notation bypasses.
BLOCKED_ADDRESSES = [
    ("127.0.0.1", "loopback"),
    ("127.1.2.3", "loopback"),
    ("::1", "loopback"),
    ("169.254.169.254", "cloud_metadata"),
    ("fd00:ec2::254", "cloud_metadata"),
    ("169.254.170.2", "cloud_metadata"),
    ("169.254.10.1", "link_local"),
    ("fe80::1", "link_local"),
    ("10.0.0.5", "private"),
    ("192.168.1.1", "private"),
    ("172.16.0.1", "private"),
    ("100.64.0.1", "non_routable"),
    ("0.0.0.0", "unspecified"),
    ("::", "unspecified"),
    ("224.0.0.1", "multicast"),
    ("ff02::1", "multicast"),
    ("::ffff:127.0.0.1", "loopback"),
    ("::ffff:10.0.0.5", "private"),
    ("2002:a00:5::", "private"),  # 6to4 wrapping 10.0.0.5
]


@pytest.fixture(autouse=True)
def _guard_settings_are_default() -> Iterator[None]:
    """The escape hatch is off unless a test turns it on."""
    get_outbound_settings.cache_clear()
    yield
    get_outbound_settings.cache_clear()


# --- address classification -------------------------------------------------


@pytest.mark.parametrize(("address", "reason"), BLOCKED_ADDRESSES)
def test_classify_address_blocks_non_routable_destinations(address: str, reason: str) -> None:
    assert classify_address(address) == reason


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "2606:2800:220:1::1"])
def test_classify_address_allows_public_destinations(address: str) -> None:
    assert classify_address(address) is None


# --- syntax layer (no DNS) --------------------------------------------------


@pytest.mark.parametrize(("address", "_reason"), BLOCKED_ADDRESSES)
def test_check_host_syntax_blocks_literal_addresses(address: str, _reason: str) -> None:
    with pytest.raises(OutboundTargetBlocked):
        check_host_syntax(address)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "localhost.",
        "core.localhost",
        "printer.local",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    ],
)
def test_check_host_syntax_blocks_local_and_metadata_names(host: str) -> None:
    with pytest.raises(OutboundTargetBlocked):
        check_host_syntax(host)


def test_check_host_syntax_allows_a_banks_own_hostname() -> None:
    # ``.internal`` is a bank's own resolver namespace, not a blocked suffix.
    assert check_host_syntax("corebank.sample-bank.internal") == "corebank.sample-bank.internal"


def test_check_host_syntax_allows_empty_only_when_asked() -> None:
    assert check_host_syntax("", allow_empty=True) == ""
    with pytest.raises(OutboundTargetBlocked):
        check_host_syntax("")


def test_check_host_port_syntax_blocks_a_blocked_host_with_a_port() -> None:
    with pytest.raises(OutboundTargetBlocked):
        check_host_port_syntax("127.0.0.1:5432")
    with pytest.raises(OutboundTargetBlocked):
        check_host_port_syntax("[::1]:5432")


def test_check_url_syntax_enforces_the_scheme_allow_list() -> None:
    assert check_url_syntax("https://core.bank.example/api") == "https://core.bank.example/api"
    for url in ("http://core.bank.example", "file:///etc/passwd", "gopher://core.bank.example"):
        with pytest.raises(OutboundTargetBlocked) as blocked:
            check_url_syntax(url)
        assert blocked.value.reason == "scheme_not_allowed"


def test_check_url_syntax_ignores_userinfo_when_finding_the_host() -> None:
    # ``https://core.bank.example@127.0.0.1/`` targets 127.0.0.1, not the bank.
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_url_syntax("https://core.bank.example@127.0.0.1/x")
    assert blocked.value.reason == "loopback"


# --- resolving layer (authoritative) ----------------------------------------


def test_check_host_allows_a_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "core.bank.example")
    target = check_host("core.bank.example")
    assert target.host == "core.bank.example"
    assert target.addresses == (PUBLIC_IP,)


def test_check_host_blocks_a_hostname_that_resolves_to_a_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_dns(monkeypatch, {"rebind.attacker.example": ("10.0.0.5",)})
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_host("rebind.attacker.example")
    assert blocked.value.reason == "private"
    # The resolved address is internal topology: it stays out of the message.
    assert "10.0.0.5" not in blocked.value.message
    assert "10.0.0.5" in blocked.value.internal_detail


def test_check_host_validates_every_address_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name with one public and one loopback answer is blocked."""
    stub_dns(monkeypatch, {"split.attacker.example": (PUBLIC_IP, "127.0.0.1")})
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_host("split.attacker.example")
    assert blocked.value.reason == "loopback"


def test_check_host_blocks_metadata_by_resolution_even_under_an_innocent_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_dns(monkeypatch, {"core.bank.example": ("169.254.169.254",)})
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_host("core.bank.example")
    assert blocked.value.reason == "cloud_metadata"


def test_check_host_fails_closed_on_an_unresolvable_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NXDOMAIN now says nothing about the answer the transport will get."""
    stub_dns(monkeypatch, {})
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_host("nowhere.attacker.example")
    assert blocked.value.reason == "unresolvable"


def test_check_host_blocks_a_decimal_ipv4_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    """``2130706433`` is 127.0.0.1 to a resolver; only the resolving layer sees it."""
    stub_dns(monkeypatch, {"2130706433": ("127.0.0.1",)})
    assert check_host_syntax("2130706433") == "2130706433"  # syntax layer cannot tell
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_host("2130706433")
    assert blocked.value.reason == "loopback"


def test_check_host_port_carries_the_port_through(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "replica.bank.example")
    target = check_host_port("replica.bank.example:1521")
    assert (target.host, target.port) == ("replica.bank.example", 1521)


def test_check_url_resolves_the_urls_host(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_dns(monkeypatch, {"core.bank.example": ("10.1.2.3",)})
    with pytest.raises(OutboundTargetBlocked) as blocked:
        check_url("https://core.bank.example/api/v1")
    assert blocked.value.reason == "private"


def test_check_url_accepts_a_bare_host_token(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "core.bank.example")
    target = check_url("core.bank.example:443", allowed_schemes={"https"})
    assert target.host == "core.bank.example"


# --- redirects --------------------------------------------------------------


class _Headers(dict):
    pass


class _Url:
    def __init__(self, value: str) -> None:
        self._value = value

    def join(self, other: str) -> str:
        return f"https://core.bank.example{other}"

    def __str__(self) -> str:
        return self._value


class _Response:
    def __init__(self, status_code: int, location: str = "") -> None:
        self.status_code = status_code
        self.headers = _Headers({"location": location} if location else {})
        self.url = _Url("https://core.bank.example/api")


def test_redirect_guard_blocks_a_redirect_to_a_blocked_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_public_dns(monkeypatch, "core.bank.example")
    hook = redirect_guard()
    with pytest.raises(OutboundTargetBlocked):
        hook(_Response(302, "https://169.254.169.254/latest/meta-data/"))


def test_redirect_guard_allows_a_redirect_to_a_public_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_public_dns(monkeypatch, "core.bank.example", "other.bank.example")
    hook = redirect_guard()
    hook(_Response(302, "https://other.bank.example/api"))
    hook(_Response(200))  # not a redirect at all


def test_redirect_guard_resolves_a_relative_location(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "core.bank.example")
    redirect_guard()(_Response(301, "/moved"))


# --- escape hatch -----------------------------------------------------------


def test_escape_hatch_permits_loopback_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OUTBOUND_ALLOW_PRIVATE_TARGETS", "1")
    monkeypatch.setenv("APP_ENV", "local")
    get_outbound_settings.cache_clear()
    get_settings.cache_clear()
    try:
        assert check_host_syntax("127.0.0.1") == "127.0.0.1"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_escape_hatch_is_refused_on_every_deployed_environment(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    """The hatch used to ask ``app_env == "production"`` (audit finding D-29),
    so ``staging`` — the same containers on a host somebody else can reach,
    where ``127.0.0.1`` is the operator control plane and OpenBao — honoured
    it. The rule is now the allow-list of UNDEPLOYED environments, so an
    unrecognised value is treated as deployed rather than as a developer's
    laptop."""
    monkeypatch.setenv("OUTBOUND_ALLOW_PRIVATE_TARGETS", "1")
    monkeypatch.setenv("APP_ENV", app_env)
    get_outbound_settings.cache_clear()
    get_settings.cache_clear()
    try:
        with pytest.raises(OutboundTargetBlocked):
            check_host_syntax("127.0.0.1")
    finally:
        get_outbound_settings.cache_clear()
        get_settings.cache_clear()
