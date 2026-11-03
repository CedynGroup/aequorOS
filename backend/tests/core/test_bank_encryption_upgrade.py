from __future__ import annotations

from typing import Protocol, cast

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import JSON, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.key_management.fields import blind_index
from app.core.key_management.local import LocalKeyProvider
from app.data_engine.models.canonical import CanonicalCounterparty
from app.data_engine.service import canonical_withdrawal
from app.data_engine.service.canonical_withdrawal import WithdrawalError
from app.identity.models.institution_profile import RelatedParty
from app.identity.public import Bank
from tests.core.test_bank_field_encryption import (
    BANK_A,
    CTX,
    IDENTIFIER,
    MAKER_CTX,
    counterparty,
    provider,
)
from tests.db.test_postgres_migrations import alembic_config_for_app
from tests.support.helpers import ORG_1

__all__ = ["provider"]


class IndexMigration(Protocol):
    def backfill_indexes(self, connection: Connection) -> None: ...


def test_upgrade_indexes_all_existing_generations_before_using_them(
    db_session: Session, provider: LocalKeyProvider
) -> None:
    cp = counterparty(db_session, include_package=False, name=" ＡDA  Traders ")
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    withdrawal = canonical_withdrawal.request_withdrawal(
        db_session,
        MAKER_CTX,
        bank,
        entity="counterparty",
        source_system=cp.source_system,
        as_of_date=cp.as_of_date,
        reason="Synthetic withdrawal before upgrade",
        requested_by="Maker",
    )
    canonical_withdrawal.approve_withdrawal(
        db_session, CTX, bank, withdrawal.id, approved_by="Checker"
    )
    party = RelatedParty(
        organization_id=ORG_1,
        bank_id=BANK_A,
        party_type="individual",
        full_name=" ＡDA  Director ",
    )
    db_session.add(party)
    db_session.commit()
    cp_id, party_id, withdrawal_id = cp.id, party.id, withdrawal.id
    db_session.execute(
        text(
            "UPDATE canonical_counterparties SET source_reference_blind_index=NULL, "
            "name_blind_index=NULL, concentration_group_blind_index=NULL, "
            "external_identifiers_blind_indexes='{}'"
        )
    )
    db_session.execute(text("UPDATE related_parties SET full_name_blind_index=NULL"))
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610220101")
    assert revision is not None
    migration = cast(IndexMigration, cast(object, revision.module))
    migration.backfill_indexes(db_session.connection())
    db_session.commit()
    db_session.expunge_all()
    stored = db_session.get(CanonicalCounterparty, cp_id)
    related = db_session.get(RelatedParty, party_id)
    assert stored is not None and related is not None
    assert stored.source_reference_blind_index == IDENTIFIER
    assert stored.concentration_group_blind_index == blind_index(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_A,
        domain="counterparty.group",
        value="group:SYNTHETIC-GROUP-17",
    )
    assert stored.external_identifiers_blind_indexes == {"national_id": IDENTIFIER}
    assert related.full_name_blind_index == blind_index(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_A,
        domain="related_party.full_name",
        value="ada director",
        normalization="name",
    )
    stored.withdrawn_at = None
    db_session.flush()
    current = (
        CanonicalCounterparty.organization_id == ORG_1,
        CanonicalCounterparty.bank_id == BANK_A,
        CanonicalCounterparty.withdrawn_at.is_(None),
        CanonicalCounterparty.superseded_by.is_(None),
    )
    assert list(
        db_session.scalars(
            select(CanonicalCounterparty.id).where(
                *current,
                CanonicalCounterparty.name_blind_index
                == blind_index(
                    db_session,
                    organization_id=ORG_1,
                    bank_id=BANK_A,
                    domain="counterparty.name",
                    value="ada traders",
                    normalization="name",
                ),
            )
        )
    ) == [cp_id]
    assert list(
        db_session.scalars(
            select(CanonicalCounterparty.id).where(
                *current,
                cast(
                    JSON.Comparator[object],
                    CanonicalCounterparty.external_identifiers_blind_indexes[
                        "national_id"
                    ].comparator,
                ).as_string()
                == blind_index(
                    db_session,
                    organization_id=ORG_1,
                    bank_id=BANK_A,
                    domain="counterparty.identifier:national_id",
                    value=IDENTIFIER,
                ),
            )
        )
    ) == [cp_id]
    db_session.rollback()
    counterparty(db_session, include_package=False)
    db_session.commit()
    with pytest.raises(WithdrawalError, match="replaced"):
        canonical_withdrawal.reverse_withdrawal(
            db_session, CTX, bank, withdrawal_id, reversed_by="Officer", reason="Synthetic reversal"
        )
    assert (
        len(
            db_session.scalars(
                select(CanonicalCounterparty).where(
                    CanonicalCounterparty.bank_id == BANK_A,
                    CanonicalCounterparty.withdrawn_at.is_(None),
                )
            ).all()
        )
        == 1
    )
