"""Exercise field backfill against the deployed migration and RLS schema."""

from __future__ import annotations

import os
from datetime import date
from typing import cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.bi.models.content import BiDashboard, BiDashboardVersion
from app.core.key_management import registry
from app.core.key_management.backfill import backfill_bank
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.types import KeyReference, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.data_engine.service.field_search import find_counterparties
from app.db.base import utc_now
from app.identity.models.institution_profile import RelatedParty, RelatedPartyRole
from app.identity.public import Bank, User
from app.models.audit_event import AuditEvent
from app.models.organization import Organization
from app.reporting.models.packages import RegulatoryPackage
from tests.core.test_bank_field_encryption import IDENTIFIER, NAME, counterparty
from tests.db import test_postgres_migrations as migrations
from tests.support.helpers import ORG_1, USER_1

forward_migrated_postgres_schema = migrations.forward_migrated_postgres_schema
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


def test_migrated_field_capacity_scoped_backfill_and_unchanged_historical_audit(
    forward_migrated_postgres_schema: migrations.MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-field-migration-key", "us-east-1", "111122223333")
    provider.add_key(key)

    def resolve(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", resolve)
    with Session(forward_migrated_postgres_schema.app_engine, expire_on_commit=False) as db:
        db.info["organization_id"] = ORG_1
        db.add(Organization(id=ORG_1, name="Synthetic migration organization"))
        db.flush()
        for bank_id in ("BK-SAMP0001", "BK-SAMP0002", "BK-SAMP0003"):
            db.add(
                Bank(
                    id=bank_id,
                    organization_id=ORG_1,
                    name="Synthetic migration bank",
                    short_name="Synthetic",
                    currency="GHS",
                    jurisdiction_code="GH",
                    institution_type="universal_bank",
                    license_type="universal_bank",
                    storage_slug=bank_id.lower(),
                )
            )
        db.flush()
        party = RelatedParty(
            organization_id=ORG_1,
            bank_id="BK-SAMP0001",
            party_type="individual",
            full_name="Synthetic historical director",
            contact={"national_id": "SYNTHETIC-17"},
        )
        sibling = RelatedParty(
            organization_id=ORG_1,
            bank_id="BK-SAMP0002",
            party_type="individual",
            full_name="Synthetic sibling director",
            contact={"national_id": "SYNTHETIC-18"},
        )
        db.add_all([party, sibling])
        db.flush()
        db.add(
            RelatedPartyRole(
                organization_id=ORG_1,
                party_id=party.id,
                role="director",
                term_of_appointment="Synthetic appointment details",
                icag_registration="SYNTHETIC-19",
            )
        )
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type="related_party.created",
                details={"full_name": "Synthetic historical director"},
            )
        )
        db.commit()
        historical = cast(
            str, db.execute(text("SELECT details::text FROM audit_events")).scalar_one()
        )
        registry.register(
            db,
            bank_id="BK-SAMP0001",
            organization_id=ORG_1,
            storage_slug="bk-samp0001",
            key=key,
            providers=resolve,
        )
        db.commit()
        db.expunge_all()
        result = backfill_bank(db, organization_id=ORG_1, bank_id="BK-SAMP0001", batch_size=1)
        assert result.checked == result.encrypted == 2
        stored = cast(
            str,
            db.execute(
                text("SELECT full_name FROM related_parties WHERE bank_id='BK-SAMP0001'")
            ).scalar_one(),
        )
        assert stored.startswith("aqs-bank-field-v1:") and "historical director" not in stored
        assert (
            db.execute(
                text("SELECT full_name FROM related_parties WHERE bank_id='BK-SAMP0002'")
            ).scalar_one()
            == "Synthetic sibling director"
        )
        assert db.execute(text("SELECT details::text FROM audit_events")).scalar_one() == historical
        again = backfill_bank(db, organization_id=ORG_1, bank_id="BK-SAMP0001", batch_size=1)
        assert again.encrypted == 0 and again.checked == 2
        assert (
            db.execute(
                text("SELECT full_name FROM related_parties WHERE bank_id='BK-SAMP0001'")
            ).scalar_one()
            == stored
        )
        loaded = db.scalar(select(RelatedParty).where(RelatedParty.bank_id == "BK-SAMP0001"))
        assert loaded is not None and loaded.contact == {"national_id": "SYNTHETIC-17"}

        _assert_immutable_cutover(db, key, resolve)


def _assert_immutable_cutover(
    db: Session, key: KeyReference, resolve: registry.ProviderFactory
) -> None:
    db.add(
        User(
            id=USER_1,
            organization_id=ORG_1,
            email="synthetic@example.test",
            display_name="Synthetic",
            role="viewer",
        )
    )
    db.flush()
    # Execute JSON blind-index predicates on the deployed JSONB column, not
    # merely on SQLite's JSON implementation.
    party_id = counterparty(db).id
    db.commit()
    db.expunge_all()
    matches = find_counterparties(
        db,
        TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
        "BK-SAMP0001",
        field="external_identifier",
        value=IDENTIFIER,
        namespace="national_id",
    )
    assert [party.id for party in matches] == [party_id]
    assert matches[0].name == NAME
    dashboard = BiDashboard(
        organization_id=ORG_1,
        bank_id="BK-SAMP0002",
        owner_user_id=USER_1,
        title="Synthetic",
        description="",
        visibility="private",
        badge="personal",
        current_version=1,
    )
    db.add(dashboard)
    db.flush()
    db.add(
        BiDashboardVersion(
            organization_id=ORG_1,
            bank_id="BK-SAMP0002",
            dashboard_id=dashboard.id,
            version=1,
            title="Synthetic",
            description="",
            spec={"filters": [{"value": "Synthetic historical identity"}]},
            spec_digest="a" * 64,
            change_note="",
            created_by_user_id=USER_1,
            created_at=utc_now(),
        )
    )
    db.add(
        RegulatoryPackage(
            organization_id=ORG_1,
            bank_id="BK-SAMP0003",
            return_family="large_exposures",
            return_code="SYNTHETIC",
            reporting_date=date(2026, 6, 30),
            frequency="monthly",
            status="generated",
            version=1,
            snapshot={"counterparty_name": "Synthetic historical identity"},
            generated_by=USER_1,
        )
    )
    db.commit()
    for bank_id, table, column in (
        ("BK-SAMP0002", "bi_dashboard_versions", "spec"),
        ("BK-SAMP0003", "regulatory_packages", "snapshot"),
    ):
        before = cast(object, db.execute(text(f"SELECT {column}::text FROM {table}")).scalar_one())
        with pytest.raises(KeyUnavailableError, match="legacy plaintext"):
            registry.register(
                db,
                organization_id=ORG_1,
                bank_id=bank_id,
                storage_slug=bank_id.lower(),
                key=key,
                providers=resolve,
            )
        db.rollback()
        assert (
            db.scalar(select(BankEncryptionKey).where(BankEncryptionKey.bank_id == bank_id)) is None
        )
        # Catalogue rows from an older release must hit the same refusal in
        # maintenance, without attempting an UPDATE forbidden by the guard.
        db.add(
            BankEncryptionKey(
                organization_id=ORG_1,
                bank_id=bank_id,
                storage_slug=bank_id.lower(),
                provider=key.provider,
                key_id=key.key_id,
                region=key.region,
                owner_account=key.owner_account,
                status="active",
            )
        )
        db.commit()
        with pytest.raises(KeyUnavailableError, match="existing content guards remain unchanged"):
            backfill_bank(db, organization_id=ORG_1, bank_id=bank_id)
        assert db.execute(text(f"SELECT {column}::text FROM {table}")).scalar_one() == before
        with pytest.raises(DatabaseError):
            db.execute(text(f"UPDATE {table} SET {column} = '{{}}'"))
        db.rollback()
        assert db.execute(text(f"SELECT {column}::text FROM {table}")).scalar_one() == before
