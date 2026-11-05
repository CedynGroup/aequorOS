"""Tests for test_demo_key."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management.demo import initialize
from app.core.key_management.models import BankEncryptionKey
from app.db.base import Base
from app.models import Bank, Organization
from scripts.demo_bank_key import main
from tests.fixtures.reference_data import seed_global_reference_data
from tests.support.database import TestDatabaseConnections


def test_connect_provisions_only_synthetic_key_metadata_in_explicit_sqlite(tmp_path: Path) -> None:
    connections = TestDatabaseConnections(f"sqlite+pysqlite:///{tmp_path / 'demo.db'}")
    engine = connections.engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_global_reference_data(db)
        db.add(Organization(id="OR-DEMO0001", name="Synthetic demo"))
        db.flush()
        db.add(
            Bank(
                id="BK-DEMO0001",
                organization_id="OR-DEMO0001",
                name="Synthetic demo bank",
                short_name="Synthetic",
                jurisdiction_code="GH",
                currency="GHS",
                license_type="universal_bank",
                institution_type="universal_bank",
            )
        )
        db.commit()
    initialize(tmp_path / "demo-key.json", organization_id="OR-DEMO0001", bank_id="BK-DEMO0001")
    for _ in range(2):
        assert main(["connect", "--directory", str(tmp_path)]) == 0
    with Session(engine) as db:
        key = db.scalar(select(BankEncryptionKey))
        assert key is not None and key.bank_id == "BK-DEMO0001"
        assert key.key_id == "synthetic:OR-DEMO0001:BK-DEMO0001"
        assert key.storage_slug == "bk-demo0001"
        bank = db.get(Bank, "BK-DEMO0001")
        assert bank is not None and bank.storage_slug == key.storage_slug
    connections.close()
