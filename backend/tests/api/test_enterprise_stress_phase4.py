"""Phase 4 per-risk methods wired end-to-end through the enterprise stress API.

Seeds a small canonical position book for the sample bank at the reporting-period
end, then proves the run incorporates the bottom-up credit stress (RWA migration +
FX revaluation + real exposure-class decomposition), the concentration stress and
the contingent-leverage stress — while the existing BankFinancialFact-only path
(no canonical positions) still runs unchanged (covered by test_enterprise_stress).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    LineageRecord,
)
from tests.api.helpers import ORG_1, headers
from tests.api.test_enterprise_stress import (
    RUNS_URL,
    _approve_scenario,
    _create_scenario,
    _period_id,
    _seed_checker,
)
from tests.api.test_ingestion import seed_bank

_AS_OF = date(2026, 3, 31)


def _seed_canonical_positions(bank_id: str) -> None:
    """A compact canonical book: a connected corporate group, a bank placement,
    a foreign-currency loan, a deposit funder and a derivative."""
    session = get_sessionmaker()()
    try:
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=bank_id,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=_AS_OF,
        )
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="phase4-fixture",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        common = {
            "organization_id": ORG_1,
            "bank_id": bank_id,
            "as_of_date": _AS_OF,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }

        corporate = CanonicalCounterparty(
            **common,
            source_reference="CP/BIGCORP",
            name="Big Corp Ltd",
            counterparty_type="CORPORATE",
            country_code="GH",
            group_reference="GRP-1",
        )
        peer_bank = CanonicalCounterparty(
            **common,
            source_reference="CP/PEERBANK",
            name="Peer Bank",
            counterparty_type="BANK_NON_OECD",
            country_code="GH",
        )
        session.add_all([corporate, peer_bank])

        corp_product = CanonicalProduct(
            **common,
            source_reference="PRODUCT/LN.CORP",
            product_code="LN.CORP",
            name="Corporate Term Loan",
            regulatory_category="CORPORATE_UNRATED",
            # A risk weight is a regulatory determination about the exposure and
            # is never assumed (audit 2026-08-22 D-8a) — the stress exposure book
            # now resolves it through ``capital.engine.resolve_risk_weight`` and
            # refuses a book that carries none, exactly as the capital engine
            # already did. ``CanonicalProduct.risk_weight_code`` is the field real
            # ingestion populates for precisely this (the T24 catalogs map
            # ``RISK.WEIGHT.BAND`` / ``riskWeightBand`` onto it), so a fixture
            # without one was modelling a book that cannot occur. RW100 is the
            # weight the Capital Requirements Directive gives a claim on a
            # corporate (¶138) and the code ``fact_derivation._LOAN_CATEGORY_MAP``
            # derives for ``CORPORATE_UNRATED`` on the fact plane, so the two
            # planes agree on the same book.
            risk_weight_code="RW100",
        )
        session.add(corp_product)
        session.flush()

        def position(  # noqa: PLR0913 - a keyword-only fixture builder
            ref: str,
            position_type: str,
            currency: str,
            *,
            balance: str,
            balance_ghs: str | None = None,
            stage: int | None = None,
            product: CanonicalProduct | None = None,
            counterparty: CanonicalCounterparty | None = None,
            notional: str | None = None,
            extra: dict[str, str] | None = None,
        ) -> None:
            row = CanonicalPosition(
                **common, source_reference=ref, position_type=position_type, currency=currency
            )
            session.add(row)
            session.flush()
            attributes: dict[str, str] = {}
            if balance_ghs is not None:
                attributes["balance_ghs"] = balance_ghs
            if extra:
                attributes.update(extra)
            session.add(
                CanonicalPositionSnapshot(
                    **common,
                    source_reference=ref,
                    position_id=row.id,
                    counterparty_id=counterparty.id if counterparty else None,
                    product_id=product.id if product else None,
                    balance=Decimal(balance),
                    notional=Decimal(notional) if notional else None,
                    ifrs9_stage=stage,
                    attributes=attributes,
                )
            )

        # Connected corporate group (two loans sharing GRP-1).
        position("LOAN/CORP1", "LOAN", "GHS", balance="100000000",
                 balance_ghs="100000000", stage=1, product=corp_product, counterparty=corporate)
        position("LOAN/CORP2", "LOAN", "GHS", balance="40000000",
                 balance_ghs="40000000", stage=1, product=corp_product, counterparty=corporate)
        # Foreign-currency corporate loan (FX revaluation channel).
        position("LOAN/USD", "LOAN", "USD", balance="2000000",
                 balance_ghs="30000000", stage=1, product=corp_product, counterparty=corporate)
        # Interbank placement (banks CRD class). No product register row, so the
        # risk-weight code rides on the snapshot attribute — the other of the two
        # paths ``_exposure_risk_weight`` reads. RW50 is the Capital Requirements
        # Directive weight for a claim on an UNRATED bank (¶123, and the ¶123
        # table's "Unrated" column); the peer is a Ghanaian bank with no external
        # credit assessment on the fixture.
        position("IBP/PEER", "INTERBANK_PLACEMENT", "GHS", balance="20000000",
                 balance_ghs="20000000", counterparty=peer_bank,
                 extra={"risk_weight_code": "RW50"})
        # A deposit funder (funding-source concentration).
        position("DEP/BIG", "DEPOSIT", "GHS", balance="60000000",
                 balance_ghs="60000000", counterparty=corporate)
        # A derivative (contingent leverage).
        position("DRV/1", "DERIVATIVE", "GHS", balance="0", notional="50000000",
                 extra={"notional_ghs": "50000000"})
        session.commit()
    finally:
        session.close()


def _run(client: TestClient) -> dict:
    bank_id = seed_bank(client)
    _seed_canonical_positions(bank_id)
    period_id = _period_id(client, bank_id)
    checker = _seed_checker(client)
    scenario_id = _create_scenario(client, code=f"p4_{uuid4().hex[:6]}")
    _approve_scenario(client, scenario_id, checker)
    response = client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Phase 4 per-risk stress.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_bottom_up_credit_and_concentration_flow_into_the_run(db_client: TestClient) -> None:
    run = _run(db_client)
    outcome = run["outcome"]

    # Bottom-up credit: rating migration + FX revaluation lift the credit RWA.
    bottom_up = outcome["bottom_up_credit"]
    assert Decimal(bottom_up["credit_rwa_uplift_factor"]) > Decimal("1")
    assert Decimal(bottom_up["stressed_credit_rwa"]) > Decimal(bottom_up["base_credit_rwa"])
    assert Decimal(bottom_up["fx_revaluation_rwa"]) > Decimal("0")  # the USD loan
    assert Decimal(bottom_up["migration_rwa"]) > Decimal("0")

    # Real exposure-class decomposition drives Table 1 "Impact of Adverse".
    impact = run["appendix_ii"]["table1_summary"]["impact_of_adverse"]
    year1 = next(item for item in impact if item["year"] == 1)
    by_class = {loss["exposure_class"]: Decimal(loss["loss"]) for loss in year1["losses"]}
    assert by_class["corporates"] > Decimal("0")

    # Concentration: the connected corporate group is the single largest name.
    concentration = outcome["concentration"]
    assert concentration["largest_group_key"] == "group:GRP-1"
    assert Decimal(concentration["total_incremental_loss"]) > Decimal("0")

    # Contingent leverage: the derivative inflates the leverage exposure.
    contingent = outcome["contingent_leverage"]
    assert contingent["has_contingent_positions"] is True
    assert Decimal(contingent["stressed_leverage_exposure"]) > Decimal(
        contingent["base_leverage_exposure"]
    )

    # Operational simulation always runs (seven scenarios, a worst-case charge).
    assert Decimal(outcome["operational"]["worst_loss_ghs"]) > Decimal("0")

    # Table 5 stress rows carry the derived Pillar-2 add-ons.
    stress_rows = [
        row for row in run["appendix_ii"]["table5_rwa"]["rows"] if row["label"].startswith("stress")
    ]
    assert stress_rows and stress_rows[0]["pillar2"]["credit_concentration"] is not None


def test_run_is_reproducible_with_the_canonical_book(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    _seed_canonical_positions(bank_id)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client, code="p4_repro")
    _approve_scenario(db_client, scenario_id, checker)
    body = {
        "scenario_id": scenario_id,
        "reporting_period_id": period_id,
        "reason": "Reproducibility with the canonical book.",
    }
    first = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=body)
    second = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=body)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["input_hash"] == second.json()["input_hash"]
