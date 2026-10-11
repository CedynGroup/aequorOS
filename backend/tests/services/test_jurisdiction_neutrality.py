"""Jurisdiction identity and regulator labels come from the bank and registry."""

from __future__ import annotations

import re

import pytest
from sqlalchemy.orm import Session

from app.adapters.market_data.scope_taxonomy import DataScope
from app.domain.bi.catalogue import catalogue
from app.domain.policy import PolicyUnresolvedError
from app.models import (
    Bank,
    BankFinancialFact,
    Jurisdiction,
    ParamCapitalThreshold,
    RegulatoryParameter,
)
from app.services import jurisdictions
from app.services.jurisdictions import base_currency


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
