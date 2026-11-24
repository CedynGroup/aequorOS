"""Reporting gateway channel: signed filings, verified receipts, bank-safe errors.

A fake gateway behind ``httpx.MockTransport`` checks what the real one checks
(the client assertion, the manifest signature, the payload digest) and answers
with a receipt signed by its own key, so these tests prove the four protection
layers end to end without a network.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core.key_management.signing import (
    VaultSigningProvider,
    b64url,
    es256_signature,
    generate_p256_key,
    private_key_pem,
    public_jwk,
)
from app.core.key_management.types import KeyReference, KeyUnavailableError
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import gateway_queries
from app.reporting.service.channels.errors import ChannelError, ChannelPreconditionError
from app.reporting.service.channels.jcs import CanonicalizationError, canonical_json
from app.reporting.service.channels.reporting_gateway import (
    ReportingGatewayChannel,
    verify_es256_jws,
)
from app.reporting.service.gateway_catalog import gateway_payload
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, SAMPLE_BANK_ID
from tests.support.factories.outbound import stub_public_dns

BASE_URL = "https://gateway.example.test"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
KID = "0142-sign-20261121"


@pytest.fixture(autouse=True)
def resolvable_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "gateway.example.test")


def _certificate(key: ec.EllipticCurvePrivateKey) -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "0142")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _package(rows: list[dict[str, object]] | None = None) -> RegulatoryPackage:
    return RegulatoryPackage(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        reporting_date=date(2026, 10, 31),
        frequency="monthly",
        basis="solo",
        status="approved",
        version=1,
        submission_revision=0,
        snapshot={
            "sections": [
                {
                    "code": "hqla",
                    "unit": "ghs",
                    "rows": rows
                    if rows is not None
                    else [{"code": "1.1", "description": "Treasury bills", "value": "1500"}],
                }
            ],
            "totals": [
                {"code": "hqla_total_ghs", "description": "Total HQLA", "value": "1500.0000"}
            ],
        },
    )


def _sign(key: ec.EllipticCurvePrivateKey, kid: str, payload: bytes) -> str:
    header = b64url(canonical_json({"alg": "ES256", "kid": kid}))
    signing_input = f"{header}.{b64url(payload)}"
    der = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    return f"{signing_input}.{b64url(es256_signature(der))}"


QUERIES: list[dict[str, object]] = [
    {
        "number": "QRY-2026-0001",
        "kind": "query",
        "subject": "Review",
        "status": "awaiting_institution",
        "receipt_id": "SRG-26-0000001",
        "due_at": "2026-11-26T17:00:00Z",
        "opened_at": "2026-11-23T20:37:37Z",
        "acknowledged_at": None,
        "messages": [
            {
                "id": "m-1",
                "author": "supervisor",
                "author_label": "E. Inkoom Danso",
                "body": "where is the formula",
                "created_at": "2026-11-23T20:37:37Z",
            }
        ],
    },
    {
        "number": "QRY-2026-0002",
        "kind": "resubmission",
        "subject": "Resubmission requested: CAR-RWA for 30 Jun 2026",
        "status": "awaiting_institution",
        "receipt_id": "SRG-26-0000002",
        "due_at": "2026-11-26T12:00:00Z",
        "opened_at": "2026-11-23T21:57:00Z",
        "acknowledged_at": None,
        "messages": [],
    },
    {
        "number": "QRY-2026-0003",
        "kind": "resubmission",
        "subject": "Resubmission requested: CAR-RWA for 30 Jun 2026",
        "status": "closed",
        "receipt_id": "SRG-26-0000002",
        "due_at": None,
        "opened_at": "2026-11-23T21:58:00Z",
        "acknowledged_at": None,
        "messages": [],
    },
]


@dataclass
class FakeGateway:
    """What the real gateway verifies, in-process."""

    client_key: ec.EllipticCurvePrivateKey
    signing_key: ec.EllipticCurvePrivateKey
    receipt_key: ec.EllipticCurvePrivateKey = field(default_factory=generate_p256_key)
    signs_receipts_with: ec.EllipticCurvePrivateKey | None = None
    tamper_receipt: bool = False
    submission_error: tuple[int, str] | None = None
    held_receipts: frozenset[str] = frozenset()
    requests: list[httpx.Request] = field(default_factory=list)
    filings: list[dict[str, object]] = field(default_factory=list)
    query_calls: list[tuple[str, object]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - one route per path
        self.requests.append(request)
        path = request.url.path
        if path == "/oauth/token":
            return self._token(request)
        if path == "/v1/.well-known/jwks.json":
            return httpx.Response(
                200, json={"keys": [public_jwk(self.receipt_key.public_key(), "gw-receipt")]}
            )
        assert request.headers["Authorization"] == "Bearer access-token"
        if path == "/v1/returns/LCR-NSFR":
            return httpx.Response(
                200,
                json={
                    "versions": [
                        {
                            "version": "2026.1",
                            "status": "published",
                            "first_reporting_date": "2026-01-01",
                            "last_reporting_date": None,
                        }
                    ]
                },
            )
        if request.method == "POST" and (
            path == "/v1/submissions"
            or (path.startswith("/v1/submissions/") and path.endswith("/amendments"))
        ):
            return self._submission(request)
        if path.startswith("/v1/queries"):
            return self._queries(request)
        receipt = path.removeprefix("/v1/submissions/")
        if request.method == "GET" and receipt in self.held_receipts:
            return httpx.Response(200, json={"receipt_id": receipt, "status": "in_review"})
        return httpx.Response(404, json={"code": "not_found"})

    def _queries(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            assert request.url.params["status"] == "all"
            return httpx.Response(200, json={"items": QUERIES, "next_cursor": None})
        body = json.loads(request.content) if request.content else None
        self.query_calls.append((path, body))
        if path.endswith("/acknowledgements"):
            return httpx.Response(204)
        assert isinstance(body, dict)
        return httpx.Response(
            201,
            json={
                "id": "m-2",
                "author": "institution",
                "author_label": "Sample Bank Ltd",
                "body": body["body"],
                "created_at": "2026-11-23T20:00:00Z",
            },
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        claims = jwt.decode(
            form["client_assertion"],
            self.client_key.public_key(),
            algorithms=["ES256"],
            audience=f"{BASE_URL}/oauth/token",
            options={"verify_exp": False, "verify_iat": False},
        )
        assert claims["iat"] == int(NOW.timestamp())
        assert claims["exp"] - claims["iat"] <= 300
        assert claims["iss"] == claims["sub"] == "client-0142"
        return httpx.Response(200, json={"access_token": "access-token", "token_type": "Bearer"})

    def _submission(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"]
        if self.submission_error is not None:
            status_code, code = self.submission_error
            return httpx.Response(status_code, json={"code": code, "detail": "internal detail"})
        filing: dict[str, object] = json.loads(request.content)
        self.filings.append(filing)
        manifest = filing["manifest"]
        assert isinstance(manifest, dict)
        signed = verify_es256_jws(
            str(filing["signature"]), [public_jwk(self.signing_key.public_key(), KID)]
        )
        assert signed == canonical_json(manifest)
        assert (
            manifest["payload_sha256"]
            == hashlib.sha256(canonical_json(filing["payload"])).hexdigest()
        )
        receipt: dict[str, object] = {
            "receipt_id": "RCPT-2026-000001",
            "institution_code": manifest["institution_code"],
            "return_code": manifest["return_code"],
            "reporting_date": manifest["reporting_date"],
            "payload_sha256": manifest["payload_sha256"],
            "manifest_sha256": hashlib.sha256(canonical_json(manifest)).hexdigest(),
        }
        receipt_jws = _sign(
            self.signs_receipts_with or self.receipt_key, "gw-receipt", canonical_json(receipt)
        )
        if self.tamper_receipt:
            receipt["payload_sha256"] = "0" * 64
            receipt_jws = _sign(self.receipt_key, "gw-receipt", canonical_json(receipt))
        return httpx.Response(
            201, json={"receipt": receipt, "receipt_jws": receipt_jws, "status": "received"}
        )


def _channel(
    gateway: FakeGateway,
    *,
    config: dict[str, object] | None = None,
    declaration: dict[str, object] | None = None,
    supersedes_receipt_id: str | None = None,
) -> ReportingGatewayChannel:
    return ReportingGatewayChannel(
        config=config
        if config is not None
        else {
            "api_base_url": BASE_URL,
            "institution_code": "0142",
            "client_id": "client-0142",
            "client_certificate_pem": _certificate(gateway.client_key),
            "signing_key": {"provider": "vault", "kid": KID},
            "enrolled_at": NOW.isoformat(),
        },
        credentials={
            "client_private_key_pem": private_key_pem(gateway.client_key),
            "signing_private_key_pem": private_key_pem(gateway.signing_key),
        },
        currency="GHS",
        declaration=declaration
        or {"prepared_by_role": "Head of Regulatory Reporting", "approved_by_role": "CFO"},
        supersedes_receipt_id=supersedes_receipt_id,
        transport=httpx.MockTransport(gateway),
        clock=lambda: NOW,
    )


def _gateway(
    *,
    signs_receipts_with: ec.EllipticCurvePrivateKey | None = None,
    tamper_receipt: bool = False,
    submission_error: tuple[int, str] | None = None,
    held_receipts: frozenset[str] = frozenset(),
) -> FakeGateway:
    return FakeGateway(
        client_key=generate_p256_key(),
        signing_key=generate_p256_key(),
        signs_receipts_with=signs_receipts_with,
        tamper_receipt=tamper_receipt,
        submission_error=submission_error,
        held_receipts=held_receipts,
    )


def test_submit_signs_with_the_banks_vault_key_and_verifies_the_receipt() -> None:
    gateway = _gateway()
    channel = _channel(gateway)

    receipt_id = channel.submit(_package(), [])

    assert receipt_id == "RCPT-2026-000001"
    filing = gateway.filings[0]
    assert filing["payload"] == {
        "currency": "GHS",
        "lines": {"hqla:1.1": "1500", "totals:hqla_total_ghs": "1500"},
        "labels": {"hqla:1.1": "Treasury bills", "totals:hqla_total_ghs": "Total HQLA"},
    }
    manifest = filing["manifest"]
    assert isinstance(manifest, dict)
    assert manifest["return_version"] == "2026.1"
    assert manifest["institution_code"] == "0142"
    assert channel.last_detail["signing_kid"] == KID
    assert channel.last_detail["approved_by_role"] == "CFO"


def test_the_filed_documents_travel_with_the_filing_under_the_banks_signature() -> None:
    gateway = _gateway()
    channel = _channel(gateway)
    documents = [
        (
            "LCR-NSFR_2026-06-30.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK workbook",
        ),
        ("LCR-NSFR_2026-06-30.zip", "application/zip", b"PK csv sheets"),
        ("LCR-NSFR_2026-06-30.pdf", "application/pdf", b"%PDF-1.7"),
    ]
    channel.carry_documents(documents)

    channel.submit(_package(), [])

    filing = gateway.filings[0]
    manifest = filing["manifest"]
    assert isinstance(manifest, dict)
    # The manifest the bank signs lists each document by digest and size ...
    assert manifest["documents"] == [
        {
            "name": name,
            "media_type": media_type,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        for name, media_type, content in documents
    ]
    # ... and the bytes go alongside, matched by name.
    sent = filing["documents"]
    assert isinstance(sent, list)
    assert [(item["name"], base64.b64decode(item["content_base64"])) for item in sent] == [
        (name, content) for name, _, content in documents
    ]
    assert [d["name"] for d in channel.last_detail["documents"]] == [d[0] for d in documents]


def test_a_filing_without_documents_keeps_its_original_manifest_shape() -> None:
    gateway = _gateway()
    _channel(gateway).submit(_package(), [])
    filing = gateway.filings[0]
    assert "documents" not in filing
    manifest = filing["manifest"]
    assert isinstance(manifest, dict)
    assert "documents" not in manifest


def test_the_regulators_queries_are_read_with_the_read_scope() -> None:
    gateway = _gateway()
    queries = _channel(gateway).list_queries()
    assert [q["number"] for q in queries] == ["QRY-2026-0001", "QRY-2026-0002", "QRY-2026-0003"]
    tokens = [r for r in gateway.requests if r.url.path == "/oauth/token"]
    assert "scope=queries%3Aread" in tokens[-1].content.decode()


def test_acknowledging_and_answering_a_query_use_the_gateway_paths() -> None:
    gateway = _gateway()
    channel = _channel(gateway)
    channel.acknowledge_query("QRY-2026-0001")
    sent = channel.respond_to_query("QRY-2026-0001", "The formula workbook is attached.", None)
    assert gateway.query_calls == [
        ("/v1/queries/QRY-2026-0001/acknowledgements", None),
        (
            "/v1/queries/QRY-2026-0001/responses",
            {"body": "The formula workbook is attached.", "resubmission_receipt_id": None},
        ),
    ]
    assert sent["author"] == "institution"


def test_a_corrected_filing_answers_only_the_open_request_for_it() -> None:
    gateway = _gateway()
    answered = gateway_queries.answer_resubmission_requests(
        _channel(gateway), "SRG-26-0000002", "SRG-26-0000004"
    )
    # The open request for that receipt is answered with the new receipt; the plain
    # query and the closed request are left alone.
    assert answered == ["QRY-2026-0002"]
    assert gateway.query_calls == [
        (
            "/v1/queries/QRY-2026-0002/responses",
            {
                "body": "The corrected filing is SRG-26-0000004, filed as an amendment of "
                "SRG-26-0000002.",
                "resubmission_receipt_id": "SRG-26-0000004",
            },
        )
    ]


def test_a_filing_that_corrects_nothing_answers_nothing() -> None:
    gateway = _gateway()
    assert (
        gateway_queries.answer_resubmission_requests(
            _channel(gateway), "SRG-26-0000009", "SRG-26-0000010"
        )
        == []
    )
    assert gateway.query_calls == []


def test_the_same_package_revision_always_sends_the_same_idempotency_key() -> None:
    gateway = _gateway()
    _channel(gateway).submit(_package(), [])
    _channel(gateway).submit(_package(), [])

    keys = [
        request.headers["Idempotency-Key"]
        for request in gateway.requests
        if request.url.path == "/v1/submissions"
    ]
    assert len(keys) == 2
    assert keys[0] == keys[1]


def test_the_same_package_revision_is_the_same_filing_content_whenever_it_is_built() -> None:
    gateway = _gateway()
    saved = _package()
    saved.updated_at = NOW - timedelta(days=1)  # a stored package's last change
    first = _channel(gateway).build_filing(saved, "2026.1")
    later = ReportingGatewayChannel(
        config=_channel(gateway)._config,  # pyright: ignore[reportPrivateUsage]
        credentials={
            "client_private_key_pem": private_key_pem(gateway.client_key),
            "signing_private_key_pem": private_key_pem(gateway.signing_key),
        },
        currency="GHS",
        declaration={"prepared_by_role": "Head of Regulatory Reporting", "approved_by_role": "CFO"},
        transport=httpx.MockTransport(gateway),
        clock=lambda: NOW + timedelta(hours=3),
    ).build_filing(saved, "2026.1")

    assert later["manifest"] == first["manifest"]
    assert later["declaration"] == first["declaration"]
    assert first["manifest"]["created_at"] == "2026-11-20T12:00:00Z"


_CORRECTION: dict[str, object] = {
    "prepared_by_role": "Head of Regulatory Reporting",
    "approved_by_role": "CFO",
    "amendment_reason": "Reclassified a treasury bill holding.",
}


def test_a_correction_amends_the_receipt_the_gateway_holds() -> None:
    gateway = _gateway(held_receipts=frozenset({"SRG-26-0000001"}))
    channel = _channel(gateway, declaration=_CORRECTION, supersedes_receipt_id="SRG-26-0000001")

    channel.submit(_package(), [])

    posted = [r.url.path for r in gateway.requests if r.method == "POST"][-1]
    assert posted == "/v1/submissions/SRG-26-0000001/amendments"
    filing = gateway.filings[0]
    assert filing["manifest"]["supersedes_receipt_id"] == "SRG-26-0000001"  # type: ignore[index]
    assert filing["declaration"]["amendment_reason"] == _CORRECTION["amendment_reason"]  # type: ignore[index]
    assert "superseded_receipt_not_on_gateway" not in channel.last_detail


def test_a_correction_of_a_receipt_the_gateway_no_longer_holds_is_filed_as_an_original() -> None:
    # A gateway environment reset (or restored from an earlier point) has no record
    # of the filing this version corrects: there is nothing to amend there.
    gateway = _gateway()
    channel = _channel(gateway, declaration=_CORRECTION, supersedes_receipt_id="SRG-26-0000001")

    channel.submit(_package(), [])

    posted = [r.url.path for r in gateway.requests if r.method == "POST"][-1]
    assert posted == "/v1/submissions"
    filing = gateway.filings[0]
    assert filing["manifest"]["supersedes_receipt_id"] is None  # type: ignore[index]
    assert filing["declaration"]["amendment_reason"] is None  # type: ignore[index]
    assert channel.last_detail["supersedes_receipt_id"] is None
    assert channel.last_detail["superseded_receipt_not_on_gateway"] == "SRG-26-0000001"


def test_a_failed_receipt_check_stops_the_correction() -> None:
    # Only "no such filing" releases the link; any other refusal is not evidence
    # that the gateway lost the receipt.
    gateway = _gateway()

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/submissions/SRG-26-0000001":
            gateway.requests.append(request)
            return httpx.Response(403, json={"code": "insufficient_scope"})
        return gateway(request)

    channel = _channel(gateway, declaration=_CORRECTION, supersedes_receipt_id="SRG-26-0000001")
    channel._transport = httpx.MockTransport(refusing)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ChannelError, match="do not allow filing returns"):
        channel.submit(_package(), [])
    assert gateway.filings == []


def test_a_filing_the_gateway_has_no_record_of_is_said_plainly() -> None:
    with pytest.raises(ChannelError) as exc:
        _channel(_gateway()).poll_with_detail("SRG-26-0000001")

    assert exc.value.operator_message.startswith(
        "The AequorOS Reporting Gateway has no record of filing SRG-26-0000001."
    )
    assert "record a correction" in exc.value.operator_message


def test_a_receipt_signed_by_an_unknown_key_is_refused() -> None:
    gateway = _gateway(signs_receipts_with=generate_p256_key())

    with pytest.raises(ChannelError, match="receipt could not be verified"):
        _channel(gateway).submit(_package(), [])


def test_a_receipt_that_differs_from_the_filing_sent_is_refused() -> None:
    gateway = _gateway(tamper_receipt=True)

    with pytest.raises(ChannelError, match="does not match the filing sent"):
        _channel(gateway).submit(_package(), [])


def test_gateway_errors_reach_the_bank_only_as_safe_wording() -> None:
    gateway = _gateway(submission_error=(422, "signing_key_revoked"))

    with pytest.raises(ChannelError) as raised:
        _channel(gateway).submit(_package(), [])

    assert raised.value.operator_message == (
        "Your bank's signature key has been withdrawn on the gateway."
    )
    assert "internal detail" not in raised.value.operator_message


def test_an_unenrolled_bank_is_told_to_ask_its_aequoros_team() -> None:
    gateway = _gateway()

    with pytest.raises(ChannelPreconditionError, match="Ask your AequorOS team to enrol it"):
        _channel(gateway, config={}).submit(_package(), [])
    assert gateway.requests == []


def test_only_an_approved_package_can_be_filed() -> None:
    package = _package()
    package.status = "draft"

    with pytest.raises(ChannelPreconditionError, match="Only an approved package"):
        _channel(_gateway()).submit(package, [])


def test_figures_are_filed_exactly_in_the_template_shape() -> None:
    payload = gateway_payload(
        _package([{"code": "1.1", "description": "Treasury  bills", "value": "1500.5000"}]), "GHS"
    )
    assert payload == {
        "currency": "GHS",
        "lines": {"hqla:1.1": "1500.5", "totals:hqla_total_ghs": "1500"},
        "labels": {"hqla:1.1": "Treasury bills", "totals:hqla_total_ghs": "Total HQLA"},
    }


def test_a_return_the_gateway_does_not_carry_is_refused() -> None:
    package = _package()
    package.return_code = "ICAAP-REPORT"
    with pytest.raises(ChannelPreconditionError, match="does not take ICAAP-REPORT"):
        gateway_payload(package, "GHS")


def test_a_number_line_carrying_text_is_refused() -> None:
    with pytest.raises(ChannelPreconditionError, match="must be a number"):
        gateway_payload(
            _package([{"code": "1.1", "description": "Bills", "value": "about 1500"}]), "GHS"
        )


def test_vault_signing_key_must_be_p256() -> None:
    other_curve = ec.generate_private_key(ec.SECP384R1())

    with pytest.raises(KeyUnavailableError):
        VaultSigningProvider(private_key_pem(other_curve))


def test_vault_signatures_verify_as_es256_jws() -> None:
    key = generate_p256_key()
    provider = VaultSigningProvider(private_key_pem(key))
    digest = hashlib.sha256(b"manifest").digest()

    der = provider.sign_digest(KeyReference("vault", KID, "", ""), digest)

    key.public_key().verify(der, b"manifest", ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"b": 1, "a": [True, None, "é"]}, '{"a":[true,null,"é"],"b":1}'),
        ({"€": 1, "\r": 2}, '{"\\r":2,"€":1}'),
        ({"n": 10**15}, '{"n":1000000000000000}'),
    ],
)
def test_canonical_json_follows_rfc_8785(value: object, expected: str) -> None:
    assert canonical_json(value) == expected.encode("utf-8")


@pytest.mark.parametrize("value", [1.5, 2**60, {1: "x"}])
def test_canonical_json_refuses_values_two_implementations_could_render_differently(
    value: object,
) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json(value)
