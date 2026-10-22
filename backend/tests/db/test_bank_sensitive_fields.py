"""Exercise field backfill against the deployed migration and RLS schema."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from typing import cast

import pytest
from sqlalchemy import inspect, select, text
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
        original_updated_at = party.updated_at
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
        assert loaded.updated_at == original_updated_at

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
        scope = {"bank": bank_id, "org": ORG_1}
        read = text(
            f"SELECT {column}::text FROM {table} WHERE bank_id = :bank AND organization_id = :org"
        )
        before = cast(object, db.execute(read, scope).scalar_one())
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
        assert db.execute(read, scope).scalar_one() == before
        with pytest.raises(DatabaseError):
            db.execute(
                text(
                    f"UPDATE {table} SET {column} = '{{}}' "
                    "WHERE bank_id = :bank AND organization_id = :org"
                ),
                scope,
            )
        db.rollback()
        assert db.execute(read, scope).scalar_one() == before


@pytest.fixture
def sensitive_db(
    forward_migrated_postgres_schema: migrations.MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Session, LocalKeyProvider, KeyReference]]:
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-field-regression", "us-east-1", "111122223333")
    provider.add_key(key)
    monkeypatch.setattr(registry, "provider_for", lambda _name: provider)
    with Session(forward_migrated_postgres_schema.app_engine, expire_on_commit=False) as db:
        db.info["organization_id"] = ORG_1
        db.add(Organization(id=ORG_1, name="Synthetic organization"))
        db.flush()
        db.add(
            User(
                id=USER_1,
                organization_id=ORG_1,
                email="synthetic@example.test",
                display_name="Synthetic",
                role="viewer",
            )
        )
        db.add(
            Bank(
                id="BK-SAMP0001",
                organization_id=ORG_1,
                name="Synthetic",
                short_name="Synthetic",
                currency="GHS",
                jurisdiction_code="GH",
                institution_type="universal_bank",
                license_type="universal_bank",
                storage_slug="bk-samp0001",
            )
        )
        db.commit()
        yield db, provider, key


@pytest.mark.parametrize("connected", [False, True])
def test_package_validation_preserves_immutable_snapshot(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference], connected: bool
) -> None:
    db, provider, key = sensitive_db
    if connected:
        registry.register(
            db,
            bank_id="BK-SAMP0001",
            organization_id=ORG_1,
            storage_slug="bk-samp0001",
            key=key,
            providers=lambda _name: provider,
        )
        db.commit()
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id="BK-SAMP0001",
        return_family="large_exposures",
        return_code="SYNTHETIC",
        reporting_date=date(2026, 6, 30),
        frequency="monthly",
        status="generated",
        version=1,
        snapshot={"counterparty_name": NAME},
        generated_by=USER_1,
    )
    db.add(package)
    db.commit()
    package_id = package.id
    before = cast(
        object,
        db.execute(
            text("SELECT snapshot::text FROM regulatory_packages WHERE id=:id"), {"id": package_id}
        ).scalar_one(),
    )
    db.expunge_all()
    loaded = db.get(RegulatoryPackage, package_id)
    assert loaded is not None
    db.expire(loaded, ["snapshot", "notes"])
    loaded.validation_report = {"valid": True}
    loaded.status = "validated"
    db.commit()
    assert (
        cast(
            object,
            db.execute(
                text("SELECT snapshot::text FROM regulatory_packages WHERE id=:id"),
                {"id": package_id},
            ).scalar_one(),
        )
        == before
    )
    db.expunge_all()
    validated = db.get(RegulatoryPackage, package_id)
    assert validated is not None and validated.validation_report == {"valid": True}
    assert validated.snapshot == {"counterparty_name": NAME}
    validated.snapshot = {"counterparty_name": "Synthetic forbidden rewrite"}
    with pytest.raises(DatabaseError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize("column", ["validation_report", "etl_report", "error_message"])
def test_downgrade_refuses_only_encrypted_failed_ingestion(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference], column: str
) -> None:
    from alembic import command
    from app.models.ingestion import IngestionBatch

    db, provider, key = sensitive_db
    registry.register(
        db,
        bank_id="BK-SAMP0001",
        organization_id=ORG_1,
        storage_slug="bk-samp0001",
        key=key,
        providers=lambda _name: provider,
    )
    db.commit()
    values: dict[str, object] = {"validation_report": {}, "etl_report": None, "error_message": None}
    values[column] = NAME if column == "error_message" else {"name": NAME}
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id="BK-SAMP0001",
        source_system="EXCEL_CSV",
        adapter_version="synthetic",
        extraction_mode="full",
        status="failed",
        as_of_date=date(2026, 6, 30),
        **values,
    )
    db.add(batch)
    db.flush()
    if column != "validation_report":
        db.execute(
            text("UPDATE ingestion_batches SET validation_report='{}' WHERE id=:id"),
            {"id": batch.id},
        )
    db.commit()
    with pytest.raises(RuntimeError, match="Bank-encrypted fields prohibit schema downgrade"):
        command.downgrade(migrations.alembic_config_for_app(), "202610140100")
    assert (
        cast(object, db.scalar(text("SELECT version_num FROM alembic_version"))) == "202610220101"
    )
    assert "source_reference_blind_index" in {
        column["name"] for column in inspect(db.get_bind()).get_columns("canonical_counterparties")
    }


def test_migrated_alert_filters_encrypt_backfill_and_preserve_list_shape(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference],
) -> None:
    from decimal import Decimal

    from app.bi.models.notifications import BiAlert, BiAlertEvent
    from app.bi.service.identity_policy import IDENTITY_REASON

    db, provider, key = sensitive_db
    filters: list[dict[str, object]] = [
        {"member": "counterparty.name", "op": "eq", "values": [NAME]}
    ]
    alert = BiAlert(
        organization_id=ORG_1,
        bank_id="BK-SAMP0001",
        owner_user_id=USER_1,
        name="Synthetic alert",
        measure_id="loans.balance_rc",
        filters=filters,
        direction="above",
        threshold_basis="stated",
        threshold=Decimal(10),
        notify_user_ids=[],
    )
    db.add(alert)
    db.commit()
    alert_id = alert.id
    registry.register(
        db,
        bank_id="BK-SAMP0001",
        organization_id=ORG_1,
        storage_slug="bk-samp0001",
        key=key,
        providers=lambda _name: provider,
    )
    db.commit()
    db.expunge_all()
    assert backfill_bank(db, organization_id=ORG_1, bank_id="BK-SAMP0001").encrypted == 1
    raw = cast(object, db.scalar(text("SELECT filters::text FROM bi_alerts")))
    assert isinstance(raw, str) and NAME not in raw and "aqs-bank-field-v1" in raw
    loaded = db.get(BiAlert, alert_id)
    assert loaded is not None and loaded.filters == filters
    loaded.filters = []
    db.add(
        BiAlertEvent(
            organization_id=ORG_1,
            bank_id="BK-SAMP0001",
            alert_id=alert_id,
            as_of_date=date(2026, 6, 30),
            build_fingerprint="a" * 64,
            state="not_evaluated",
            reason=IDENTITY_REASON,
            threshold_basis="stated",
            member_ids=[],
            notified_user_ids=[],
            withheld_user_ids=[],
            builder_version=1,
        )
    )
    db.commit()
    db.expunge_all()
    reloaded = db.get(BiAlert, alert_id)
    assert reloaded is not None and reloaded.filters == []
    assert backfill_bank(db, organization_id=ORG_1, bank_id="BK-SAMP0001").encrypted == 0
