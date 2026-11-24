# pyright: standard
"""The audit hash chain: linking, signatures, verification, tamper detection, export."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from jwt.api_jws import PyJWS
from sqlalchemy import delete, text, update

from app.audit.chain import (
    GENESIS_HASH,
    SYSTEM,
    AppendOnlyViolation,
    append,
    compute_entry_hash,
    entry_body,
    verify,
)
from app.core.encoding import b64url_decode
from app.core.jcs import canonicalize
from app.db.models import AuditEntry
from app.keys.jwk import public_key_from_jwk, verify_raw
from tests.harness import Harness


def _append(harness: Harness, n: int) -> None:
    with harness.ctx.db.session() as session:
        for i in range(n):
            append(
                session,
                harness.ctx.keys,
                harness.ctx.clock,
                actor=SYSTEM,
                category="system",
                action=f"Test entry {i}",
                object_type="test",
                object_ref=str(i),
                detail={"i": i, "text": "ü"},
            )


def test_chain_links_and_signs_every_entry(any_harness: Harness) -> None:
    harness = any_harness
    _append(harness, 5)
    with harness.ctx.db.session() as session:
        entries = session.query(AuditEntry).order_by(AuditEntry.seq).all()
        assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
        assert entries[0].prev_hash == GENESIS_HASH
        for previous, entry in zip(entries, entries[1:], strict=False):
            assert entry.prev_hash == previous.entry_hash
        for entry in entries:
            expected = hashlib.sha256(
                (entry.prev_hash + "\n").encode() + canonicalize(entry_body(entry))
            ).hexdigest()
            assert entry.entry_hash == expected
            key = harness.ctx.keys.find("audit", entry.signature_kid)
            assert key is not None
            assert verify_raw(
                key.public_key, entry.entry_hash.encode(), b64url_decode(entry.signature)
            )
        result = verify(session, harness.ctx.keys)
    assert result.intact
    assert result.entries == 5


def test_orm_refuses_updates_and_deletes(harness: Harness) -> None:
    _append(harness, 1)
    with pytest.raises(AppendOnlyViolation), harness.ctx.db.session() as session:
        entry = session.get(AuditEntry, 1)
        assert entry is not None
        entry.action = "Changed"
        session.flush()
    with pytest.raises(AppendOnlyViolation), harness.ctx.db.session() as session:
        entry = session.get(AuditEntry, 1)
        session.delete(entry)
        session.flush()
    with pytest.raises(AppendOnlyViolation), harness.ctx.db.session() as session:
        session.execute(update(AuditEntry).values(action="x"))
    with pytest.raises(AppendOnlyViolation), harness.ctx.db.session() as session:
        session.execute(delete(AuditEntry))


def _tamper_sqlite(harness: Harness, sql: str) -> None:
    with harness.engine.begin() as connection:
        connection.exec_driver_sql(sql)


@pytest.mark.parametrize(
    ("label", "sql"),
    [
        (
            "detail edited",
            'UPDATE audit_entries SET detail = \'{"i": 99, "text": "x"}\' WHERE seq = 3',
        ),
        ("action edited", "UPDATE audit_entries SET action = 'Something else' WHERE seq = 3"),
        (
            "signature replaced",
            "UPDATE audit_entries SET signature = (SELECT signature FROM audit_entries WHERE seq = 2) WHERE seq = 3",
        ),
        ("entry removed", "DELETE FROM audit_entries WHERE seq = 3"),
    ],
)
def test_verify_detects_tampering(harness: Harness, label: str, sql: str) -> None:
    _append(harness, 5)
    _tamper_sqlite(harness, sql)
    response = harness.client.post(
        "/portal/api/audit/verify", headers=harness.staff("internal-audit")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intact"] is False, label
    assert body["first_bad_seq"] in (3, 4), label
    entry = harness.client.get("/portal/api/audit/3", headers=harness.staff("internal-audit"))
    if entry.status_code == 200 and label != "entry removed":
        assert entry.json()["signature_valid"] is False


def test_rehashing_a_tampered_entry_still_breaks_the_chain(harness: Harness) -> None:
    _append(harness, 4)
    with harness.ctx.db.session() as session:
        entry = session.get(AuditEntry, 2)
        assert entry is not None
        body = entry_body(entry)
        body["action"] = "Forged"
        forged_hash = compute_entry_hash(entry.prev_hash, body)
    _tamper_sqlite(
        harness,
        f"UPDATE audit_entries SET action = 'Forged', entry_hash = '{forged_hash}' WHERE seq = 2",
    )
    with harness.ctx.db.session() as session:
        result = verify(session, harness.ctx.keys)
    assert not result.intact
    assert result.first_bad_seq == 2  # the audit-key signature no longer matches


def test_postgres_trigger_refuses_update_delete_and_truncate(pg_harness: Harness) -> None:
    _append(pg_harness, 2)
    for statement in (
        "UPDATE audit_entries SET action = 'x' WHERE seq = 1",
        "DELETE FROM audit_entries WHERE seq = 1",
        "TRUNCATE audit_entries",
    ):
        with pytest.raises(Exception, match="append-only"), pg_harness.engine.begin() as connection:
            connection.execute(text(statement))


def test_postgres_tampering_is_detected(pg_harness: Harness) -> None:
    _append(pg_harness, 4)
    with pg_harness.engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(text("UPDATE audit_entries SET object_ref = 'tampered' WHERE seq = 2"))
    with pg_harness.ctx.db.session() as session:
        result = verify(session, pg_harness.ctx.keys)
    assert not result.intact
    assert result.first_bad_seq == 2


def test_export_bundle_verifies_independently(harness: Harness) -> None:
    _append(harness, 6)
    response = harness.client.get(
        "/portal/api/audit/export", headers=harness.staff("internal-audit")
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert set(archive.namelist()) == {
        "entries.jsonl",
        "jwks.json",
        "manifest.json",
        "manifest.jws",
    }
    jwks = json.loads(archive.read("jwks.json"))
    keys = {k["kid"]: public_key_from_jwk(k) for k in jwks["audit"]["keys"]}
    previous = None
    lines = archive.read("entries.jsonl").decode().splitlines()
    for line in lines:
        record = json.loads(line)
        body = {
            k: v
            for k, v in record.items()
            if k not in ("prev_hash", "entry_hash", "signature", "signature_kid")
        }
        if previous is not None:
            assert record["prev_hash"] == previous
        assert (
            hashlib.sha256((record["prev_hash"] + "\n").encode() + canonicalize(body)).hexdigest()
            == record["entry_hash"]
        )
        assert verify_raw(
            keys[record["signature_kid"]],
            record["entry_hash"].encode(),
            b64url_decode(record["signature"]),
        )
        previous = record["entry_hash"]
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["entries"] == len(lines)
    assert manifest["entries_sha256"] == hashlib.sha256(archive.read("entries.jsonl")).hexdigest()
    jws = archive.read("manifest.jws").decode()
    kid = json.loads(b64url_decode(jws.split(".")[0]))["kid"]
    assert PyJWS().decode_complete(jws, key=keys[kid], algorithms=["ES256"])[
        "payload"
    ] == canonicalize(manifest)
    assert (
        harness.client.get(
            "/portal/api/audit/export", headers=harness.staff("aboateng")
        ).status_code
        == 403
    )
