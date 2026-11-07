"""Shared Data Engine ingestion seeding and batch-driving helpers.

Extracted from the data engine ingestion integration module so suites
elsewhere can drive the ingestion journey without importing a collected
test module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.data_engine.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.data_engine.schemas.ingestion import IngestionUploadRead
from app.db.session import get_sessionmaker
from tests.data_engine.adapters.excel_csv import fixtures
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, USER_1, headers

AS_OF = str(fixtures.AS_OF)

FULL_MAPPING = MappingConfig(
    field_mappings={
        "gl_account": EntityMapping(
            source_table="GL",
            fields={
                "source_reference": "Code",
                "account_code": "Code",
                "name": "Label",
                "account_class": "Class",
            },
        ),
        "counterparty": EntityMapping(
            source_table="Customers",
            fields={
                "source_reference": "CustomerId",
                "name": "CustomerName",
                "counterparty_type": "Segment",
                "country_code": "Country",
            },
        ),
        "product": EntityMapping(
            source_table="Products",
            fields={
                "source_reference": "ProductCode",
                "product_code": "ProductCode",
                "name": "ProductName",
            },
        ),
        "position": EntityMapping(
            source_table="Loans",
            fields={
                "source_reference": "AccountRef",
                "position_type": "Type",
                "currency": "Ccy",
                "balance": "Outstanding",
                "counterparty_reference": "Customer",
                "product_code": "Product",
                "interest_rate": "Rate",
                "rate_type": "RateKind",
                "contractual_maturity": "Maturity",
            },
        ),
    },
    enum_mappings={
        "counterparty_type": {"RETAIL": "RETAIL_INDIVIDUAL", "CORP": "CORPORATE"},
        "rate_type": {"F": "FIXED", "V": "FLOATING", "FLOAT": "FLOATING"},
    },
    product_mappings={"LN.CORP.5Y": "CORPORATE_LOAN_UNRATED_100RW"},
)

RECON_MAPPING = MappingConfig(
    field_mappings={
        "gl_account": EntityMapping(
            source_table="GL",
            fields={
                "source_reference": "Code",
                "account_code": "Code",
                "name": "Label",
                "account_class": "Class",
                "balance": "Balance",
            },
        ),
        "position": EntityMapping(
            source_table="Loans",
            fields={
                "source_reference": "AccountRef",
                "position_type": "Type",
                "currency": "Ccy",
                "balance": "Outstanding",
                "gl_account_code": "GLAccount",
            },
        ),
    },
)


def seed_bank(client: TestClient) -> str:
    _ = client  # initialize the test app's engine before opening a direct session
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        grant_institution_authority(
            session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            user_id=USER_1,
            bundle=RoleBundle.ANALYST,
            module=ModuleScope.DATA,
            sensitivity=SensitivityScope.ALL,
        )
        session.commit()
    finally:
        session.close()
    return SAMPLE_BANK_ID


def activate_mapping(
    client: TestClient, bank_id: str, mapping: MappingConfig, *, organization_id: str = ORG_1
) -> str:
    response = client.post(
        f"/api/v1/banks/{bank_id}/mapping-configs",
        headers=headers(organization_id),
        json={
            "source_system": "EXCEL_CSV",
            "name": "Workbook mapping",
            "config": mapping.model_dump(mode="json"),
            "activate": True,
            "reason": "Onboarding mapping for tests.",
        },
    )
    assert response.status_code == 200, response.text
    return cast(str, response.json()["id"])


def stage_source(
    client: TestClient, bank_id: str, path: Path, *, organization_id: str = ORG_1
) -> IngestionUploadRead:
    with path.open("rb") as handle:
        response = client.post(
            f"/api/v1/banks/{bank_id}/ingestion-uploads",
            headers=headers(organization_id),
            files={"file": (path.name, handle, "application/octet-stream")},
        )
    assert response.status_code == 201, response.text
    return IngestionUploadRead.model_validate(response.json())


def start_batch(
    client: TestClient, bank_id: str, location: Path, *, as_of_date: str = AS_OF
) -> dict[str, Any]:
    staged = stage_source(client, bank_id, location)
    response = client.post(
        f"/api/v1/banks/{bank_id}/ingestion-batches",
        headers=headers(),
        json={
            "source_system": "EXCEL_CSV",
            "as_of_date": as_of_date,
            "location": staged.location,
            "reason": "Month-end ingestion.",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())
