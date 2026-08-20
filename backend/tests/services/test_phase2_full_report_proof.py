"""Phase 2 completion proof: EVERY registered return, end to end.

One test drives the exact production path over the deterministic seeded
book: the 22-scenario + forecast official-run sweep (`run_official_modules`,
the same entry the background pipeline uses), the reverse-stress frontier,
then — for every entry in the return registry — package generation and an
xlsx export with real bytes. The two template-gated obligations
(LAS-QUARTERLY) must refuse with ``template_pending`` — that
refusal is their specified behavior, not a gap.

This is the executable form of "generate all reports and confirm the fixes
worked": if a Phase 2 change breaks any return's generation or export, this
test names it.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, RegulatoryPackage
from app.schemas.institution_profile import (
    BankProductCreate,
    InstitutionProfilePut,
    RelatedPartyCreate,
    RelatedPartyRoleInput,
)
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.schemas.reverse_stress import ReverseStressRunCreate
from app.services import data_activation, institution_profile, reverse_stress
from app.services.regulatory_reporting import generation
from app.services.regulatory_reporting.exports import export_package
from app.services.regulatory_reporting.registry import REGISTRY
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.services.test_le_and_lmt import _CanonicalSeeder
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)

TEMPLATE_GATED = {"LAS-QUARTERLY"}  # BSD-MONTHLY retired: official BSD forms generate (bog_forms)
# ICAAP-STRESS-APPENDIX2 (Phase 5) sources the BoG Appendix II tables from a
# BOARD-ATTESTED enterprise-stress run. The Phase-2 official-run sweep mints no
# enterprise-stress run and no sign-off, so this return REFUSES here by design —
# that governance gate is its specified behaviour, proven end to end in
# tests/services/test_icaap_stress_appendix2_report.py.
STRESS_RUN_GATED = {"ICAAP-STRESS-APPENDIX2"}


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    return client


def _period_id(db: Session) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


def test_every_registered_return_generates_and_exports_end_to_end(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    materialize_canonical_test_book(db_session)
    period_id = _period_id(db_session)
    # A small canonical book so the position-derived returns (Large
    # Exposures, the LMT monitoring tools) have data to report.
    seeder = _CanonicalSeeder(db_session)
    corp = seeder.counterparty("PROOF/CP", "Volta Manufacturing Ltd", "CORPORATE", tin="C-100")
    seeder.position(
        "PROOF/LOAN",
        "LOAN",
        "60000000",
        counterparty=corp,
        maturity=date(2026, 12, 31),
        ifrs9_stage=1,
    )
    seeder.position(
        "PROOF/DEP", "DEPOSIT", "40000000", counterparty=corp, deposit_account_type="CALL"
    )
    # The LRT corporate packs pre-fill from the governance registers:
    # profile, at least one related party, at least one product.
    institution_profile.upsert_profile(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        InstitutionProfilePut(
            reason="Phase 2 proof: corporate profile capture",
            institution_type="Universal Bank",
            legal_entity_structure="Public Limited Company",
            authorisation_date=date(2005, 8, 15),
            incorporation_date=date(2004, 11, 2),
            tin="C0001234567",
            registration_number="CS-2004-11-0042",
        ),
    )
    institution_profile.create_related_party(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RelatedPartyCreate(
            reason="Phase 2 proof: register shareholder",
            party_type="legal_entity",
            full_name="Golden Coast Holdings Ltd",
            roles=[RelatedPartyRoleInput(role="shareholder")],
        ),
    )
    institution_profile.create_product(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        BankProductCreate(
            reason="Phase 2 proof: register deposit product",
            name="Corporate Call Account",
            product_type="Deposits",
        ),
    )

    # The production official-run sweep: 22 scenarios + forecast.
    outcomes = data_activation.run_official_modules(
        db_session, MAKER, SAMPLE_BANK_ID, period_id
    )
    by_module = {outcome.module: outcome for outcome in outcomes}
    assert set(by_module) == {
        "liquidity", "capital", "irr", "fx", "ftp", "forecast", "implied_rating",
    }  # fmt: skip
    # implied_rating (PD remediation) joined the sweep after this proof was
    # written: it REFUSES by design when no GHANA_SOVEREIGN rating has been
    # published — the hermetic book carries no rating register, so its refusal
    # is the honest outcome here, not a failure of the official run.
    rating = by_module["implied_rating"]
    assert rating.status != "succeeded"
    assert "GHANA_SOVEREIGN" in str(rating.error)
    failed = {
        module: outcome.error
        for module, outcome in by_module.items()
        if outcome.status != "succeeded" and module != "implied_rating"
    }
    assert not failed, f"Official module runs failed: {failed}"

    # Reverse stress rides the same book (Phase 2 item 4).
    frontier = reverse_stress.run_reverse_stress(
        db_session, MAKER, SAMPLE_BANK_ID, ReverseStressRunCreate(reporting_period_id=period_id)
    )
    assert frontier.narrative

    generated: dict[str, int] = {}
    for code, definition in sorted(REGISTRY.items()):
        payload = RegulatoryPackageCreate(return_code=code, reporting_date=REPORTING_DATE)
        if code in TEMPLATE_GATED:
            with pytest.raises(HTTPException) as excinfo:
                generation.generate_package(db_session, MAKER, SAMPLE_BANK_ID, payload)
            assert excinfo.value.detail["error_code"] == "template_pending", code  # type: ignore[index]
            continue
        if code in STRESS_RUN_GATED:
            with pytest.raises(HTTPException) as excinfo:
                generation.generate_package(db_session, MAKER, SAMPLE_BANK_ID, payload)
            assert (
                excinfo.value.detail["error_code"] == "no_attested_stress_run"  # type: ignore[index]
            ), code
            continue
        read = generation.generate_package(db_session, MAKER, SAMPLE_BANK_ID, payload)
        package = db_session.scalar(
            select(RegulatoryPackage).where(RegulatoryPackage.id == read.id)
        )
        assert package is not None, code
        assert package.status == "generated", code
        assert package.content_digest, code
        assert package.snapshot["template_id"] == definition.template_id, code

        artifact = export_package(db_session, MAKER, package, "xlsx")
        assert artifact.size_bytes > 0, code
        assert artifact.checksum_sha256, code
        generated[code] = len(package.snapshot["sections"])

    # Every registered return generates, except the gated ones that refuse by design
    # (LAS-QUARTERLY: template pending; ICAAP-STRESS-APPENDIX2: no attested stress run).
    assert len(generated) == len(REGISTRY) - len(TEMPLATE_GATED) - len(STRESS_RUN_GATED)
    # Phase 2 signature checks. LMT tool sections are data-gated (they render
    # only when their canonical inputs exist — plan W6.3), so this minimal
    # book yields the LCR subset + the data-backed tools; the full 16-section
    # grid over a complete book is proven in tests/services/test_le_and_lmt.py.
    assert generated["LMT"] >= 12
    assert "STRESS-PACK" in generated
