"""Control isolated synthetic encryption stories in the disposable Playwright book."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import UUID

from fastapi import HTTPException
from openpyxl import Workbook
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.core.key_management.aws import kms_client
from app.core.key_management.schemas import BankKeyConnect, BankKeyRotate
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import FIELD_FORMAT
from app.core.tenancy import TenantContext
from app.data_engine.service.field_search import find_counterparties
from app.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.identity.service import authorization, membership
from app.models import Bank, Organization, User
from app.operator.services import bank_encryption
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.services import ingestion
from tests.support.inmemory_storage import InMemoryStorageClient

STORIES = ("revocation", "outage", "rotation")
IDENTIFIER = "SYNTHETIC-ENCRYPTION-CUSTOMER-17"
BORROWER = "Synthetic encryption borrower Ñ"


class Identity(BaseModel):
    id: UUID
    organizationId: str


class _Admin(Protocol):
    def create_key(self, *, Description: str) -> object: ...
    def disable_key(self, *, KeyId: str) -> object: ...
    def enable_key(self, *, KeyId: str) -> object: ...
    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> object: ...


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Arn: str


class _Created(BaseModel):
    model_config = ConfigDict(extra="ignore")
    KeyMetadata: _Metadata


class State(BaseModel):
    keys: list[str] = []


class Options(BaseModel):
    action: Literal["create", "connect", "disable", "enable", "rotate", "assert-storage", "remove"]
    story: Literal["revocation", "outage", "rotation"]
    directory: Path


def _identity(story: str) -> Identity:
    source = Path(__file__).parents[1] / "dashboard/e2e/support/identities.json"
    data = cast(dict[str, dict[str, object]], json.loads(source.read_text()))
    return Identity.model_validate(data["journey"]["encryption_" + story])


def _copy_numeric_fixture(database: Path, org: str, bank: str) -> None:
    # Copy the platform's already-computed synthetic metrics. No engine is
    # bypassed or re-derived here; assertions compare these values across locks.
    with sqlite3.connect(database) as db:
        for table in (
            "bank_reporting_periods",
            "current_financial_facts",
            "live_metrics",
            "param_lcr_runoff_rate",
            "param_nsfr_weight",
            "param_capital_threshold",
        ):
            columns = [
                str(row[1])
                for row in cast(
                    list[tuple[object, ...]], db.execute(f"PRAGMA table_info({table})").fetchall()
                )
            ]
            selected = {
                "id": "lower(hex(randomblob(16)))",
                "organization_id": ":org",
                "bank_id": ":bank",
                "source_fact_period_id": "NULL",
            }
            names = ", ".join('"' + name + '"' for name in columns)
            values = ", ".join(selected.get(name, '"' + name + '"') for name in columns)
            scope = "organization_id='OR-DEM00001'"
            if "bank_id" in columns:
                scope += " AND bank_id='BK-SAMP0001'"
            db.execute(
                f"INSERT OR IGNORE INTO {table} ({names}) "
                f"SELECT {values} FROM {table} WHERE {scope}",
                {"org": org, "bank": bank},
            )


def _create(db: Session, identity: Identity, bank: str) -> None:
    org = identity.organizationId
    db.add(Organization(id=org, name="Synthetic encryption journey"))
    db.flush()
    user = User(
        id=identity.id,
        organization_id=org,
        email=f"encryption.{bank.lower()}@aequoros.example",
        display_name="Synthetic encryption analyst",
        role="analyst",
    )
    db.add(user)
    db.add(
        Bank(
            id=bank,
            organization_id=org,
            name="Synthetic encryption bank",
            short_name="Synthetic",
            jurisdiction_code="GH",
            currency="GHS",
            institution_type="universal_bank",
            license_type="universal",
            storage_slug=bank.lower(),
        )
    )
    db.flush()
    membership.ensure_baseline_membership(
        db, user=user, granted_by_id="e2e-encryption", commit=False
    )
    authorization.create_role_binding(
        db,
        organization_id=org,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.ALL,
            SensitivityScope.ALL,
            data_scope=DataScope.ALL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "e2e-encryption"),
        reason="Exercise synthetic encryption journey",
        commit=False,
    )
    assert user.authorization_version == 3
    db.commit()


def _ingest_identity(db: Session, identity: Identity, bank: str, directory: Path) -> None:
    ctx = TenantContext(organization_id=identity.organizationId, actor_user_id=identity.id)
    config = MappingConfig(
        field_mappings={
            "counterparty": EntityMapping(
                source_table="Customers",
                fields={
                    "source_reference": "CustomerId",
                    "name": "Name",
                    "counterparty_type": "Type",
                },
            )
        }
    )
    ingestion.create_mapping_config(
        db,
        ctx,
        bank,
        MappingConfigCreate(
            source_system="EXCEL_CSV",
            name="Synthetic encryption identities",
            config=config,
            activate=True,
            reason="Synthetic encryption journey",
        ),
    )
    workbook = Workbook()
    customers = workbook.active
    assert customers is not None
    customers.title = "Customers"
    customers.append(["CustomerId", "Name", "Type"])
    customers.append([IDENTIFIER, BORROWER, "RETAIL_INDIVIDUAL"])
    location = directory / "synthetic-identities.xlsx"
    workbook.save(location)
    result = ingestion.start_ingestion(
        db,
        ctx,
        bank,
        IngestionBatchCreate(
            source_system="EXCEL_CSV",
            as_of_date=date(2026, 6, 30),
            location=str(location),
            reason="Synthetic encrypted identity ingestion",
        ),
        InMemoryStorageClient(),
    )
    assert result.batch.status in {"accepted", "accepted_with_warnings"}, result.batch.status
    db.commit()


def _assert_storage(
    db: Session, database: Path, identity: Identity, bank: str
) -> dict[str, object]:
    with sqlite3.connect(database) as raw:
        parties = cast(
            list[tuple[str, str]],
            raw.execute(
                "SELECT full_name, full_name_blind_index FROM related_parties "
                "WHERE bank_id=? ORDER BY id",
                (bank,),
            ).fetchall(),
        )
        assert parties, "The browser must save a protected related party first"
        for name, token in parties:
            assert "Synthetic" not in name and len(token) == 64
            assert name.startswith(FIELD_FORMAT + ":"), (
                "Expected a persisted field-encryption envelope"
            )
        counterparties = cast(
            list[tuple[str, str]],
            raw.execute(
                "SELECT name, source_reference FROM canonical_counterparties WHERE bank_id=?",
                (bank,),
            ).fetchall(),
        )
        batch = cast(
            tuple[str],
            raw.execute(
                "SELECT id FROM ingestion_batches WHERE bank_id=?",
                (bank,),
            ).fetchone(),
        )
        assert len(counterparties) == 1
        assert BORROWER not in counterparties[0][0] and IDENTIFIER not in counterparties[0][1]
    ctx = TenantContext(organization_id=identity.organizationId, actor_user_id=identity.id)
    matches = find_counterparties(db, ctx, bank, field="source_reference", value=IDENTIFIER)
    assert len(matches) == 1 and matches[0].name == BORROWER
    assert (
        find_counterparties(db, ctx, bank, field="source_reference", value=IDENTIFIER.lower()) == []
    )
    return {
        "encrypted_parties": len(parties),
        "exact_identifier_matches": len(matches),
        "party_ciphertexts": [name for name, _ in parties],
        "batch_id": str(UUID(batch[0])),
    }


def _remove(database: Path, identity: Identity) -> None:
    with sqlite3.connect(database) as db:
        tables = [
            str(row[0])
            for row in cast(
                list[tuple[object, ...]],
                db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
            )
        ]
        for table in tables:
            columns = {
                str(row[1])
                for row in cast(
                    list[tuple[object, ...]], db.execute(f'PRAGMA table_info("{table}")').fetchall()
                )
            }
            if "organization_id" in columns:
                db.execute(
                    f'DELETE FROM "{table}" WHERE organization_id=?', (identity.organizationId,)
                )
        db.execute("DELETE FROM organizations WHERE id=?", (identity.organizationId,))


def _database(directory: Path) -> Path:
    if get_settings().app.app_env != "test" or not get_key_settings().kms_endpoint_url:
        raise ValueError(
            "Encryption journeys require APP_ENV=test and the explicit local KMS endpoint"
        )
    if get_key_settings().demo_bank_key_file is not None:
        raise ValueError("Encryption journeys require the real AWS provider")
    directory = directory.resolve()
    database = directory / "e2e.db"
    if database.is_symlink() or not database.is_file():
        raise ValueError("Bootstrap the disposable e2e.db before controlling encryption")
    return database


def run(options: Options) -> dict[str, object]:
    database = _database(options.directory)
    directory = database.parent
    identity = _identity(options.story)
    bank = "BK-KEY0000" + str(STORIES.index(options.story) + 1)
    state_file = directory / ("encryption-" + options.story + ".json")
    state = State.model_validate_json(state_file.read_text()) if state_file.exists() else State()
    client = kms_client("us-east-1")
    admin = cast(_Admin, cast(object, client))
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        if options.action == "remove":
            for key in state.keys:
                _ = admin.enable_key(KeyId=key)
            _remove(database, identity)
            for key in state.keys:
                _ = admin.schedule_key_deletion(KeyId=key, PendingWindowInDays=7)
            state_file.unlink(missing_ok=True)
            return {"removed": True}
        with Session(engine) as db:
            if options.action == "create":
                _create(db, identity, bank)
                for _ in range(2):
                    key = _Created.model_validate(
                        admin.create_key(Description="aequorOS synthetic journey")
                    )
                    state.keys.append(key.KeyMetadata.Arn)
                    state_file.write_text(state.model_dump_json())
                return {"bank_id": bank, "organization_id": identity.organizationId}
            payload = {
                "key_id": state.keys[0],
                "region": "us-east-1",
                "owner_account": "111122223333",
            }
            if options.action == "connect":
                _ = admin.disable_key(KeyId=state.keys[0])
                try:
                    bank_encryption.connect_key(
                        db, identity.organizationId, bank, BankKeyConnect.model_validate(payload)
                    )
                except HTTPException as refused:
                    assert refused.status_code == 503
                else:
                    raise AssertionError("A disabled bank key must not connect")
                _ = admin.enable_key(KeyId=state.keys[0])
                connected = bank_encryption.connect_key(
                    db, identity.organizationId, bank, BankKeyConnect.model_validate(payload)
                )
                db.commit()
                _ingest_identity(db, identity, bank, directory)
                _copy_numeric_fixture(database, identity.organizationId, bank)
                return {"status": connected.status.value, "disabled_connect_refused": True}
            if options.action in {"disable", "enable"}:
                operation: Callable[..., object] = (
                    admin.disable_key if options.action == "disable" else admin.enable_key
                )
                _ = operation(KeyId=state.keys[0])
                return {"status": options.action}
            if options.action == "rotate":
                rotated = bank_encryption.rotate_key(
                    db,
                    identity.organizationId,
                    bank,
                    BankKeyRotate.model_validate(
                        {
                            **payload,
                            "key_id": state.keys[1],
                            "reason": "Synthetic key rotation journey",
                        },
                    ),
                )
                db.commit()
                return {"status": rotated.key.status.value, "key_id": rotated.key.key_id}
            return _assert_storage(db, database, identity, bank)
    finally:
        engine.dispose()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="aequorOS disposable encryption journey control")
    parser.add_argument("action")
    parser.add_argument("--story", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    print(json.dumps(run(Options.model_validate(vars(parser.parse_args())))))


if __name__ == "__main__":
    main()
