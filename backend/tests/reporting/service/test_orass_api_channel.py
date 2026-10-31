"""ORASS transport and egress behavior from already-authorized transient inputs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypedDict

import httpx
import pytest
from pydantic import TypeAdapter

from app.models import RegulatoryPackage, RegulatoryPackageArtifact
from app.reporting.service.channels.errors import ChannelDowntimeError, ChannelPreconditionError
from app.reporting.service.channels.orass_api import OrassApiChannel
from tests.reporting.testing.packages import transient_package
from tests.support.factories.outbound import stub_dns, stub_public_dns


class _SubmissionBody(TypedDict):
    metadata: dict[str, object]
    artifacts: list[dict[str, object]]


class _Seen(TypedDict):
    url: str
    auth: str | None
    body: _SubmissionBody


@pytest.fixture(autouse=True)
def resolvable_orass(monkeypatch: pytest.MonkeyPatch) -> None:
    """``api_base_url`` is tenant-supplied, so the channel resolves it through
    the egress guard before every request; stub DNS to stay offline."""
    stub_public_dns(monkeypatch, "orass.example.test")


def _api_channel(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    config: dict[str, object] | None = None,
    credentials: dict[str, object] | None = None,
) -> OrassApiChannel:
    return OrassApiChannel(
        config={"api_base_url": "https://orass.example.test", **(config or {})},
        credentials=credentials if credentials is not None else {"api_key": "test-key"},
        transport=httpx.MockTransport(handler),
    )


def _stub_artifact(package: RegulatoryPackage) -> RegulatoryPackageArtifact:
    return RegulatoryPackageArtifact(
        organization_id=package.organization_id,
        package_id=package.id,
        kind="xlsx",
        object_path="x",
        checksum_sha256="0" * 64,
        size_bytes=1,
        created_at=datetime.now(UTC),
    )


def test_orass_api_submit_returns_reference_and_labels_contract() -> None:
    package = transient_package()
    seen: _Seen = {"url": "", "auth": None, "body": {"metadata": {}, "artifacts": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = TypeAdapter(_SubmissionBody).validate_json(request.content)
        return httpx.Response(201, json={"reference": "LCRN01390", "status": "received"})

    channel = _api_channel(handler)
    ref = channel.submit(package, [_stub_artifact(package)])
    assert ref == "LCRN01390"
    assert seen["url"].endswith("/api/v1/returns/LCR-NSFR/submissions")
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["metadata"]["return_code"] == "LCR-NSFR"
    assert channel.last_detail["provisional_contract"] is True


def test_orass_api_connectivity_failure_maps_to_downtime() -> None:
    package = transient_package()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    channel = _api_channel(handler)
    with pytest.raises(ChannelDowntimeError) as exc:
        channel.submit(package, [_stub_artifact(package)])
    assert "BG/FMD/2026/07" in str(exc.value)


def test_orass_api_5xx_maps_to_downtime_and_4xx_to_precondition() -> None:
    package = transient_package()

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(ChannelDowntimeError):
        _api_channel(server_error).submit(package, [_stub_artifact(package)])

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    with pytest.raises(ChannelPreconditionError):
        _api_channel(unauthorized).submit(package, [_stub_artifact(package)])


def test_orass_api_poll_maps_statuses_and_carries_comments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "declined", "comments": "Not approvable as filed."}
        )

    status, detail = _api_channel(handler).poll_with_detail("LCRN01390")
    assert status == "declined"
    assert detail["comments"] == "Not approvable as filed."


def test_orass_api_requires_base_url_and_credentials() -> None:
    package = transient_package()

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={})

    unconfigured = OrassApiChannel(
        config={}, credentials={"api_key": "k"}, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ChannelPreconditionError):
        unconfigured.submit(package, [_stub_artifact(package)])

    keyless = _api_channel(handler, credentials={})
    with pytest.raises(ChannelPreconditionError):
        keyless.submit(package, [_stub_artifact(package)])


def test_orass_api_resubmission_request_flow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/submissions/LCRN01390/resubmission-requests")
        return httpx.Response(201, json={"status": "granted"})

    status, detail = _api_channel(handler).request_resubmission("LCRN01390", "Wrong attachment")
    assert status == "granted"
    assert detail["provisional_contract"] is True


# --- ORASS egress guard (audit P0-6) ---------------------------------------

BLOCKED_ORASS_BASE_URLS = [
    "https://127.0.0.1",
    "https://localhost",
    "https://[::1]",
    "https://169.254.169.254",
    "https://metadata.google.internal",
    "https://10.0.0.5",
    "https://192.168.1.1",
    "https://172.16.0.1",
    "https://100.64.0.1",
    "https://0.0.0.0",
    "https://[::ffff:127.0.0.1]",
    "http://orass.example.test",  # scheme allow-list: TLS only
]


@pytest.mark.parametrize("base_url", BLOCKED_ORASS_BASE_URLS)
def test_orass_api_refuses_a_blocked_base_url(base_url: str) -> None:
    """A tenant-set ``api_base_url`` must not become an internal socket, and a
    blocked target is a configuration fault — never regulator downtime (which
    would route the operator into the email fallback)."""
    package = transient_package()

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the guard must refuse before any request is made")

    channel = _api_channel(handler, config={"api_base_url": base_url})
    with pytest.raises(ChannelPreconditionError) as blocked:
        channel.submit(package, [_stub_artifact(package)])
    assert not isinstance(blocked.value, ChannelDowntimeError)


def test_orass_api_refuses_a_base_url_that_resolves_internally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public-looking name whose DNS answer is the metadata service."""
    package = transient_package()
    stub_dns(monkeypatch, {"orass.example.test": ("169.254.169.254",)})

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the guard must refuse before any request is made")

    with pytest.raises(ChannelPreconditionError) as blocked:
        _api_channel(handler).submit(package, [_stub_artifact(package)])
    assert "169.254.169.254" not in str(blocked.value)


def test_orass_api_rejects_a_redirect_to_a_blocked_destination() -> None:
    """The transport does not follow redirects, and the hook refuses one that
    points at a blocked destination rather than letting it through."""
    package = transient_package()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/"})

    with pytest.raises(ChannelPreconditionError) as blocked:
        _api_channel(handler).submit(package, [_stub_artifact(package)])
    assert "redirected" in str(blocked.value)
