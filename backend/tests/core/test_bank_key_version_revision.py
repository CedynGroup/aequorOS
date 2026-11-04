from __future__ import annotations

from typing import Protocol, cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.db.base import utc_now
from tests.core.test_bank_field_encryption import BANK_A, BANK_B, KEY_A
from tests.db.test_postgres_migrations import alembic_config_for_app
from tests.support.database import TestDatabaseConnections
from tests.support.helpers import ORG_1


class VersionMigration(Protocol):
    def backfill_references(self, connection: Connection) -> None: ...
    def backfill_versions(self, connection: Connection) -> None: ...


def revision_tables() -> tuple[sa.MetaData, sa.Table, sa.Table, sa.Table]:
    metadata = sa.MetaData()
    keys = sa.Table(
        "bank_encryption_keys",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bank_id", sa.String()),
        sa.Column("organization_id", sa.String()),
        sa.Column("provider", sa.String()),
        sa.Column("key_id", sa.String()),
        sa.Column("region", sa.String()),
        sa.Column("owner_account", sa.String()),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("index_version_id", sa.Uuid()),
    )
    envelopes = sa.Table(
        "object_key_envelopes",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bank_id", sa.String()),
        sa.Column("organization_id", sa.String()),
        sa.Column("provider", sa.String()),
        sa.Column("key_id", sa.String()),
        sa.Column("region", sa.String()),
        sa.Column("owner_account", sa.String()),
        sa.Column("context_digest", sa.String()),
        sa.Column("wrapped_key", sa.LargeBinary()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    parties = sa.Table(
        "canonical_counterparties",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bank_id", sa.String()),
        sa.Column("organization_id", sa.String()),
        sa.Column("name", sa.Text()),
        sa.Column("source_reference", sa.Text()),
        sa.Column("group_reference", sa.Text()),
        sa.Column("attributes", sa.JSON()),
        sa.Column("external_identifiers", sa.JSON()),
        sa.Column("name_blind_index", sa.String()),
        sa.Column("source_reference_blind_index", sa.String()),
        sa.Column("concentration_group_blind_index", sa.String()),
        sa.Column("external_identifiers_blind_indexes", sa.JSON()),
    )
    _ = sa.Table(
        "related_parties",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bank_id", sa.String()),
        sa.Column("organization_id", sa.String()),
        sa.Column("full_name", sa.Text()),
        sa.Column("full_name_blind_index", sa.String()),
    )
    return metadata, keys, envelopes, parties


@pytest.mark.parametrize("legacy_index", [False, True])
def test_revision_local_versions_reindex_populated_and_empty_banks_without_health_columns(
    monkeypatch: pytest.MonkeyPatch, legacy_index: bool
) -> None:
    provider = LocalKeyProvider()
    provider.add_key(KEY_A)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610220102")
    assert revision is not None
    migration = cast(VersionMigration, cast(object, revision.module))
    metadata, keys, envelopes, parties = revision_tables()
    connections = TestDatabaseConnections("sqlite://")
    engine = connections.engine()
    metadata.create_all(engine)
    with engine.begin() as connection:
        for bank in (BANK_A, BANK_B):
            _ = connection.execute(
                keys.insert().values(
                    id=uuid4(),
                    bank_id=bank,
                    organization_id=ORG_1,
                    provider=KEY_A.provider,
                    key_id=KEY_A.key_id,
                    region=KEY_A.region,
                    owner_account=KEY_A.owner_account,
                )
            )
        context = {
            "purpose": "bank-sensitive-index-v1" if legacy_index else "bank-object",
            "organization_id": ORG_1,
            "bank_id": BANK_A,
        }
        digest = registry.context_digest(context)
        data = provider.generate_data_key(KEY_A, registry.wrapping_context(BANK_A, digest))
        legacy = uuid4()
        _ = connection.execute(
            envelopes.insert().values(
                id=legacy,
                bank_id=BANK_A,
                organization_id=ORG_1,
                context_digest=digest,
                wrapped_key=data.wrapped,
                created_at=utc_now(),
            )
        )
        for name in ("A", "B"):
            _ = connection.execute(
                parties.insert().values(
                    id=uuid4(),
                    bank_id=BANK_A,
                    organization_id=ORG_1,
                    name=name,
                    source_reference=name,
                    group_reference=None,
                    attributes={"group": "G"},
                    external_identifiers={"national_id": name},
                    source_reference_blind_index=name,
                )
            )
        migration.backfill_references(connection)
        migration.backfill_versions(connection)
        rows = connection.execute(sa.select(keys)).mappings().all()
        assert len(rows) == 2 and all(row["current_version_id"] for row in rows)
        populated = next(row for row in rows if row["bank_id"] == BANK_A)
        empty = next(row for row in rows if row["bank_id"] == BANK_B)
        assert empty["current_version_id"] == empty["index_version_id"]
        assert populated["index_version_id"] == (
            legacy if legacy_index else populated["current_version_id"]
        )
        stored = connection.execute(sa.select(parties)).mappings().all()
        assert len({row["concentration_group_blind_index"] for row in stored}) == 1
        assert all(row["source_reference_blind_index"] != row["source_reference"] for row in stored)
        assert [row["name"] for row in stored] == ["A", "B"]
        historical = (
            connection.execute(sa.select(envelopes).where(envelopes.c.id == legacy))
            .mappings()
            .one()
        )
        assert historical["wrapped_key"] == data.wrapped and historical["key_id"] == KEY_A.key_id
        migration.backfill_versions(connection)
        assert connection.execute(sa.select(parties)).mappings().all() == stored
        assert connection.scalar(sa.select(sa.func.count()).select_from(envelopes)) == 3
    connections.close()
