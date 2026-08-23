"""Input-hash determinism: re-derivation must be a value-level no-op.

The chain this guards: ``derive_facts`` → persisted ``BankFinancialFact``
values → each module's ``current_input_hash`` (the SAME snapshot + hash the
immutable official runs use). Any unsorted collection, wall-clock read, or
derivation drift anywhere in that chain shows up here as a changed fact value
or a flipped hash.

Why it exists: on 2026-08-09 live-vs-official freshness flapped for three
modules. The cause was two CODE GENERATIONS sharing one database (an old
worker's derivations vs new snapshot schemas) — not nondeterminism — but
proving that required exactly the checks below, so they are pinned as the
regression guard for the real thing.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankFinancialFact, BankReportingPeriod
from app.services import (
    regulatory_capital,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)
from app.services.fact_derivation import derive_facts
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

_MODULE_HASHES = {
    "liquidity": regulatory_liquidity.current_input_hash,
    "capital": regulatory_capital.current_input_hash,
    "irr": regulatory_irr.current_input_hash,
    "fx": regulatory_fx.current_input_hash,
    "ftp": regulatory_ftp.current_input_hash,
}


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _fact_values(db: Session, period_id: Any) -> dict[str, str]:
    """Every persisted fact as a value-only record — ids and timestamps excluded."""
    rows = db.scalars(
        select(BankFinancialFact).where(BankFinancialFact.reporting_period_id == period_id)
    )
    return {
        f"{fact.fact_group}::{fact.category}": json.dumps(
            {
                "amount": str(fact.amount),
                "hqla_level": fact.hqla_level,
                "attributes": fact.attributes,
            },
            sort_keys=True,
        )
        for fact in rows
    }


def test_rederivation_reproduces_identical_facts_and_hashes(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    ctx = _ctx()

    first = derive_facts(db_session, ctx, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    period = db_session.get(BankReportingPeriod, first.reporting_period_id)
    assert bank is not None and period is not None

    facts_one = _fact_values(db_session, period.id)
    hashes_one = {name: fn(db_session, ctx, bank, period) for name, fn in _MODULE_HASHES.items()}
    # A hash of None means the module saw no facts — that would make the
    # determinism assertion vacuous, so it is a failure here, not a skip.
    assert all(hashes_one.values()), hashes_one

    second = derive_facts(db_session, ctx, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    assert second.reporting_period_id == period.id

    facts_two = _fact_values(db_session, period.id)
    hashes_two = {name: fn(db_session, ctx, bank, period) for name, fn in _MODULE_HASHES.items()}

    assert facts_one == facts_two
    assert hashes_one == hashes_two


def test_current_input_hash_is_stable_without_rederivation(db_session: Session) -> None:
    """Two reads of the same persisted state must hash identically — the
    freshness comparison depends on it."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    ctx = _ctx()
    result = derive_facts(db_session, ctx, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    period = db_session.get(BankReportingPeriod, result.reporting_period_id)
    assert bank is not None and period is not None

    for name, fn in _MODULE_HASHES.items():
        assert fn(db_session, ctx, bank, period) == fn(db_session, ctx, bank, period), name
