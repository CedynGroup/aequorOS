from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer_group

from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.presentation import HEADER, LOCKED_TEXT, SESSION_SUPPORT
from app.core.key_management.types import KeyStatus
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPositionSnapshot,
    ParamConcentrationLimit,
)
from app.schemas.regulatory_credit import (
    ConcentrationLimitEntry,
    ConcentrationLimitRegisterRead,
    ConcentrationLimitUpdate,
    CreditConcentrationRead,
)
from app.services import credit_concentration, credit_params, fact_derivation, regulatory_credit
from tests.api import test_impersonation_auth as impersonation
from tests.core import test_key_unavailable_numeric as encrypted
from tests.core.test_bank_field_encryption import KEY_A
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.services.test_credit_concentration import CTX
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, headers

type Prefix = Literal["group", "cp"]
type Kind = Literal["share_of_book_pct", "share_of_capital_pct"]


@pytest.mark.parametrize("connected", [False, True])
def test_support_register_reads_hide_named_buckets_without_mutating_limits(
    db_session: Session,
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
) -> None:
    materialize_canonical_test_book(db_session)
    if connected:
        encrypted.connect(db_session, monkeypatch)
    entries = [
        ConcentrationLimitEntry(
            dimension="single_name",
            limit_kind=kind,
            bucket_key=key,
            value=Decimal(index + 1),
        )
        for index, (kind, key) in enumerate(
            (
                ("share_of_book_pct", "cp:Acme"),
                ("share_of_capital_pct", "group:Private Holdings"),
                ("hhi", "Private Borrower"),
                ("share_of_book_pct", "pos:LOAN/1"),
                ("share_of_book_pct", None),
            )
        )
    ]
    entries.append(
        ConcentrationLimitEntry(
            dimension="sector", limit_kind="hhi", bucket_key="Manufacturing", value=Decimal(6)
        )
    )
    expected = credit_params.update_concentration_limit_register(
        db_session,
        CTX,
        SAMPLE_BANK_ID,
        ConcentrationLimitUpdate(
            effective_from=date(2026, 1, 1),
            approved_by="Synthetic Board",
            reason="Synthetic support register presentation regression",
            limits=entries,
        ),
    )
    rows = list(
        db_session.scalars(select(ParamConcentrationLimit).order_by(ParamConcentrationLimit.id))
    )
    stored = [(row.id, row.bucket_key, row.value) for row in rows]
    url = f"/api/v1/banks/{SAMPLE_BANK_ID}/concentration-limits?as_of=2026-01-01"
    normal = db_client.get(url, headers=headers())
    assert normal.status_code == 200, normal.text
    assert ConcentrationLimitRegisterRead.model_validate_json(normal.content) == expected
    token = impersonation._impersonation_token(ORG_1)  # pyright: ignore[reportPrivateUsage]
    staff = db_client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert staff.status_code == 200, staff.text
    assert TypeAdapter(dict[str, str]).validate_json(staff.headers[HEADER]) == {
        SAMPLE_BANK_ID: "support"
    }
    assert staff.headers["Cache-Control"] == "no-store"
    for name in ("Acme", "Private Holdings", "Private Borrower"):
        assert name not in staff.text
    masked = expected.model_copy(deep=True)
    for limit in masked.limits:
        if limit.bucket_key in ("cp:Acme", "group:Private Holdings", "Private Borrower"):
            limit.bucket_key = LOCKED_TEXT
    assert ConcentrationLimitRegisterRead.model_validate_json(staff.content) == masked
    db_session.info[SESSION_SUPPORT] = True
    assert (
        credit_params.get_concentration_limit_register(
            db_session, CTX, SAMPLE_BANK_ID, date(2026, 1, 1)
        )
        == masked
    )
    db_session.info.pop(SESSION_SUPPORT)
    db_session.expire_all()
    assert [(row.id, row.bucket_key, row.value) for row in rows] == stored
    restored = db_client.get(url, headers=headers())
    assert ConcentrationLimitRegisterRead.model_validate_json(restored.content) == expected


def prepare(
    db: Session, monkeypatch: pytest.MonkeyPatch, prefix: Prefix, *, connected: bool
) -> tuple[Bank, LocalKeyProvider | None, str]:
    materialize_canonical_test_book(db)
    provider = encrypted.connect(db, monkeypatch) if connected else None
    seed_canonical_fixture(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    parties = list(
        db.scalars(select(CanonicalCounterparty).options(undefer_group("bank_identity")))
    )
    assert parties
    for party in parties:
        party.name = "Acme"
        party.group_reference = "Acme" if prefix == "group" else None
        party.attributes = {
            key: value
            for key, value in cast(dict[str, object], party.attributes).items()
            if key not in ("group_reference", "group", "parent")
        }
    for snapshot in db.scalars(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
            CanonicalPositionSnapshot.as_of_date == FIXTURE_AS_OF,
        )
    ):
        snapshot.attributes = {
            **cast(dict[str, object], snapshot.attributes),
            "sector": "Synthetic sector",
        }
    fact_derivation.derive_facts(db, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    fact_derivation.derive_current_facts(db, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db.commit()
    token = parties[0].concentration_group_blind_index
    assert token is not None
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank, provider, token


def limits(db: Session, kind: Kind, *keys: str) -> None:
    credit_params.update_concentration_limit_register(
        db,
        CTX,
        SAMPLE_BANK_ID,
        ConcentrationLimitUpdate(
            effective_from=date(2026, 1, 1),
            approved_by="Synthetic Board",
            reason="Synthetic named concentration-limit regression",
            limits=[
                *(
                    ConcentrationLimitEntry(
                        dimension="single_name",
                        limit_kind=kind,
                        bucket_key=key,
                        value=Decimal("0.01"),
                    )
                    for key in keys
                ),
                ConcentrationLimitEntry(
                    dimension="single_name", limit_kind=kind, value=Decimal("100000")
                ),
                ConcentrationLimitEntry(dimension="sector", limit_kind="hhi", value=Decimal("1")),
            ],
        ),
    )
    db.commit()


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("prefix", ["group", "cp"])
@pytest.mark.parametrize("kind", ["share_of_book_pct", "share_of_capital_pct"])
def test_named_limits_match_the_opaque_group_and_breach(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
    prefix: Prefix,
    kind: Kind,
) -> None:
    bank, _provider, token = prepare(db_session, monkeypatch, prefix, connected=connected)
    limits(db_session, kind, f"{prefix}:Acme")
    db_session.expunge_all()
    engine = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    assert any(b.key == token for b in engine.breaches)
    reading = credit_concentration.concentration_read(db_session, CTX, bank, FIXTURE_AS_OF)
    single = next(d for d in reading.dimensions if d.dimension == "single_name")
    matched = next(b for b in single.buckets if b.key == f"{prefix}:Acme")
    assert matched.limit_status == "above_limit" and matched.limit_kind == kind
    assert matched.limit_value == Decimal("0.01")
    assert matched.utilization_pct is not None and matched.utilization_pct > 100
    assert matched in reading.breaches
    assert reading.locked_limits == [] and reading.limit_count == 3


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
@pytest.mark.parametrize("prefix", ["group", "cp"])
@pytest.mark.parametrize("kind", ["share_of_book_pct", "share_of_capital_pct"])
def test_retained_named_limits_are_locked_on_the_api_when_key_is_unavailable(  # noqa: PLR0913
    db_session: Session,
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    status: KeyStatus,
    prefix: Prefix,
    kind: Kind,
) -> None:
    bank, provider, token = prepare(db_session, monkeypatch, prefix, connected=True)
    assert provider is not None
    limits(db_session, kind, f"{prefix}:Acme")
    engine = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    assert any(b.key == token for b in engine.breaches)
    before = credit_concentration.concentration_read(db_session, CTX, bank, FIXTURE_AS_OF)
    assert any(b.key == f"{prefix}:Acme" for b in before.breaches)
    db_session.commit()
    db_session.expunge_all()
    provider.set_status(KEY_A, status)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/credit/concentration", headers=headers()
    )
    assert response.status_code == 200, response.text
    assert TypeAdapter(dict[str, str]).validate_json(response.headers[HEADER]) == {
        SAMPLE_BANK_ID: "revoked" if status == KeyStatus.DISABLED else "unavailable"
    }
    assert "Acme" not in response.text
    after = CreditConcentrationRead.model_validate(cast(object, response.json()))
    assert after.total_book_ghs == before.total_book_ghs and after.limit_count == 3
    assert len(after.locked_limits) == 1
    locked = after.locked_limits[0]
    assert locked.limit_status == "locked"
    assert locked.limit_kind == kind and locked.value == Decimal("0.01")
    single = next(d for d in after.dimensions if d.dimension == "single_name")
    bucket = next(b for b in single.buckets if b.key == LOCKED_TEXT)
    assert bucket.limit_status == "locked" and bucket.utilization_pct is None
    assert bucket.limit_value is None
    sector = next(d for d in after.dimensions if d.dimension == "sector")
    assert sector.hhi_status == "above_limit"
    assert all(b.key != LOCKED_TEXT for b in after.breaches)
    retained = credit_params.get_concentration_limit_register(
        db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF
    )
    assert any(limit.bucket_key == f"{prefix}:Acme" for limit in retained.limits)
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == FIXTURE_AS_OF,
        )
    )
    assert period is not None
    live = regulatory_credit.compute_live(db_session, CTX, bank, period)
    assert any(finding.rule_id == "concentration_limits_locked" for finding in live.findings)


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("kind", ["share_of_book_pct", "share_of_capital_pct"])
def test_literal_pos_limits_still_breach_with_a_locked_named_limit(
    db_session: Session,
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
    kind: Kind,
) -> None:
    bank, provider, _token = prepare(db_session, monkeypatch, "group", connected=connected)
    snapshot = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
            CanonicalPositionSnapshot.as_of_date == FIXTURE_AS_OF,
            CanonicalPositionSnapshot.source_reference == "LOAN/1",
        )
    )
    assert snapshot is not None
    snapshot.counterparty_id = None
    reference = snapshot.source_reference
    limits(db_session, kind, f"pos:{reference}", "group:Acme")
    db_session.expunge_all()
    if provider is not None:
        provider.set_status(KEY_A, KeyStatus.DISABLED)
    engine = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    assert any(b.key == f"pos:{reference}" for b in engine.breaches)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/credit/concentration", headers=headers()
    )
    assert response.status_code == 200, response.text
    reading = CreditConcentrationRead.model_validate_json(response.content)
    matched = next(b for b in reading.breaches if b.key == f"pos:{reference}")
    assert matched.limit_status == "above_limit" and matched.limit_kind == kind
    assert matched.limit_value == Decimal("0.01")
    assert len(reading.locked_limits) == int(connected)
