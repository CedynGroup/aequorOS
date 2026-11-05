"""Tests for test_identity_corrections."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.data_engine.adapters.temenos_t24.domains import CoreBankingDomain
from app.data_engine.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.data_engine.domain.ingestion.validation import (
    SETTLED_IDENTITY_RULE,
)
from app.data_engine.schemas.ingestion import InternalIngestionBatchCreate, MappingConfigCreate
from app.data_engine.service.ingestion import create_mapping_config, start_ingestion
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from tests.data_engine.adapters.temenos_t24.test_identity_corrections import (
    HELD_DATE,
    REFERENCE,
    stage_bundle,
)
from tests.support.helpers import ORG_1, USER_1
from tests.support.inmemory_storage import InMemoryStorageClient


@pytest.mark.parametrize("mode", ["IRIS", "OPEN_API"])
@pytest.mark.parametrize(
    ("initial_currency", "date_input"),
    [
        ("GHZ", {}),
        ("GHZ", {"origination_date": None}),
        ("GHS", {}),
        ("GHS", {"origination_date": None}),
    ],
)
def test_t24_bundle_date_correction_updates_or_refuses_the_persisted_identity(
    db_session: Session,
    tmp_path: Path,
    mode: str,
    initial_currency: str,
    date_input: dict[str, Any],
) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="T24 identity test bank",
        short_name="t24-identity",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.commit()
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    create_mapping_config(
        db_session,
        ctx,
        bank.id,
        MappingConfigCreate(
            source_system="T24",
            name="T24 identity mapping",
            config=default_t24_mapping_config(mode),
            activate=True,
            reason="Identity correction regression.",
        ),
    )
    storage = InMemoryStorageClient()
    initial_path = stage_bundle(
        tmp_path / "initial.json",
        mode,
        CoreBankingDomain.POSITIONS_LOANS,
        {"currency": initial_currency, "balance": "1250", "origination_date": "20420101"},
    )
    initial = start_ingestion(
        db_session,
        ctx,
        bank.id,
        InternalIngestionBatchCreate(
            source_system="T24",
            as_of_date=date(2026, 6, 30),
            location=initial_path,
            reason="Initial position.",
        ),
        storage,
    )
    assert initial.batch.records_error == int(initial_currency == "GHZ")
    corrected_path = stage_bundle(
        tmp_path / "corrected.json",
        mode,
        CoreBankingDomain.POSITIONS_LOANS,
        {"currency": "GHS", "balance": "1500", **date_input},
    )
    corrected = start_ingestion(
        db_session,
        ctx,
        bank.id,
        InternalIngestionBatchCreate(
            source_system="T24",
            as_of_date=date(2026, 7, 31),
            location=corrected_path,
            reason="Correct the position.",
        ),
        storage,
    )
    refused = initial_currency == "GHS" and bool(date_input)
    assert corrected.batch.records_error == int(refused)
    failures = corrected.batch.validation_report["failures"]
    assert any(failure["rule"] == SETTLED_IDENTITY_RULE for failure in failures) == refused
    db_session.expire_all()
    position = db_session.scalars(
        select(CanonicalPosition).where(
            CanonicalPosition.bank_id == bank.id,
            CanonicalPosition.source_reference == REFERENCE,
        )
    ).one()
    assert position.currency == "GHS"
    cleared = initial_currency == "GHZ" and bool(date_input)
    assert position.origination_date == (None if cleared else HELD_DATE)
    snapshot = db_session.scalars(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.position_id == position.id,
            CanonicalPositionSnapshot.as_of_date == date(2026, 7, 31),
        )
    ).one()
    assert snapshot.validation_status == ("error" if refused else "accepted")
