"""Bank-local authority cannot change a sibling's policy, history or outputs."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.liquidity.schemas.liquidity_thresholds import (
    LiquidityHaircutUpdate,
    LiquidityThresholdUpdate,
)
from app.liquidity.service import liquidity_thresholds
from app.liquidity.service.regulatory_liquidity import (
    _currency_mismatch_limit,  # pyright: ignore[reportPrivateUsage] - pin the engine resolution seam
)
from app.models import (
    Bank,
    CurrentFinancialFact,
    Job,
    ParamLiquidityHaircut,
    ParamLiquidityThreshold,
)
from app.policy.public import get_active_bank_params, get_active_params, prefetch_active_params
from app.reporting.service.le_generation import (
    _haircut_for,  # pyright: ignore[reportPrivateUsage] - pin Table 9 resolution
    _haircut_schedule,  # pyright: ignore[reportPrivateUsage] - pin Table 9 resolution
    _table1_thresholds,  # pyright: ignore[reportPrivateUsage] - pin Table 1 resolution
)
from tests.data_engine.api.test_ingestion import seed_bank
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.liquidity.configure_helpers import configure_headers, grant_configure
from tests.liquidity.service.test_liquidity_currency_gaps import (
    _run,  # pyright: ignore[reportPrivateUsage] - reuse the real-engine fixture
    _seed_fx_book,  # pyright: ignore[reportPrivateUsage] - reuse the canonical fixture
)
from tests.services.test_le_and_lmt import (
    _CanonicalSeeder,  # pyright: ignore[reportPrivateUsage] - reuse the canonical fixture
    _generate,  # pyright: ignore[reportPrivateUsage] - reuse the real-return fixture
    _seed_table1_book,  # pyright: ignore[reportPrivateUsage] - reuse the canonical fixture
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

SIBLING = "BK-LOCLIQ02"
AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
PATHS = ("liquidity-thresholds", "liquidity-haircuts")


def _bank(
    db: Session, bank_id: str, org: str = ORG_1, country: str = "GH", kind: str = "universal_bank"
) -> Bank:
    bank = Bank(
        id=bank_id,
        organization_id=org,
        name="Liquidity scope fixture",
        short_name="Liquidity",
        jurisdiction_code=country,
        currency="NGN" if country == "NG" else "GHS",
        license_type=kind,
        institution_type=kind,
    )
    db.add(bank)
    db.flush()
    return bank


def _policy(db: Session, bank: Bank) -> None:
    """Two existing generations, deliberately identical keys across sibling banks."""
    for start, end, value in (
        (date(2026, 1, 1), date(2026, 3, 1), "110"),
        (date(2026, 3, 1), None, "120"),
    ):
        common = {
            "organization_id": bank.organization_id,
            "bank_id": bank.id,
            "jurisdiction_code": bank.jurisdiction_code,
            "effective_from": start,
            "effective_to": end,
            "approved_by": "Existing Board",
            "approval_timestamp": utc_now(),
        }
        for code in ("narrow_to_volatile", "currency_mismatch_limit_pct"):
            db.add(
                ParamLiquidityThreshold(
                    **common,
                    institution_class=(
                        "sdi" if bank.institution_type == "savings_and_loans" else "bank"
                    ),
                    threshold_code=code,
                    threshold_pct=Decimal(value),
                )
            )
        db.add(
            ParamLiquidityHaircut(
                **common,
                asset_class="SOVEREIGN",
                haircut_pct=Decimal(value) / Decimal("10"),
            )
        )
    db.add(
        CurrentFinancialFact(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            source_as_of_date=AS_OF,
            source_generation=1,
            fact_group="balance_sheet",
            category="cash_vault",
            amount=Decimal("1"),
            currency=bank.currency,
        )
    )
    db.flush()


def _engine_policy(
    bank_id: str,
) -> tuple[dict[str, tuple[Decimal, str]], Decimal | None, Decimal | None]:
    with get_sessionmaker()() as db:
        bank = db.get(Bank, bank_id)
        assert bank is not None
        return (
            _table1_thresholds(db, CTX, bank, AS_OF),
            _haircut_for("SOVEREIGN_GOG_TBILL", _haircut_schedule(db, CTX, bank, AS_OF)),
            _currency_mismatch_limit(db, CTX, bank, AS_OF),
        )


def test_bank_a_write_leaves_sibling_registers_and_engine_policy_unchanged(
    db_client: TestClient,
) -> None:
    bank_a = seed_bank(db_client)
    with get_sessionmaker()() as db:
        sibling = _bank(db, SIBLING)
        bank = db.get(Bank, bank_a)
        assert bank is not None
        _policy(db, bank)
        _policy(db, sibling)
        db.commit()
    before_engine = _engine_policy(SIBLING)
    before_reads: dict[str, str] = {}
    for path in PATHS:
        response = db_client.get(f"/api/v1/banks/{SIBLING}/{path}?as_of={AS_OF}", headers=headers())
        assert response.status_code == 200, response.text
        before_reads[path] = response.text
    actor = configure_headers(bank_a)
    for effective_from, floor, haircut in (
        ("2026-04-01", "130", "25"),
        ("2026-05-01", "140", "30"),
    ):
        for path, values in (
            (
                "liquidity-thresholds",
                {
                    "thresholds": {
                        "narrow_to_volatile": floor,
                        "currency_mismatch_limit_pct": "10",
                    }
                },
            ),
            ("liquidity-haircuts", {"haircuts": {"SOVEREIGN": haircut}}),
        ):
            payload = TypeAdapter(dict[str, JsonValue]).validate_python(
                {
                    "effective_from": effective_from,
                    "approved_by": "Bank A Board",
                    "reason": "Bank A only annual policy",
                    **values,
                }
            )
            allowed = db_client.put(f"/api/v1/banks/{bank_a}/{path}", headers=actor, json=payload)
            assert allowed.status_code == 200, allowed.text
            denied = db_client.put(f"/api/v1/banks/{SIBLING}/{path}", headers=actor, json=payload)
            assert denied.status_code == 403, denied.text
    assert _engine_policy(SIBLING) == before_engine
    assert _engine_policy(bank_a) == (
        {**before_engine[0], "narrow_to_volatile": (Decimal("140"), "board_register")},
        Decimal("30"),
        Decimal("10"),
    )
    for path, before in before_reads.items():
        response = db_client.get(f"/api/v1/banks/{SIBLING}/{path}?as_of={AS_OF}", headers=headers())
        assert response.status_code == 200, response.text
        assert response.text == before
    with get_sessionmaker()() as db:
        # Supersession and invalidation must not fan out to the sibling with real inputs.
        assert set(db.scalars(select(Job.bank_id).where(Job.job_type == "pipeline_refresh"))) == {
            bank_a,
        }
        for model in (ParamLiquidityThreshold, ParamLiquidityHaircut):
            rows = db.scalars(
                select(model).where(
                    model.bank_id == bank_a, model.effective_from == date(2026, 3, 1)
                )
            ).all()
            assert rows and all(row.effective_to == date(2026, 4, 1) for row in rows)


@pytest.mark.parametrize(
    ("org", "country", "kind"),
    [
        (ORG_1, "GH", "universal_bank"),
        (ORG_1, "NG", "universal_bank"),
        (ORG_1, "GH", "savings_and_loans"),
        (ORG_2, "GH", "universal_bank"),
    ],
)
def test_policy_resolver_requires_complete_bank_scope(
    db_client: TestClient,
    org: str,
    country: str,
    kind: str,
) -> None:
    bank_a = seed_bank(db_client)
    with get_sessionmaker()() as db:
        bank = _bank(db, SIBLING, org, country, kind)
        _policy(db, bank)
        db.commit()
        ctx = TenantContext(organization_id=org)
        assert _currency_mismatch_limit(db, ctx, bank, AS_OF) == Decimal("120")
        assert _haircut_for(
            "SOVEREIGN_GOG_TBILL", _haircut_schedule(db, ctx, bank, AS_OF)
        ) == Decimal("12")
        assert get_active_bank_params(db, ORG_1, bank_a, "GH", ParamLiquidityThreshold, AS_OF) == []
        assert (
            get_active_bank_params(
                db,
                ORG_1,
                SIBLING,
                "NG" if country == "GH" else "GH",
                ParamLiquidityThreshold,
                AS_OF,
            )
            == []
        )
        if org != ORG_1:
            assert (
                get_active_bank_params(
                    db,
                    ORG_1,
                    SIBLING,
                    country,
                    ParamLiquidityThreshold,
                    AS_OF,
                )
                == []
            )
        for model in (ParamLiquidityThreshold, ParamLiquidityHaircut):
            with pytest.raises(ValueError, match="require bank_id"):
                get_active_params(db, org, country, model, AS_OF)
            with pytest.raises(ValueError, match="require bank_id"):
                prefetch_active_params(db, org, country, model, [])
            rows = get_active_params(db, org, country, model, AS_OF, bank_id=SIBLING)
            assert rows and all(row.bank_id == SIBLING for row in rows)
            assert (
                prefetch_active_params(db, org, country, model, [AS_OF], bank_id=SIBLING).active_on(
                    AS_OF
                )
                == rows
            )
        before_register = liquidity_thresholds.get_register(
            db, ctx, SIBLING, AS_OF
        ).model_dump_json()
        before_haircuts = liquidity_thresholds.get_haircut_schedule(
            db, ctx, SIBLING, AS_OF
        ).model_dump_json()
        before_floors = _table1_thresholds(db, ctx, bank, AS_OF)
        actor = configure_headers(bank_a)
        for path, values in (
            ("liquidity-thresholds", {"thresholds": {"currency_mismatch_limit_pct": "1"}}),
            ("liquidity-haircuts", {"haircuts": {"SOVEREIGN": "90"}}),
        ):
            response = db_client.put(
                f"/api/v1/banks/{bank_a}/{path}",
                json={
                    **values,
                    "effective_from": "2026-04-01",
                    "approved_by": "Bank A Board",
                    "reason": "Bank A only",
                },
                headers=actor,
            )
            assert response.status_code == 200, response.text
        db.expire_all()
        assert (
            liquidity_thresholds.get_register(db, ctx, SIBLING, AS_OF).model_dump_json()
            == before_register
        )
        assert (
            liquidity_thresholds.get_haircut_schedule(db, ctx, SIBLING, AS_OF).model_dump_json()
            == before_haircuts
        )
        assert _table1_thresholds(db, ctx, bank, AS_OF) == before_floors
        assert _currency_mismatch_limit(db, ctx, bank, AS_OF) == Decimal("120")
        assert _haircut_for(
            "SOVEREIGN_GOG_TBILL", _haircut_schedule(db, ctx, bank, AS_OF)
        ) == Decimal("12")


@pytest.mark.usefixtures("return_generation_authority")
def test_sibling_write_preserves_computed_lmt_and_currency_results(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_fx_book(db_session)
    _seed_table1_book(db_session)
    seeder = _CanonicalSeeder(db_session)
    product = seeder.product("SCOPE.BOND", "SOVEREIGN_GOG_BOND_0RW")
    seeder.position(
        "SCOPE/BOND",
        "SECURITY_HOLDING",
        Decimal("4000000"),
        product=product,
        maturity=date(2029, 6, 30),
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    _policy(db_session, bank)
    _bank(db_session, SIBLING)
    grant_configure(db_session, SIBLING)
    baseline = _run(db_session, "baseline")
    assert baseline.status == "succeeded"
    before: JsonValue = TypeAdapter[JsonValue](JsonValue).validate_python(
        _generate(db_session, "LMT").snapshot["sections"]
    )
    validations = [
        (row.rule_code, row.passed, row.message)
        for row in baseline.validations
        if row.rule_code.startswith("currency_mismatch_")
    ]
    assert validations
    liquidity_thresholds.update_register(
        db_session,
        CTX,
        SIBLING,
        LiquidityThresholdUpdate(
            effective_from=date(2026, 3, 15),
            approved_by="Sibling Board",
            reason="Sibling only",
            thresholds={
                "narrow_to_volatile": Decimal("250"),
                "currency_mismatch_limit_pct": Decimal("1"),
            },
        ),
    )
    liquidity_thresholds.update_haircut_schedule(
        db_session,
        CTX,
        SIBLING,
        LiquidityHaircutUpdate(
            effective_from=date(2026, 3, 15),
            approved_by="Sibling Board",
            reason="Sibling only",
            haircuts={"SOVEREIGN": Decimal("75")},
        ),
    )
    after_run = _run(db_session, "baseline")
    assert after_run.status == "succeeded"
    assert [
        (row.rule_code, row.passed, row.message)
        for row in after_run.validations
        if row.rule_code.startswith("currency_mismatch_")
    ] == validations
    assert (
        TypeAdapter[JsonValue](JsonValue).validate_python(
            _generate(db_session, "LMT").snapshot["sections"]
        )
        == before
    )
