"""Bank field encryption on the deployed PostgreSQL schema."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from alembic import command
from app.bi.models.notifications import BiAlert, BiAlertEvent, BiSubscription
from app.bi.service.identity_policy import IDENTITY_REASON
from app.core.authorization import ModuleScope, SensitivityScope
from app.core.key_management import fields, registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import ObjectKeyEnvelope
from app.core.key_management.types import KeyReference
from app.db.base import utc_now
from app.identity.models.institution_profile import RelatedParty, RelatedPartyRole
from app.identity.public import Bank, User
from app.models.canonical import CanonicalCounterparty
from app.models.organization import Organization
from app.reporting.models.packages import RegulatoryPackage
from tests.core.test_bank_field_encryption import (
    IDENTIFIER,
    NAME,
    assert_unicode_name_round_trip,
    counterparty,
    raw_fields,
    saved_version,
)
from tests.core.test_bank_field_readers import GroupAxis, assert_long_concentration_tokens
from tests.db import test_postgres_migrations as migrations
from tests.support.authority import grant_organization_analyst
from tests.support.helpers import ORG_1, USER_1

forward_migrated_postgres_schema = migrations.forward_migrated_postgres_schema
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


@pytest.fixture
def sensitive_db(
    forward_migrated_postgres_schema: migrations.MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Session, LocalKeyProvider, KeyReference]]:
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-field-regression", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
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


def test_existing_data_upgrades_indexes_envelopes_and_connected_empty_banks(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference],
) -> None:
    db, provider, reference = sensitive_db
    cp = counterparty(db, include_package=False)
    party = RelatedParty(
        organization_id=ORG_1,
        bank_id="BK-SAMP0001",
        party_type="individual",
        full_name=NAME,
    )
    db.add(party)
    db.add(
        Bank(
            id="BK-SAMP0002",
            organization_id=ORG_1,
            name="Synthetic empty bank",
            short_name="Empty",
            currency="GHS",
            jurisdiction_code="GH",
            institution_type="universal_bank",
            license_type="universal_bank",
            storage_slug="bk-samp0002",
        )
    )
    db.commit()
    cp_id, party_id = cp.id, party.id
    db.close()
    config = migrations.alembic_config_for_app()
    command.downgrade(config, "202610140100")
    connection = db.connection()
    metadata = MetaData()
    keys = Table("bank_encryption_keys", metadata, autoload_with=connection)
    envelopes = Table("object_key_envelopes", metadata, autoload_with=connection)
    for bank in ("BK-SAMP0001", "BK-SAMP0002"):
        _ = db.execute(
            keys.insert().values(
                id=uuid4(),
                bank_id=bank,
                organization_id=ORG_1,
                storage_slug=bank.lower(),
                provider=reference.provider,
                key_id=reference.key_id,
                region=reference.region,
                owner_account=reference.owner_account,
                status="active",
                checked_at=utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    context = {"purpose": "bank-object", "object_path": "synthetic/old.csv"}
    digest = registry.context_digest(context)
    secret = provider.generate_data_key(reference, registry.wrapping_context("BK-SAMP0001", digest))
    envelope_id = uuid4()
    _ = db.execute(
        envelopes.insert().values(
            id=envelope_id,
            bank_id="BK-SAMP0001",
            organization_id=ORG_1,
            context_digest=digest,
            wrapped_key=secret.wrapped,
            created_at=utc_now(),
        )
    )
    db.commit()
    command.upgrade(config, "head")
    for bank in ("BK-SAMP0001", "BK-SAMP0002"):
        key = registry.scoped_key(db, organization_id=ORG_1, bank_id=bank)
        assert key.current_version_id is not None
        assert key.index_version_id == key.current_version_id
        assert fields.index_key(db, key)
    key = registry.scoped_key(db, organization_id=ORG_1, bank_id="BK-SAMP0001")
    envelope = db.get(ObjectKeyEnvelope, envelope_id)
    assert envelope is not None and registry.reference(envelope) == reference
    assert (
        registry.open_envelope(db, key, envelope_id, context, lambda _: provider).plaintext
        == secret.plaintext
    )
    stored = db.get(CanonicalCounterparty, cp_id)
    related = db.get(RelatedParty, party_id)
    assert stored is not None and related is not None
    expected = fields.index_values(
        "canonical_counterparties",
        {
            "name": stored.name,
            "source_reference": stored.source_reference,
            "group_reference": stored.group_reference,
            "external_identifiers": stored.external_identifiers,
        },
        ORG_1,
        "BK-SAMP0001",
        fields.index_key(db, key),
    )
    for column, token in expected.items():
        assert (
            db.scalar(
                select(CanonicalCounterparty.__table__.c[column]).where(
                    CanonicalCounterparty.id == cp_id
                )
            )
            == token
        )
    assert related.full_name_blind_index == fields.blind_index(
        db,
        organization_id=ORG_1,
        bank_id="BK-SAMP0001",
        domain="related_party.full_name",
        value=NAME,
        normalization="name",
    )


@pytest.mark.parametrize("connected", [False, True])
def test_unicode_name_indexes_fit_postgres_columns(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference], connected: bool
) -> None:
    db, provider, key = sensitive_db

    grant_organization_analyst(
        db,
        ModuleScope.INSTITUTION,
        SensitivityScope.ALL,
        grantor="unicode-register-fixture",
        reason="Authorize synthetic Unicode register construction",
    )

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    if connected:
        registry.register(
            db,
            bank_id="BK-SAMP0001",
            organization_id=ORG_1,
            storage_slug="bk-samp0001",
            key=key,
            providers=providers,
        )
        db.commit()
    assert_unicode_name_round_trip(db, connected)


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


def test_alert_filters_encrypt_and_preserve_list_shape(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference],
) -> None:

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
    db.expunge_all()
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


def test_new_sensitive_fields_are_ciphertext_in_postgres(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference],
) -> None:
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
    borrower = counterparty(db)
    borrower_id = borrower.id
    party = RelatedParty(
        organization_id=ORG_1,
        bank_id="BK-SAMP0001",
        party_type="individual",
        full_name=NAME,
        contact={"national_id": IDENTIFIER},
    )
    db.add(party)
    db.flush()
    db.add(
        RelatedPartyRole(
            organization_id=ORG_1,
            party_id=party.id,
            role="director",
            other_responsibilities=NAME,
            term_of_appointment=NAME,
            icag_registration=IDENTIFIER,
            sitting_allowance=Decimal("12.34"),
        )
    )
    db.add(saved_version(db, {"filters": [{"member": "counterparty.name", "values": [NAME]}]}))
    db.add(
        BiSubscription(
            organization_id=ORG_1,
            bank_id="BK-SAMP0001",
            owner_user_id=USER_1,
            name="Synthetic",
            query={"filters": [{"member": "counterparty.name", "values": [NAME]}]},
            artifact_format="csv",
            cadence="daily",
            hour=7,
            minute=30,
            recipient_user_ids=[],
        )
    )
    db.commit()
    for table, records in raw_fields(db).items():
        assert records, table
        for record in records:
            for value in record:
                encoded = json.dumps(value, ensure_ascii=False, default=str)
                assert "aqs-bank-field-v1" in encoded, table
                assert NAME not in encoded and IDENTIFIER not in encoded
    for table, column in (("bi_dashboard_versions", "spec"), ("bi_subscriptions", "query")):
        stored = cast(object, db.scalar(text(f"SELECT {column}::text FROM {table}")))
        assert NAME not in str(stored) and "aqs-bank-field-v1" in str(stored)
    assert db.scalar(text("SELECT sitting_allowance FROM related_party_roles")) == Decimal("12.34")
    db.expunge_all()
    restored = db.get(CanonicalCounterparty, borrower_id)
    assert restored is not None and restored.name == NAME
    assert restored.external_identifiers == {"national_id": IDENTIFIER}


@pytest.mark.parametrize("axis", ["name", "reference", "group_reference", "group", "parent"])
def test_optional_concentration_tokens_fit_postgres_columns(
    sensitive_db: tuple[Session, LocalKeyProvider, KeyReference],
    axis: GroupAxis,
) -> None:
    db, _, _ = sensitive_db
    assert_long_concentration_tokens(db, axis)
