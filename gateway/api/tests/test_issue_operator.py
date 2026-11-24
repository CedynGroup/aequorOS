# pyright: standard
"""Issuing a channel operator's credentials for a deployed gateway.

What is being protected: outside ``local``/``test`` no development operator exists, so
the AequorOS platform cannot enrol a single bank until an operator is issued from the
deployed gateway's CA and listed in ``GATEWAY_OPERATOR_CLIENTS``. The issued bundle must
be the layout AequorOS reads, its entry must configure a working operator, and the
command must never print a private key to a terminal or run where the CA is the Bank's.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.settings import get_settings
from app.enrolment import issue_operator
from app.enrolment.ca import LocalCertificateAuthority
from app.enrolment.operators import (
    configured_entry,
    issue_operator_bundle,
    parse_operator_clients,
)
from app.keys.provider import KeyConfigurationError
from tests.harness import Harness, cert_header

INSTITUTION = {
    "code": "0602",
    "name": "Northgate Savings and Loans",
    "type": "savings_and_loans",
    "contacts": [{"role": "Compliance officer", "name": "B. Person", "email": "b@ng.example"}],
}


def test_an_issued_operator_enrols_an_institution(harness: Harness, tmp_path: Path) -> None:
    ca = harness.ctx.ca
    assert ca is not None
    directory = tmp_path / "aequoros-platform"
    client = issue_operator_bundle(
        directory,
        ca,
        harness.clock,
        client_id="aequoros-platform",
        organisation="AequorOS platform",
    )

    # The layout a client reads from its operator directory.
    descriptor = json.loads((directory / "operator.json").read_text())
    assert descriptor["client_id"] == "aequoros-platform"
    assert descriptor["certificate_sha256"] == client.thumbprint
    assert (directory / "client.key.pem").stat().st_mode & 0o077 == 0
    certificate = x509.load_pem_x509_certificate((directory / "client.crt.pem").read_bytes())
    certificate.verify_directly_issued_by(ca.certificate())

    # Configured exactly as GATEWAY_OPERATOR_CLIENTS would configure it.
    (configured,) = parse_operator_clients(json.dumps([configured_entry(client)]))
    harness.ctx.operators[configured.client_id] = configured

    key = serialization.load_pem_private_key((directory / "client.key.pem").read_bytes(), None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    pem_text = (directory / "client.crt.pem").read_text()
    token = harness.token_response(client.client_id, key, descriptor["kid"], pem_text)
    assert token.status_code == 200, token.text
    headers = {
        **cert_header(pem_text),
        "Authorization": f"Bearer {token.json()['access_token']}",
    }
    created = harness.client.post("/ops/api/institutions", json=INSTITUTION, headers=headers)
    assert created.status_code == 201, created.text


def test_the_development_operators_id_and_odd_ids_are_refused(tmp_path: Path) -> None:
    from app.core.clock import SettableClock
    from tests.harness import START

    ca = LocalCertificateAuthority(tmp_path / "ca", SettableClock(START))
    for client_id in ("dev-operator-01", "AequorOS", "a"):
        with pytest.raises(KeyConfigurationError):
            issue_operator_bundle(
                tmp_path / client_id,
                ca,
                SettableClock(START),
                client_id=client_id,
                organisation="X",
            )


class _Stdout:
    """Standard output as the command sees it: a terminal or not, and a byte buffer."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty
        self.buffer = io.BytesIO()

    def isatty(self) -> bool:
        return self._tty


def _run(monkeypatch: pytest.MonkeyPatch, env: str, *, tty: bool) -> tuple[int, _Stdout]:
    monkeypatch.setenv("GATEWAY_ENV", env)
    get_settings.cache_clear()
    out = _Stdout(tty)
    monkeypatch.setattr(issue_operator.sys, "stdout", out)
    try:
        code = issue_operator.main(["--client-id", "aequoros-platform", "--organisation", "X"])
    finally:
        get_settings.cache_clear()
    return code, out


def test_a_private_key_is_never_printed_to_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = _run(monkeypatch, "sandbox", tty=True)
    assert code == 2
    assert out.buffer.getvalue() == b""


def test_production_issues_from_the_banks_ca_not_this_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, out = _run(monkeypatch, "production", tty=False)
    assert code == 2
    assert out.buffer.getvalue() == b""


def test_the_command_writes_the_bundle_and_audits_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, keys_dir: Path, sqlite_template: Path
) -> None:
    import shutil

    db = tmp_path / "gateway.db"
    shutil.copy(sqlite_template, db)
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("GATEWAY_KEYS_DIR", str(keys_dir))
    monkeypatch.setenv("GATEWAY_SCHEDULER_ENABLED", "false")
    code, out = _run(monkeypatch, "sandbox", tty=False)
    assert code == 0

    with tarfile.open(fileobj=io.BytesIO(out.buffer.getvalue())) as tar:
        names = set(tar.getnames())
        entry = json.loads(
            tar.extractfile("aequoros-platform/gateway-operator-client.json").read()  # type: ignore[union-attr]
        )
    assert {
        "aequoros-platform/client.key.pem",
        "aequoros-platform/client.crt.pem",
        "aequoros-platform/ca.crt.pem",
        "aequoros-platform/operator.json",
    } <= names
    (client,) = parse_operator_clients(json.dumps(entry))
    assert client.client_id == "aequoros-platform"
    # The gateway keeps no copy of the key.
    assert not list(keys_dir.rglob("aequoros-platform*"))

    import sqlite3

    with sqlite3.connect(db) as connection:
        action = connection.execute(
            "SELECT action, object_ref FROM audit_entries ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert action == ("Issued operator client credentials", "aequoros-platform")
