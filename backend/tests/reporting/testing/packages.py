"""Transient package inputs for channel behavior after authorized acquisition.

These objects never establish filing authority or prove persistence. Workflow,
version selection, seals and bank isolation remain real database contracts.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.reporting.models.packages import RegulatoryPackage
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, SAMPLE_BANK_ID


def transient_package(status: str = "approved") -> RegulatoryPackage:
    return RegulatoryPackage(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        reporting_date=date(2026, 3, 31),
        frequency="monthly",
        status=status,
        version=1,
        snapshot={"institution": {"short_name": "SBL", "name": "Sample Bank Ltd"}},
    )
