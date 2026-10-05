from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.temenos_t24.adapter import TemenosT24Adapter
from app.adapters.temenos_t24.catalog import load_mode_catalog
from app.adapters.temenos_t24.domains import CoreBankingDomain
from app.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.api.deps import TenantContext
from app.domain.ingestion.contracts import AdapterConfig
from app.domain.ingestion.validation import (
    SETTLED_IDENTITY_RULE,
    PositionIdentity,
    ValidationContext,
    default_validation_config,
    run_validation,
)
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.services.ingestion import create_mapping_config, start_ingestion
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

REFERENCE = "T24-CORRECTION-1"
HELD_DATE = date(2042, 1, 1)


def stage_bundle(
    path: Path, mode: str, domain: CoreBankingDomain, values: dict[str, Any]
) -> Path:
    entry = load_mode_catalog(mode).entries[domain]
    native_fields = {
        native: values[canonical]
        for native, canonical in entry.field_map.items()
        if canonical in values
    }
    path.write_text(
        json.dumps(
            {
                "mode": mode,
                "domains": [
                    {"domain": domain.name, "records": [{"id": REFERENCE, **native_fields}]}
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("mode", ["IRIS", "OPEN_API"])
@pytest.mark.parametrize(
    "domain",
    [
        CoreBankingDomain.POSITIONS_LOANS,
        CoreBankingDomain.POSITIONS_DEPOSITS,
        CoreBankingDomain.POSITIONS_CURRENT_ACCOUNTS,
        CoreBankingDomain.POSITIONS_MM_PLACEMENTS,
        CoreBankingDomain.POSITIONS_MM_BORROWINGS,
        CoreBankingDomain.POSITIONS_SWAPS,
    ],
)
@pytest.mark.parametrize(
    ("date_input", "refused"),
    [
        ({}, False),
        ({"origination_date": None}, True),
        ({"origination_date": ""}, True),
        ({"origination_date": "20420101"}, False),
        ({"origination_date": "20240110"}, True),
    ],
)
def test_t24_bundle_preserves_origination_date_presence_through_validation(
    tmp_path: Path,
    mode: str,
    domain: CoreBankingDomain,
    date_input: dict[str, Any],
    refused: bool,
) -> None:
    path = stage_bundle(
        tmp_path / "t24.json", mode, domain, {"currency": "GHS", "balance": "1250", **date_input}
    )
    adapter = TemenosT24Adapter()
    extraction = adapter.extract(AdapterConfig(location=str(path)), date(2026, 7, 31), ["position"])
    assert extraction.warnings == []
    records = adapter.translate(extraction, default_t24_mapping_config(mode))
    assert records.failures == []
    (position,) = records.positions
    assert ("origination_date" in position.model_fields_set) == bool(date_input)
    outcome = run_validation(
        records,
        default_validation_config(),
        ValidationContext(
            as_of_date=extraction.as_of_date,
            settled_positions={REFERENCE: PositionIdentity(position.position_type, "GHS", HELD_DATE)},
        ),
    )
    findings = [finding for finding in outcome.findings if finding.rule == SETTLED_IDENTITY_RULE]
    assert len(findings) == int(refused)
    if refused:
        assert findings[0].detail.startswith("origination_date is frozen")
        assert outcome.record_statuses[("position", REFERENCE)] == "error"


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
        IngestionBatchCreate(
            source_system="T24",
            as_of_date=date(2026, 6, 30),
            location=str(initial_path),
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
        IngestionBatchCreate(
            source_system="T24",
            as_of_date=date(2026, 7, 31),
            location=str(corrected_path),
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
