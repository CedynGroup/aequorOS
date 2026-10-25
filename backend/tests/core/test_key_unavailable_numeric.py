from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.attestation.service import digests, register_state
from app.attestation.service import workflow as attestation
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import FIELD_FORMAT, KeyStatus, KeyUnavailableError
from app.domain.credit import pd
from app.identity.public import Bank, RelatedParty
from app.identity.service.authorization import ALL_INSTITUTION_DATA
from app.live.public import BankReportingPeriod
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.schemas.packages import PackageApprovalRequestCreate, RegulatoryPackageCreate
from app.reporting.service import generation, validation, workflow
from app.schemas.regulatory_credit import CreditScenarioBatchCreate
from app.services import fact_derivation, regulatory_credit
from tests.core.test_bank_field_encryption import KEY_A, NEXT
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.reporting.service import test_filing_workflow_chain as review
from tests.reporting.service.bog_forms import test_bsd14 as rates
from tests.services import test_lrt_packs as lrt
from tests.services import test_regulatory_credit as credit
from tests.support.factories.attestation import relax_signing
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1

pytestmark = pytest.mark.usefixtures("return_generation_authority")


def connect(db: Session, monkeypatch: pytest.MonkeyPatch) -> LocalKeyProvider:
    provider = LocalKeyProvider()
    provider.add_key(KEY_A)
    provider.add_key(NEXT)

    def resolve(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", resolve)
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.storage_slug = "synthetic-numeric"
    registry.register(
        db,
        bank_id=bank.id,
        organization_id=ORG_1,
        storage_slug=bank.storage_slug,
        key=KEY_A,
        providers=resolve,
    )
    db.commit()
    return provider


def register(db: Session) -> None:
    lrt._seed_profile(db)  # pyright: ignore[reportPrivateUsage]
    lrt._seed_parties(db)  # pyright: ignore[reportPrivateUsage]
    lrt._seed_outlets(db)  # pyright: ignore[reportPrivateUsage]
    lrt._seed_products(db)  # pyright: ignore[reportPrivateUsage]
    db.commit()


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_complete_register_commitment_reads_stored_state_without_key(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, status: KeyStatus
) -> None:
    db = db_session
    materialize_canonical_test_book(db)
    provider = connect(db, monkeypatch)
    register(db)
    before = register_state.register_state_rows(db, lrt.MAKER, SAMPLE_BANK_ID)
    digest = digests.register_state_digest(before)
    party = next(row for row in before if row["table"] == "related_parties")
    stored_name = party["values"]["full_name"]
    assert isinstance(stored_name, str) and stored_name.startswith(FIELD_FORMAT + ":")
    assert FIELD_FORMAT in cast(Mapping[str, object], party["values"]["contact"])
    assert party["key_version"] is not None
    db.commit()
    db.expunge_all()
    provider.set_status(KEY_A, status)
    after = register_state.register_state_rows(db, lrt.MAKER, SAMPLE_BANK_ID)
    assert {row["table"] for row in after} == set(register_state.REGISTER_TABLES)
    assert digests.register_state_digest(after) == digest
    db.commit()
    with pytest.raises(KeyUnavailableError):
        db.scalars(select(RelatedParty).where(RelatedParty.bank_id == SAMPLE_BANK_ID)).all()
    db.rollback()
    with pytest.raises(KeyUnavailableError):
        lrt._generate(db, "LRT-PARTY")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_bsd14_generates_after_key_loss_with_protected_register(
    db_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: KeyStatus,
) -> None:
    reporting_date = rates._prepare(db_client)  # pyright: ignore[reportPrivateUsage]
    provider = connect(db_session, monkeypatch)
    register(db_session)
    provider.set_status(KEY_A, status)
    db_session.expunge_all()
    snapshot = rates._generate(db_client, reporting_date)  # pyright: ignore[reportPrivateUsage]
    payload = cast(Mapping[str, object], snapshot["bog_form"])
    cells = cast(dict[str, dict[str, float | None]], payload["cells"])[rates.SHEET]
    assert not payload["errors"]
    for ref, expected in rates.EXPECTED.items():
        actual = cells[ref]
        assert actual is not None and float(actual) == pytest.approx(expected)


@pytest.mark.usefixtures("credit_run_authority")
@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_numeric_classification_consumers_survive_key_loss(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, status: KeyStatus
) -> None:
    db = db_session
    materialize_canonical_test_book(db)
    provider = connect(db, monkeypatch)
    seed_canonical_fixture(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    fact_derivation.derive_facts(db, credit.CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    fact_derivation.derive_current_facts(db, credit.CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    credit._seed_prior_month_book(db)  # pyright: ignore[reportPrivateUsage]
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == FIXTURE_AS_OF,
        )
    )
    assert period is not None
    run = regulatory_credit.run_all_credit_scenarios(
        db, credit.CTX, SAMPLE_BANK_ID, CreditScenarioBatchCreate(reporting_period_id=period.id)
    )
    assert run.runs[0].status == "succeeded"
    active = regulatory_credit.list_credit_loans(
        db, credit.CTX, SAMPLE_BANK_ID, data_scope=ALL_INSTITUTION_DATA
    )
    assert any(row.counterparty_name for row in active.rows)
    monkeypatch.setattr(regulatory_credit, "_PD_MIN_MONTH_PAIRS", 1)
    monkeypatch.setattr(pd, "DEFAULT_MIN_LOAN_MONTHS", 1)
    db.commit()
    db.expunge_all()
    provider.set_status(KEY_A, status)
    facets = regulatory_credit.get_credit_loan_facets(
        db, credit.CTX, SAMPLE_BANK_ID, data_scope=ALL_INSTITUTION_DATA
    )
    assert facets.products
    migration = regulatory_credit.get_credit_migration(db, credit.CTX, SAMPLE_BANK_ID)
    assert migration.available and migration.matrix
    estimates = regulatory_credit.get_credit_pd(db, credit.CTX, SAMPLE_BANK_ID)
    assert estimates.available and estimates.matched_loan_months == 2
    package = generation.generate_package(
        db,
        credit.CTX,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="NPL-MONTHLY", reporting_date=FIXTURE_AS_OF),
    )
    assert package.source_runs
    db.commit()
    db.expunge_all()
    with pytest.raises(KeyUnavailableError):
        regulatory_credit.list_credit_loans(
            db, credit.CTX, SAMPLE_BANK_ID, data_scope=ALL_INSTITUTION_DATA
        )


def test_rotation_refuses_review_of_sealed_register_until_regeneration(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_session
    materialize_canonical_test_book(db)
    provider = connect(db, monkeypatch)
    register(db)
    relax_signing(db, organization_id=ORG_1, return_code="LRT-OUTLET")
    review._officer(db, review.APPROVER, "key-review@example.test", "Key Reviewer")  # pyright: ignore[reportPrivateUsage]
    package = lrt._generate(db, "LRT-OUTLET")  # pyright: ignore[reportPrivateUsage]
    validation.validate_package(db, lrt.MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db, lrt.MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    package_id = package.id
    sealed = package.register_state_digest
    db.commit()
    key = registry.scoped_key(db, bank_id=SAMPLE_BANK_ID, organization_id=ORG_1)

    def resolve(_name: str) -> LocalKeyProvider:
        return provider

    registry.rotate(db, key, NEXT, providers=resolve)
    db.commit()
    db.expunge_all()
    package = db.get(RegulatoryPackage, package_id)
    assert package is not None
    current = digests.register_state_digest(
        register_state.register_state_rows(db, lrt.MAKER, SAMPLE_BANK_ID)
    )
    assert current != sealed
    with pytest.raises(attestation.AttestationConflict, match="register_state_changed"):
        review._decide(db, review.APPROVER, package)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(attestation.AttestationConflict, match="register_state_changed"):
        attestation.ensure_submittable(db, lrt.MAKER, package)
    assert package.register_state_digest == sealed
    db.rollback()
    regenerated = lrt._generate(db, "LRT-OUTLET")  # pyright: ignore[reportPrivateUsage]
    assert regenerated.register_state_digest == current
    validation.validate_package(db, lrt.MAKER, SAMPLE_BANK_ID, regenerated.id)
    workflow.request_approval(
        db, lrt.MAKER, SAMPLE_BANK_ID, regenerated.id, PackageApprovalRequestCreate()
    )
    review._decide(db, review.APPROVER, regenerated)  # pyright: ignore[reportPrivateUsage]
    assert regenerated.version == package.version + 1
    assert package.register_state_digest == sealed
