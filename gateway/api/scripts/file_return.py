# pyright: standard
"""Reference filing client for the AequorOS Reporting Gateway (SPEC.md Parts 1 and 5).

Self-contained: it uses only ``cryptography``, PyJWT and the standard library, never the
gateway's own code, so connectors and the conformance suite can follow it line by line.

1. (optional) Enrol the institution through the operator API with the operator's client.
2. Obtain an access token: mutual TLS + ``private_key_jwt`` client assertion.
3. Build the payload, declaration and manifest; sign JCS(manifest) as a compact JWS.
4. File BSD2 (or an amendment if a current version exists) with an Idempotency-Key.
5. Verify the gateway receipt offline against ``/v1/.well-known/jwks.json``.

Locally (``http://``) the client certificate is forwarded in ``X-Forwarded-Tls-Client-Cert``
as the ingress would; against ``https://`` it is presented in the TLS handshake.

    uv run python scripts/file_return.py --base-url http://localhost:8200 \\
        --operator-dir .keys/operator --state-dir .keys/reference-client
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from jwt.api_jws import PyJWS

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
BSD2_EXAMPLE = {  # GHS thousands; synthetic figures that satisfy BSD2's arithmetic rules
    "1.1": 412350,
    "1.2": 286400,
    "1.3": 1184900,
    "1.4": 2031770,
    "1.5": 143210,
    "1": 4058630,
    "2.1": 3112480,
    "2.2": 214900,
    "2.3": 186050,
    "2.4": 545200,
    "2": 4058630,
}


# --- RFC 8785 for the JSON this client sends (objects, arrays, strings, integers, literals) ---


def jcs(value: Any) -> bytes:
    def encode(v: Any) -> str:
        if v is None or isinstance(v, bool):
            return json.dumps(v)
        if isinstance(v, int):
            if abs(v) > 2**53 - 1:
                raise ValueError("integer outside the exact IEEE 754 range")
            return str(v)
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)  # the escaping RFC 8785 requires
        if isinstance(v, list):
            return "[" + ",".join(encode(item) for item in v) + "]"
        if isinstance(v, dict):
            members = sorted(v.items(), key=lambda kv: kv[0].encode("utf-16-be"))
            return (
                "{"
                + ",".join(json.dumps(k, ensure_ascii=False) + ":" + encode(m) for k, m in members)
                + "}"
            )
        raise TypeError(f"not canonicalisable here: {type(v).__name__}")

    return encode(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk_of(key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(numbers.x.to_bytes(32, "big")),
        "y": b64url(numbers.y.to_bytes(32, "big")),
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
    }


def jwk_public_key(jwk: dict[str, str]) -> ec.EllipticCurvePublicKey:
    def num(text: str) -> int:
        return int.from_bytes(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)), "big")

    return ec.EllipticCurvePublicNumbers(num(jwk["x"]), num(jwk["y"]), ec.SECP256R1()).public_key()


# --- HTTP with the client certificate ---------------------------------------------------------


class Client:
    def __init__(self, base_url: str, cert_file: Path, key_file: Path) -> None:
        self.base = base_url.rstrip("/")
        self.cert_pem = cert_file.read_text()
        self.https = self.base.startswith("https://")
        self.context = None
        if self.https:
            self.context = ssl.create_default_context()
            self.context.load_cert_chain(str(cert_file), str(key_file))
        self.token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        hdrs = dict(headers or {})
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if not self.https:
            hdrs["X-Forwarded-Tls-Client-Cert"] = urllib.parse.quote(self.cert_pem)
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None

    def get_token(
        self, client_id: str, key: ec.EllipticCurvePrivateKey, kid: str, scope: str | None = None
    ) -> str:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": client_id,
                "sub": client_id,
                "aud": self.base + "/oauth/token",
                "iat": now,
                "exp": now + 120,
                "jti": uuid.uuid4().hex,
            },
            key,
            algorithm="ES256",
            headers={"kid": kid},
        )
        form = {
            "grant_type": "client_credentials",
            "client_assertion_type": ASSERTION_TYPE,
            "client_assertion": assertion,
        }
        if scope:
            form["scope"] = scope
        status, body = self.request("POST", "/oauth/token", form=form)
        if status != 200:
            raise SystemExit(f"token refused ({status}): {body}")
        token: str = body["access_token"]
        self.token = token
        return token


def load_key(path: Path) -> ec.EllipticCurvePrivateKey:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        path.chmod(0o600)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return key


def csr_for(key: ec.EllipticCurvePrivateKey, name: str) -> str:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


# --- enrolment through the operator API ---------------------------------------------------------


def enrol(
    args: argparse.Namespace,
    state: Path,
    client_key: ec.EllipticCurvePrivateKey,
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    operator_dir = Path(args.operator_dir)
    descriptor = json.loads((operator_dir / "operator.json").read_text())
    operator_key = load_key(operator_dir / descriptor["key_file"])
    operator = Client(
        args.base_url,
        operator_dir / descriptor["certificate_file"],
        operator_dir / descriptor["key_file"],
    )
    operator.get_token(descriptor["client_id"], operator_key, descriptor["kid"])
    status, body = operator.request(
        "POST",
        "/ops/api/institutions",
        body={
            "code": args.code,
            "name": args.name,
            "type": args.type,
            "contacts": [
                {
                    "role": "Technical contact",
                    "name": "Reporting Systems",
                    "email": "reporting@example.org",
                }
            ],
        },
    )
    print(f"enrol institution {args.code}: {status}")
    if status not in (200, 201):
        raise SystemExit(body)
    client_kid = (
        "client-"
        + b64url(
            hashlib.sha256(
                jcs({k: jwk_of(client_key, "")[k] for k in ("crv", "kty", "x", "y")})
            ).digest()
        )[:16]
    )
    status, issued = operator.request(
        "POST",
        f"/ops/api/institutions/{args.code}/clients",
        body={
            "environment": "production",
            "csr_pem": csr_for(client_key, args.code),
            "jwks": {"keys": [jwk_of(client_key, client_kid)]},
            "software": {"name": "SRG reference client", "version": "0.1"},
        },
    )
    print(f"issue client certificate: {status}")
    if status not in (200, 201):
        raise SystemExit(issued)
    (state / "client.crt.pem").write_text(issued["certificate_pem"])
    (state / "client.json").write_text(
        json.dumps({"client_id": issued["client_id"], "kid": client_kid}, indent=2)
    )
    status, body = operator.request(
        "POST",
        f"/ops/api/institutions/{args.code}/signing-keys",
        body={
            "kid": f"{args.code}-sign-ref-1",
            "public_jwk": jwk_of(signing_key, f"{args.code}-sign-ref-1"),
        },
    )
    print(f"register signing key: {status}")
    if status not in (200, 201):
        raise SystemExit(body)
    status, body = operator.request(
        "POST",
        f"/ops/api/institutions/{args.code}/status",
        body={"status": "live", "reason": "Reference client enrolment"},
    )
    print(f"set live: {status}")


# --- filing ------------------------------------------------------------------------------------


def build_filing(
    code: str,
    version: str,
    reporting_date: str,
    lines: dict[str, int],
    signing_key: ec.EllipticCurvePrivateKey,
    kid: str,
    supersedes: str | None,
) -> dict[str, Any]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload = {"currency": "GHS", "unit": "thousands", "lines": lines}
    declaration = {
        "prepared_by_role": "Head of Financial Reporting",
        "approved_by_role": "Chief Finance Officer",
        "approved_at": now,
        "amendment_reason": "Reference client re-run" if supersedes else None,
        "software": {"name": "SRG reference client", "version": "0.1"},
    }
    manifest = {
        "institution_code": code,
        "return_code": "BSD2",
        "return_version": version,
        "reporting_date": reporting_date,
        "basis": "solo",
        "payload_sha256": sha256_hex(jcs(payload)),
        "declaration_sha256": sha256_hex(jcs(declaration)),
        "supersedes_receipt_id": supersedes,
        "created_at": now,
    }
    signature = PyJWS().encode(
        jcs(manifest),
        signing_key,
        algorithm="ES256",
        headers={"kid": kid, "typ": "srg-manifest+jws"},
    )
    return {
        "manifest": manifest,
        "payload": payload,
        "declaration": declaration,
        "signature": signature,
    }


def verify_receipt(client: Client, envelope: dict[str, Any], filing: dict[str, Any]) -> None:
    _, jwks = client.request("GET", "/v1/.well-known/jwks.json")
    token = envelope["receipt_jws"]
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    if header.get("typ") != "srg-receipt+jws" or header.get("alg") != "ES256":
        raise SystemExit(f"unexpected receipt header {header}")
    jwk = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    payload = PyJWS().decode_complete(token, key=jwk_public_key(jwk), algorithms=["ES256"])[
        "payload"
    ]
    receipt = envelope["receipt"]
    checks = {
        "signature covers JCS(receipt)": payload == jcs(receipt),
        "payload digest": receipt["payload_sha256"] == filing["manifest"]["payload_sha256"],
        "manifest digest": receipt["manifest_sha256"] == sha256_hex(jcs(filing["manifest"])),
        "institution": receipt["institution_code"] == filing["manifest"]["institution_code"],
    }
    for label, ok in checks.items():
        print(f"  receipt check · {label}: {'ok' if ok else 'FAILED'}")
    if not all(checks.values()):
        raise SystemExit("receipt verification failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://localhost:8200")
    parser.add_argument(
        "--operator-dir", default=None, help="enrol first with this operator client directory"
    )
    parser.add_argument("--state-dir", default=".keys/reference-client")
    parser.add_argument("--code", default="0901")
    parser.add_argument("--name", default="Example Reference Bank")
    parser.add_argument("--type", default="universal_bank")
    parser.add_argument("--reporting-date", default="2026-10-31")
    args = parser.parse_args()
    state = Path(args.state_dir) / args.code
    state.mkdir(parents=True, exist_ok=True)
    client_key = load_key(state / "client.key.pem")
    signing_key = load_key(state / "signing.key.pem")
    if args.operator_dir:
        enrol(args, state, client_key, signing_key)
    identity = json.loads((state / "client.json").read_text())
    client = Client(args.base_url, state / "client.crt.pem", state / "client.key.pem")
    client.get_token(identity["client_id"], client_key, identity["kid"])
    print(f"access token for {identity['client_id']}: ok")
    _, detail = client.request("GET", "/v1/returns/BSD2")
    version = next(
        v["version"]
        for v in reversed(detail["versions"])
        if v["status"] == "published"
        and v["first_reporting_date"] <= args.reporting_date
        and (v["last_reporting_date"] is None or args.reporting_date <= v["last_reporting_date"])
    )
    _, schema = client.request("GET", f"/v1/returns/BSD2/versions/{version}/schema")
    required = set(schema["schema"]["properties"]["lines"]["required"])
    if required != set(BSD2_EXAMPLE):
        raise SystemExit(f"BSD2 {version} expects lines {sorted(required)}")
    _, current = client.request(
        "GET", f"/v1/submissions?reporting_date={args.reporting_date}&return_code=BSD2"
    )
    live = [s for s in current["items"] if s["status"] != "superseded"]
    supersedes = live[0]["receipt_id"] if live else None
    filing = build_filing(
        args.code,
        version,
        args.reporting_date,
        BSD2_EXAMPLE,
        signing_key,
        f"{args.code}-sign-ref-1",
        supersedes,
    )
    path = f"/v1/submissions/{supersedes}/amendments" if supersedes else "/v1/submissions"
    key = str(uuid.uuid4())
    status, envelope = client.request("POST", path, body=filing, headers={"Idempotency-Key": key})
    print(
        f"filed BSD2 {version} for {args.reporting_date}{' as an amendment of ' + supersedes if supersedes else ''}: {status}"
    )
    if status != 202:
        raise SystemExit(envelope)
    replay_status, replay = client.request(
        "POST", path, body=filing, headers={"Idempotency-Key": key}
    )
    print(
        f"idempotent retry returned the original receipt: {replay_status == 202 and replay == envelope}"
    )
    print(
        f"receipt {envelope['receipt']['receipt_id']} · ledger entry {envelope['receipt']['ledger_seq']}"
    )
    verify_receipt(client, envelope, filing)
    receipt_id = envelope["receipt"]["receipt_id"]
    status_body: dict[str, Any] = {"status": "validating", "validation": None}
    for _ in range(20):
        _, status_body = client.request("GET", f"/v1/submissions/{receipt_id}")
        if status_body["status"] not in ("received", "validating"):
            break
        time.sleep(0.25)
    validation = status_body["validation"] or {}
    print(
        f"status {status_body['status']} · rules passed {validation.get('passed')} · failed {validation.get('failed')}"
        f" · warnings {validation.get('warnings')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
