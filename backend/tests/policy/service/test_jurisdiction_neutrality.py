"""Jurisdiction identity and regulator labels come from the bank and registry."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.adapters.market_data.scope_taxonomy import DataScope
from app.api.deps import TenantContext
from app.bi.domain.catalogue import catalogue
from app.domain.authority.results import Computed
from app.liquidity.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.liquidity.service import regulatory_liquidity
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CurrentFinancialFact,
    Jurisdiction,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    RegulatoryParameter,
    RegulatoryRun,
)
from app.policy.domain import PolicyUnresolvedError
from app.policy.service import jurisdictions
from app.policy.service.jurisdictions import base_currency
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)


def _liquidity_book(
    db: Session,
    jurisdiction: str = "NG",
    currency: str = "NGN",
    *,
    cap_bound: bool = False,
    mixed_hqla: bool = False,
) -> tuple[Bank, BankReportingPeriod, TenantContext]:
    materialize_canonical_test_book(db)
    bank = db.get(Bank, SAMPLE_BANK_ID)
    period = db.scalar(
        select(BankReportingPeriod)
        .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert bank is not None and period is not None
    bank.jurisdiction_code, bank.currency = jurisdiction, currency
    for model in (ParamLcrRunoffRate, ParamNsfrWeight, ParamCapitalThreshold):
        db.execute(
            update(model)
            .where(model.organization_id == DEMO_ORG_ID)
            .values(jurisdiction_code=jurisdiction)
        )
    db.execute(
        delete(RegulatoryParameter).where(RegulatoryParameter.jurisdiction_code == jurisdiction)
    )
    db.execute(
        update(RegulatoryParameter)
        .where(RegulatoryParameter.jurisdiction_code == "GH")
        .values(jurisdiction_code=jurisdiction)
    )
    db.execute(
        update(ParamCapitalThreshold)
        .where(
            ParamCapitalThreshold.organization_id == DEMO_ORG_ID,
            ParamCapitalThreshold.threshold_code == "lcr_inflow_cap_pct",
        )
        .values(value_pct=Decimal("1" if cap_bound else "100"))
    )
    facts = list(
        db.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
            )
        )
    )
    assert facts
    for fact in facts:
        fact.currency = currency
        if mixed_hqla and fact.fact_group == "securities" and fact.hqla_level == "L1":
            fact.hqla_level = "L2B" if fact.category == "bog_bills" else "L2A"
            fact.amount = Decimal("1000000000000")
        db.add(
            CurrentFinancialFact(
                organization_id=fact.organization_id,
                bank_id=fact.bank_id,
                source_as_of_date=period.period_end,
                source_generation=1,
                fact_group=fact.fact_group,
                category=fact.category,
                amount=fact.amount,
                currency=fact.currency,
                hqla_level=fact.hqla_level,
                risk_weight_code=fact.risk_weight_code,
                ccf_pct=fact.ccf_pct,
                rate_pct=fact.rate_pct,
                income_year=fact.income_year,
                attributes=cast(dict[str, object], fact.attributes),
            )
        )
    db.flush()
    return bank, period, TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)


@pytest.mark.parametrize(("jurisdiction", "currency"), [("NG", "NGN"), ("KE", "KES")])
@pytest.mark.parametrize("cap_bound", [False, True])
@pytest.mark.parametrize("mixed_hqla", [False, True])
def test_liquidity_consumers_label_each_narrative_with_the_banks_currency(
    db_session: Session,
    jurisdiction: str,
    currency: str,
    cap_bound: bool,
    mixed_hqla: bool,
) -> None:
    bank, period, ctx = _liquidity_book(
        db_session,
        jurisdiction,
        currency,
        cap_bound=cap_bound,
        mixed_hqla=mixed_hqla,
    )
    dashboard = regulatory_liquidity.get_liquidity_dashboard(db_session, ctx, bank.id, period.id)
    assert not dashboard.stored
    assert not dashboard.metrics.refusals
    messages = {row.rule_code: row.message for row in dashboard.validations}
    assert {"inflow_cap_applied", "hqla_all_level1"} <= messages.keys()
    cap = messages["inflow_cap_applied"]
    assert ("cap bound:" if cap_bound else "cap did not bind:") in cap
    assert cap.count(currency) == 2
    hqla = messages["hqla_all_level1"]
    assert hqla.count(currency) == (3 if mixed_hqla else 1)
    if mixed_hqla:
        assert "HQLA after haircuts:" in hqla
        assert "Level-2 cap deducted" in hqla and "Level-2B cap deducted" in hqla
    else:
        assert "All high quality liquid assets are Level 1:" in hqla
    assert all(
        "GHS" not in message and "cedis" not in message.lower() for message in messages.values()
    )
    live = regulatory_liquidity.compute_live(db_session, ctx, bank, period)
    assert Decimal(cast(str, live.metrics["lcr_pct"])) == dashboard.metrics.lcr_pct
    if mixed_hqla:
        assert (
            next(row.message for row in live.findings if row.rule_id == "hqla_all_level1") == hqla
        )
    analysis = regulatory_liquidity.compute_scenario_analysis(db_session, ctx, bank, period, {})
    assert isinstance(analysis.lcr, Computed)
    assert analysis.lcr.value.inflow_cap_applied == cap_bound
    run = regulatory_liquidity.create_liquidity_run(
        db_session,
        ctx,
        bank.id,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period.id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded", run.error
    assert {row.rule_code: row.message for row in run.validations} == messages
    stored = regulatory_liquidity.get_liquidity_dashboard(db_session, ctx, bank.id, period.id)
    assert stored.stored
    assert {row.rule_code: row.message for row in stored.validations} == messages


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", ""),
        ("currency", " "),
        ("currency", None),
        ("jurisdiction_code", ""),
        ("jurisdiction_code", " "),
        ("jurisdiction_code", None),
        ("jurisdiction_code", "ZZ"),
    ],
)
@pytest.mark.parametrize("consumer", ["dashboard", "live", "analysis", "official_run"])
def test_liquidity_consumers_refuse_unresolved_identity(
    db_session: Session,
    field: str,
    value: str | None,
    consumer: str,
) -> None:
    bank, period, ctx = _liquidity_book(db_session)
    setattr(bank, field, value)
    expected_error = ValueError if field == "currency" else PolicyUnresolvedError
    with db_session.no_autoflush, pytest.raises(expected_error):
        if consumer == "dashboard":
            regulatory_liquidity.get_liquidity_dashboard(db_session, ctx, bank.id, period.id)
        elif consumer == "live":
            regulatory_liquidity.compute_live(db_session, ctx, bank, period)
        elif consumer == "analysis":
            regulatory_liquidity.compute_scenario_analysis(db_session, ctx, bank, period, {})
        else:
            regulatory_liquidity.create_liquidity_run(
                db_session,
                ctx,
                bank.id,
                RegulatoryRunCreate(
                    module="liquidity", reporting_period_id=period.id, scenario_code="baseline"
                ),
            )
    with db_session.no_autoflush:
        assert db_session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0


@pytest.mark.parametrize("code", ["GH", "NG", "KE", "ZA"])
def test_identity_and_display_resolve_the_banks_registry_row(
    db_session: Session, code: str
) -> None:
    row = db_session.get(Jurisdiction, code)
    if code == "ZA":
        row = Jurisdiction(
            code=code,
            country_name="South Africa",
            currency_code="ZAR",
            currency_name="Rand",
            locale="en-ZA",
            central_bank_name="South African Reserve Bank",
            regulator_short="SARB",
        )
        db_session.add(row)
        db_session.flush()
    assert row is not None
    bank = Bank(
        id="BK-TEST0001",
        organization_id="OR-TEST0001",
        name="Registry bank",
        short_name="Registry",
        currency=f" {row.currency_code.lower()} ",
        jurisdiction_code=f" {code.lower()} ",
        license_type="universal",
        institution_type="universal_bank",
    )
    assert jurisdictions.jurisdiction_code(bank) == code
    assert base_currency(bank) == row.currency_code
    bank.jurisdiction_code = code
    assert jurisdictions.regulator_name(db_session, bank) == row.central_bank_name
    assert jurisdictions.regulator_short(db_session, bank) == row.regulator_short
    assert jurisdictions.require_jurisdiction(db_session, bank) is row


def test_bi_catalogue_display_text_is_jurisdiction_neutral() -> None:
    cat = catalogue()
    texts = [(member.id, member.label, member.description) for member in cat.members()]
    texts += [(hierarchy.id, hierarchy.label, "") for hierarchy in cat.hierarchies()]
    texts += [
        (dimension.id, value.label, "")
        for dimension in cat.dimensions()
        for value in dimension.values
    ]
    identity = re.compile(r"\b(?:GHS|NGN|KES|ZAR|EUR|GBP|cedis|naira|shilling)\b", re.IGNORECASE)
    for member_id, label, description in texts:
        assert not identity.search(label + " " + description), member_id


@pytest.mark.parametrize("field", ["currency", "jurisdiction_code"])
@pytest.mark.parametrize("value", ["", " ", None])
def test_missing_identity_refuses_instead_of_selecting_another_country(
    db_session: Session, field: str, value: str | None
) -> None:
    bank = Bank(
        id="BK-TEST0001",
        organization_id="OR-TEST0001",
        name="Unresolved bank",
        short_name="Unresolved",
        currency="NGN",
        jurisdiction_code="NG",
        license_type="universal",
        institution_type="universal_bank",
    )
    setattr(bank, field, value)
    if field == "currency":
        with pytest.raises(ValueError):
            base_currency(bank)
    else:
        with pytest.raises(PolicyUnresolvedError):
            jurisdictions.jurisdiction_code(bank)
        with pytest.raises(PolicyUnresolvedError):
            jurisdictions.require_jurisdiction(db_session, bank)


def test_an_unknown_jurisdiction_has_neutral_display_and_refuses_policy(
    db_session: Session,
) -> None:
    bank = Bank(
        id="BK-TEST0001",
        organization_id="OR-TEST0001",
        name="Unknown bank",
        short_name="Unknown",
        currency="NGN",
        jurisdiction_code="ZZ",
        license_type="universal",
        institution_type="universal_bank",
    )
    assert jurisdictions.regulator_name(db_session, bank) == "the banking regulator"
    assert jurisdictions.regulator_short(db_session, bank) == "Regulator"
    with pytest.raises(PolicyUnresolvedError):
        jurisdictions.require_jurisdiction(db_session, bank)


def test_parameter_tables_carry_no_jurisdiction_default() -> None:
    """The jurisdiction is part of a governed parameter's IDENTITY.

    ``RegulatoryParameterMixin`` defaulted ``jurisdiction_code="GH"`` and is
    inherited by NINE parameter tables, so one default filed every board-register
    generation under Ghana — a Nigerian tenant's included (enterprise audit §6).
    ``bank_financial_facts.currency`` defaulted to "GHS" the same way.
    """
    checks = (
        (ParamCapitalThreshold, "jurisdiction_code"),
        (RegulatoryParameter, "jurisdiction_code"),
        (BankFinancialFact, "currency"),
    )
    for model, column_name in checks:
        column = model.__table__.columns[column_name]
        assert column.default is None, (
            f"{model.__tablename__}.{column_name} must not carry a default — it is "
            "part of the row's identity and belongs to the write site."
        )
        assert not column.nullable


def test_bank_model_carries_no_jurisdiction_default() -> None:
    """currency and jurisdiction_code are required, never defaulted.

    Independent defaults ("GHS" and "GH") could silently disagree, so a bank
    created with jurisdiction_code="NG" kept reporting in cedis. Requiring both
    turns that into a loud failure at the creation site.
    """
    for column_name in ("currency", "jurisdiction_code"):
        column = Bank.__table__.columns[column_name]
        assert column.default is None, (
            f"banks.{column_name} must not carry a default — it decides the "
            "institution's country identity and belongs to the creation site."
        )
        assert column.server_default is None
        assert not column.nullable


def test_base_currency_refuses_to_guess() -> None:
    """No fallback. An unset currency is a skipped decision, not a Ghanaian bank."""
    bank = Bank(
        id="BK-TEST0001",
        organization_id="OR-TEST0001",
        name="Unset Currency Bank",
        short_name="UCB",
        currency="",
        jurisdiction_code="NG",
        license_type="universal",
        institution_type="universal_bank",
    )
    with pytest.raises(ValueError, match="no reporting currency"):
        base_currency(bank)


def test_every_seeded_jurisdiction_can_ingest_its_own_market_data() -> None:
    """The DataScope enum gates INGESTION, not just vendor pulls.

    ``manual_upload/parser`` resolves ``YIELD_CURVE_{currency}`` and
    ``FX_SPOT_{base}_{quote}`` against the taxonomy and rejects the row when no
    scope exists ("unsupported currency ... no yield curve scope exists"). So a
    jurisdiction in the registry with no scopes cannot get its data into the
    platform at all — by the Excel/CSV route, the API-push route, or any other.

    That is how Kenya and South Africa came to be seeded jurisdictions that could
    upload a yield curve but not their own USD spot rate. This test is the thing
    that should fail the next time a jurisdiction is added without its scopes.

    Scoped to curve + FX spot deliberately: those are what the calculation layer
    actually consumes (``fact_derivation`` reads exactly three market-data
    functions). Macro and security master are display/aspiration and are left to
    the LSEG trial to size — see docs/lseg_trial_scope.md §2.
    """
    # Mirrors the jurisdictions registry seed (GH/NG/KE/ZA). Kept literal rather
    # than read from the DB so the test is hermetic and fails on the ADDITION of
    # a jurisdiction, which is exactly when the scopes get forgotten.
    seeded = {"GH": "GHS", "NG": "NGN", "KE": "KES", "ZA": "ZAR"}
    scope_names = {scope.value for scope in DataScope}

    missing: list[str] = []
    for code, currency in sorted(seeded.items()):
        for required in (f"YIELD_CURVE_{currency}", f"FX_SPOT_USD_{currency}"):
            if required not in scope_names:
                missing.append(f"{code} ({currency}) -> {required}")

    assert not missing, (
        "Seeded jurisdictions cannot ingest their own market data. Add the scope "
        "to DataScope and a verification_required entry to BOTH vendor catalogs "
        "(never invent a RIC or security — §16.4):\n  " + "\n  ".join(missing)
    )


def test_base_currency_normalises() -> None:
    bank = Bank(
        id="BK-TEST0002",
        organization_id="OR-TEST0001",
        name="Nigeria Test Bank",
        short_name="NTB",
        currency=" ngn ",
        jurisdiction_code="NG",
        license_type="universal",
        institution_type="universal_bank",
    )
    assert base_currency(bank) == "NGN"
