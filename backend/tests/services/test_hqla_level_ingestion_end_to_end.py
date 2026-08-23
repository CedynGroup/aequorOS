"""``attributes.hqla_level`` must survive ingestion, not just classification.

Forensic re-audit 2026-08-22, round 3. ``D-6``'s fix made the Basel HQLA level
an ingested determination first (``fact_derivation._classify_security_hqla``,
documented in ``docs/API_INTEGRATION.md`` §3.4), and the classifier itself is
pinned by ``test_hqla_level_classification.py`` — but every one of those tests
constructs a ``_PositionRow`` by hand. No row on any real book carries the
attribute, so the branch that reads it had never once executed against data that
travelled the ingestion pipeline. An attribute that a bank cannot actually SEND
is a control that exists only in the unit tests.

This walks the whole chain on the hermetic book: an API push carrying
``attributes.hqla_level`` → committed canonical position → ``derive_facts`` →
the persisted ``securities`` fact → ``compute_lcr``, which charges the governed
Level-2A haircut and the Level-2 caps on it. It also walks the refusal chain,
because a level the taxonomy does not define must be excluded here exactly as it
is in the pure classifier — never read as Level 1.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.domain.liquidity.engine import LiquidityFact, LiquidityParams, compute_lcr
from app.models import Bank, BankFinancialFact, CanonicalPositionSnapshot
from app.services import regulatory_parameters
from app.services.fact_derivation import derive_facts
from tests.api.helpers import ORG_1, USER_1
from tests.api.test_ingestion import seed_bank
from tests.api.test_push_api import commit, open_push, stage
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.factories.reconciliation import allow_fixture_balance_gap
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID

AS_OF = FIXTURE_AS_OF.isoformat()

#: One sovereign product so the pushed holdings land in the sovereign bucket —
#: the only bucket ``_classify_security_hqla`` runs over.
PRODUCTS = [
    {
        "source_reference": "SEC.GOG.NOTE",
        "product_code": "SEC.GOG.NOTE",
        "name": "Government of Ghana note",
        "regulatory_category": "SOVEREIGN_0RW",
    }
]

POSITIONS = [
    {
        # The bank's OWN Basel determination: a Level-2A holding.
        "source_reference": "SEC-L2A-0001",
        "position_type": "SECURITY_HOLDING",
        "currency": "GHS",
        "balance": "200000000",
        "product_code": "SEC.GOG.NOTE",
        "attributes": {"hqla_level": "L2A"},
    },
    {
        # A level outside the Basel taxonomy. Must be EXCLUDED, never read as L1.
        "source_reference": "SEC-BAD-0002",
        "position_type": "SECURITY_HOLDING",
        "currency": "GHS",
        "balance": "50000000",
        "product_code": "SEC.GOG.NOTE",
        "attributes": {"hqla_level": "L3"},
    },
    {
        # Funds the two holdings, so the hermetic book's balance-sheet identity
        # gap is the fixture's own and the reconciliation control is exercised
        # rather than widened by this test.
        "source_reference": "DEP-HQLA-0003",
        "position_type": "DEPOSIT",
        "currency": "GHS",
        "balance": "250000000",
    },
]


def _session() -> Session:
    session = get_sessionmaker()()
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            sql_text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
        )
    return session


def _push_the_classified_holdings(client: TestClient, bank_id: str) -> None:
    opened = open_push(client, bank_id, "push-hqla-level-001", as_of=AS_OF)
    assert opened.status_code == 201, opened.text
    push_id = opened.json()["push_batch_id"]
    staged = stage(
        client, bank_id, push_id, {"entities": {"product": PRODUCTS, "position": POSITIONS}}
    )
    assert staged.status_code == 200, staged.text
    committed = commit(client, bank_id, push_id)
    assert committed.status_code == 201, committed.text
    assert committed.json()["batch"]["records_accepted"] == 4


def _derived_securities(db: Session) -> dict[str, BankFinancialFact]:
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    seed_canonical_fixture(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    allow_fixture_balance_gap(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    result = derive_facts(db, ctx, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db.commit()
    rows = db.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.reporting_period_id == result.reporting_period_id,
            BankFinancialFact.fact_group == "securities",
        )
    )
    return {fact.category: fact for fact in rows}


def test_a_pushed_hqla_level_reaches_the_canonical_position_verbatim(
    db_client: TestClient,
) -> None:
    """Step 1 of the chain: the ingestion contract preserves the attribute."""
    bank_id = seed_bank(db_client)
    _push_the_classified_holdings(db_client, bank_id)

    session = _session()
    try:
        rows = {
            row.source_reference: row
            for row in session.scalars(
                select(CanonicalPositionSnapshot).where(
                    CanonicalPositionSnapshot.source_reference.in_(
                        ("SEC-L2A-0001", "SEC-BAD-0002")
                    )
                )
            )
        }
        assert rows["SEC-L2A-0001"].attributes["hqla_level"] == "L2A"
        assert rows["SEC-BAD-0002"].attributes["hqla_level"] == "L3"
        assert rows["SEC-L2A-0001"].validation_status == "accepted"
    finally:
        session.close()


def test_an_ingested_level_2a_becomes_a_level_2a_fact_and_an_unknown_level_is_refused(
    db_client: TestClient,
) -> None:
    """Steps 2-3: derivation reads the ingested determination, and refuses the rest."""
    bank_id = seed_bank(db_client)
    _push_the_classified_holdings(db_client, bank_id)

    session = _session()
    try:
        facts = _derived_securities(session)

        # The ingested Level 2A is emitted as its own fact, at its own level.
        assert "hqla_level2a" in facts, sorted(facts)
        level2a = facts["hqla_level2a"]
        assert level2a.hqla_level == "L2A"
        assert level2a.amount == Decimal("200000000.0000")

        # ``L3`` is not a Basel level. It is excluded from HQLA — carried at full
        # value with NO level — and it is emphatically not folded into L1.
        assert facts["hqla_unclassified"].hqla_level is None
        assert facts["hqla_unclassified"].amount == Decimal("50000000.0000")
        assert facts["bog_bills"].hqla_level == "L1"
        assert facts["gog_bonds"].hqla_level == "L1"
    finally:
        session.close()


def test_the_ingested_level_2a_is_haircut_and_capped_in_the_lcr(
    db_client: TestClient,
) -> None:
    """Step 4: the whole point — the ingested tier changes the filed LCR numerator.

    Hand-computed against the seeded, cited control-plane rates (BCBS 238 ¶52
    15% Level-2A haircut; ¶47 40% Level-2 cap):

        L2A post-haircut = 200,000,000 x 0.85 = 170,000,000
        cap leg          = max((170,000,000) - (40/60) x L1, 0)
    """
    bank_id = seed_bank(db_client)
    _push_the_classified_holdings(db_client, bank_id)

    session = _session()
    try:
        facts = _derived_securities(session)
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        hqla = regulatory_parameters.resolve_hqla_parameters(
            session, bank, as_of=FIXTURE_AS_OF
        )
        assert hqla.haircut_pct["L2A"] == Decimal("15")

        level1_amount = sum(
            (fact.amount for fact in facts.values() if fact.hqla_level == "L1"), Decimal("0")
        )
        engine_facts = [
            LiquidityFact(
                fact_group="securities",
                category=category,
                amount=fact.amount,
                hqla_level=fact.hqla_level,
            )
            for category, fact in facts.items()
        ] + [
            LiquidityFact(
                fact_group="balance_sheet",
                category="retail_stable",
                amount=Decimal("1000000000"),
                hqla_level=None,
                side="liability",
            )
        ]
        result = compute_lcr(engine_facts, _params(hqla))

        composition = result.hqla_composition
        # The fixture's own Level-1 stock, unchanged by this push.
        assert level1_amount == Decimal("44000000.0000")
        assert composition.level1 == level1_amount
        # The 15% Level-2A haircut, charged from the control plane.
        assert composition.level2a == Decimal("170000000.0000")
        assert composition.level2b == Decimal("0")
        # The 40% Level-2 cap binds hard on this book, hand-computed:
        #   max(170,000,000 - (40/60) x 44,000,000, 0) = 140,666,666.6667
        assert composition.level2b_cap_adjustment == Decimal("0")
        assert composition.level2_cap_adjustment == Decimal("140666666.6667")
        #   44,000,000 + 170,000,000 - 140,666,666.6667
        assert composition.total == Decimal("73333333.3333")
        # The invariant the formula cannot fake: admitted Level 2 lands at 40%.
        admitted_level2 = (
            composition.level2a + composition.level2b - composition.level2_cap_adjustment
        )
        assert (admitted_level2 / composition.total * Decimal("100")).quantize(
            Decimal("0.01")
        ) == Decimal("40.00")
        # The unclassified 50,000,000 earns no HQLA credit at all: it is neither
        # in the stock nor in any cap base.
        assert composition.level1 + composition.level2a == Decimal("214000000.0000")
        assert result.all_hqla_level1 is False
    finally:
        session.close()


def _params(hqla: regulatory_parameters.HqlaParameters) -> LiquidityParams:
    return LiquidityParams(
        outflow_rates={"retail_stable": Decimal("5")},
        inflow_rates={},
        asf_weights={},
        rsf_weights={},
        inflow_cap_pct=Decimal("75"),
        lcr_min_pct=Decimal("100"),
        lcr_amber_floor_pct=Decimal("110"),
        nsfr_min_pct=Decimal("100"),
        nsfr_amber_floor_pct=Decimal("110"),
        hqla_haircut_pct=hqla.haircut_pct,
        hqla_level2_cap_pct=hqla.level2_cap_pct,
        hqla_level2b_cap_pct=hqla.level2b_cap_pct,
    )
