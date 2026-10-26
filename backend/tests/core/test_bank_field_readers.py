from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import Connection, event, select
from sqlalchemy.orm import Session

from app.core.key_management.local import LocalKeyProvider
from app.identity.public import Bank
from app.models import BankReportingPeriod
from app.models.canonical import CanonicalCounterparty, CanonicalPosition, CanonicalPositionSnapshot
from app.reporting.service.bog_forms.sources import ResolveContext
from app.reporting.service.bog_forms.sources_ext.bsd13 import contracts
from app.services import canonical_withdrawal
from app.services.canonical_withdrawal import WithdrawalError
from app.services.etl_dedup_jobs import (
    _load_cross_source_rows,  # pyright: ignore[reportPrivateUsage]
)
from app.services.etl_model_training import (
    _counterparty_rows,  # pyright: ignore[reportPrivateUsage]
)
from app.services.ingestion import _known_references  # pyright: ignore[reportPrivateUsage]
from tests.bi.fixtures import materialize_bi_plane
from tests.core import test_bank_field_encryption as fields
from tests.support.helpers import ORG_1

provider = fields.provider
type GroupAxis = Literal["name", "reference", "group_reference", "group", "parent"]
type Reader = Literal["references", "training", "matching", "bi", "contracts"]


@contextmanager
def counterparty_selects(db: Session) -> Generator[list[str]]:
    statements: list[str] = []

    def record(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "canonical_counterparties" in statement
        ):
            statements.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", record)


def assert_long_concentration_tokens(db: Session, axis: GroupAxis) -> None:
    width = 255 if axis in ("name", "reference") else 4096
    prefix = "\ufb03" * (width - 1)
    tokens: list[str] = []
    for suffix in ("A", "B", "A"):
        row = fields.counterparty(
            db, reference=f"synthetic-long-{len(tokens)}", include_package=False
        )
        value = prefix + suffix
        row.group_reference = None
        if axis == "name":
            row.name = value
        elif axis == "reference":
            row.group_reference = value
        else:
            row.attributes = {axis: value}
        db.flush()
        token = row.concentration_group_blind_index
        assert token is not None and len(token) == 64
        tokens.append(token)
    assert tokens[0] == tokens[2] and tokens[0] != tokens[1]
    db.commit()
    db.expunge_all()
    rows = list(
        db.scalars(
            select(CanonicalCounterparty)
            .where(
                CanonicalCounterparty.organization_id == ORG_1,
                CanonicalCounterparty.bank_id == fields.BANK_A,
            )
            .order_by(CanonicalCounterparty.source_reference_blind_index)
        )
    )
    assert len(rows) == 3
    for row in rows:
        actual = (
            row.name
            if axis == "name"
            else (row.group_reference if axis == "reference" else row.attributes[axis])
        )
        assert actual in (prefix + "A", prefix + "B")


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("axis", ["name", "reference", "group_reference", "group", "parent"])
def test_long_concentration_tokens_fit_and_keep_distinct_groups(
    db_session: Session,
    provider: LocalKeyProvider,
    axis: GroupAxis,
    connected: bool,
) -> None:
    if connected:
        fields.connect(db_session, provider)
    assert_long_concentration_tokens(db_session, axis)


def add_positions(db: Session, count: int) -> set[UUID]:
    ids: set[UUID] = set()
    for number in range(count):
        source_system = "EXCEL_CSV" if number % 2 == 0 else "API_PUSH"
        party = fields.counterparty(
            db,
            reference=f"SYNTHETIC-READER-{number}",
            include_package=False,
            source_system=source_system,
        )
        ids.add(party.id)
        party.country_code = "GH"
        common = dict(
            organization_id=ORG_1,
            bank_id=fields.BANK_A,
            as_of_date=fields.AS_OF,
            source_system=source_system,
            ingestion_batch_id=party.ingestion_batch_id,
            lineage_id=party.lineage_id,
            validation_status="accepted",
        )
        position = CanonicalPosition(
            **common,
            source_reference=f"synthetic-facility-{number}",
            position_type="FX_HEDGE",
            currency="GHS",
        )
        db.add(position)
        db.flush()
        db.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=position.source_reference,
                position_id=position.id,
                counterparty_id=party.id,
                balance=Decimal("123"),
                attributes={"sell_currency": "GHS", "buy_currency": "USD", "contract_rate": "0.1"},
            )
        )
    db.commit()
    db.expunge_all()
    return ids


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("count", [3, 30])
@pytest.mark.parametrize("reader", ["references", "training", "matching", "bi", "contracts"])
def test_bulk_identity_readers_use_bounded_selects(
    db_session: Session,
    provider: LocalKeyProvider,
    count: int,
    reader: Reader,
    connected: bool,
) -> None:
    if connected:
        fields.connect(db_session, provider)
    add_positions(db_session, count)
    bank = db_session.get(Bank, fields.BANK_A)
    assert bank is not None
    with counterparty_selects(db_session) as statements:
        if reader == "references":
            references, *_ = _known_references(db_session, fields.CTX, bank)
            assert references == {f"SYNTHETIC-READER-{number}" for number in range(count)}
        elif reader == "training":
            rows = _counterparty_rows(db_session, fields.CTX, bank.id)
            assert len(rows) == count and all(
                row["counterparty_name"] == fields.NAME for row in rows
            )
        elif reader == "matching":
            matches = _load_cross_source_rows(
                db_session, fields.CTX, bank.id, fields.AS_OF, ("FX_HEDGE",)
            )
            assert len(matches) == count and all(row.counterparty_key for row in matches)
        elif reader == "bi":
            result = materialize_bi_plane(
                db_session, organization_id=ORG_1, bank_id=bank.id, as_of=fields.AS_OF
            )
            assert result is not None and result.status == "succeeded"
            assert result.row_counts["bi_fact_position_daily"] == count
        else:
            context = ResolveContext(
                db_session, fields.CTX, bank, BankReportingPeriod(period_end=fields.AS_OF), "total"
            )
            book = contracts(context)
            assert len(book) == count and all(row.counterparty == fields.NAME for row in book)
    assert 0 < len(statements) <= (6 if reader == "bi" else 1)


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("count", [3, 30])
def test_pending_identity_updates_batch_load_without_overwriting_changes(
    db_session: Session,
    provider: LocalKeyProvider,
    count: int,
    connected: bool,
) -> None:
    if connected:
        fields.connect(db_session, provider)
    ids = add_positions(db_session, count)
    rows = list(
        db_session.scalars(
            select(CanonicalCounterparty).where(
                CanonicalCounterparty.organization_id == ORG_1,
                CanonicalCounterparty.bank_id == fields.BANK_A,
            )
        )
    )
    for row in rows:
        row.name = f"Synthetic replacement {row.id}"
    with counterparty_selects(db_session) as statements:
        db_session.flush()
    assert len(statements) == 1
    assert {row.id for row in rows} == ids
    assert all(row.name == f"Synthetic replacement {row.id}" for row in rows)
    assert all(row.group_reference == "SYNTHETIC-GROUP-17" for row in rows)
    db_session.commit()
    db_session.expunge_all()
    reloaded = list(
        db_session.scalars(
            select(CanonicalCounterparty).where(
                CanonicalCounterparty.organization_id == ORG_1,
                CanonicalCounterparty.bank_id == fields.BANK_A,
            )
        )
    )
    assert all(row.name == f"Synthetic replacement {row.id}" for row in reloaded)


@pytest.mark.parametrize("count", [1, 12])
@pytest.mark.parametrize("connection_timing", ["none", "before", "after_withdrawal"])
@pytest.mark.parametrize("conflict", [False, True])
def test_counterparty_reversal_reads_both_books_in_bounded_queries(
    db_session: Session,
    provider: LocalKeyProvider,
    count: int,
    connection_timing: str,
    conflict: bool,
) -> None:
    if connection_timing == "before":
        fields.connect(db_session, provider)
    original_ids = {
        fields.counterparty(db_session, reference=f"withdrawn-{number}", include_package=False).id
        for number in range(count)
    }
    withdrawal = fields.withdraw_counterparties(db_session, expected_rows=count)
    withdrawal_id = withdrawal.id
    if connection_timing == "after_withdrawal":
        fields.connect(db_session, provider)
    for number in range(count):
        fields.counterparty(
            db_session,
            reference="withdrawn-0" if conflict and number == 0 else f"live-{number}",
            include_package=False,
        )
    db_session.commit()
    db_session.expunge_all()
    bank = db_session.get(Bank, fields.BANK_A)
    assert bank is not None
    with counterparty_selects(db_session) as statements:
        if conflict:
            with pytest.raises(WithdrawalError, match="1 record.*same.*natural key") as refused:
                canonical_withdrawal.reverse_withdrawal(
                    db_session,
                    fields.CTX,
                    bank,
                    withdrawal_id,
                    reversed_by="Synthetic checker",
                    reason="Restore book",
                )
            assert refused.value.status_code == 409
        else:
            restored = canonical_withdrawal.reverse_withdrawal(
                db_session,
                fields.CTX,
                bank,
                withdrawal_id,
                reversed_by="Synthetic checker",
                reason="Restore book",
            )
            assert restored.status == "reversed" and restored.rows_restored == count
    assert len(statements) == 2
    db_session.flush()
    db_session.expunge_all()
    originals = list(
        db_session.scalars(
            select(CanonicalCounterparty).where(CanonicalCounterparty.id.in_(original_ids))
        )
    )
    assert len(originals) == count
    assert all((row.withdrawn_at is not None) == conflict for row in originals)
