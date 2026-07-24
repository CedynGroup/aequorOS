"""W6 return tests (docs/submission_pipeline_plan.md §W6.2–4).

Large Exposures (LE-MONTHLY): canonical counterparty exposures crossing the
10% NOF line, connected-counterparty grouping, exempt classification, the
top-100 cap, %-of-NOF math against the capital run's Tier 1, validate +
export round-trip, and the 409 paths (no baseline capital run / no canonical
positions).

LMT monitoring tools: the contractual maturity-mismatch ladder, top-10
depositor funding concentration and available unencumbered assets appear as
additional sections when canonical data exists.

IRRBB BoG ±450 bp parameterization: the param rows are seeded effective-dated
but the engine computes only the Basel set, so the ±450 return rows appear
ONLY when run metrics actually carry them — asserted both ways.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    LineageRecord,
    ParamStressShock,
    RegulatoryPackage,
    RegulatoryRun,
)
from app.schemas.regulatory_irr import IrrScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import regulatory_capital, regulatory_irr, regulatory_liquidity
from app.services.regulatory_reporting import calendar, generation, validation
from app.services.regulatory_reporting.exports import export_package
from app.services.regulatory_reporting.registry import REGISTRY
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)
PCT = Decimal("0.0001")


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


def _run_capital_baseline(db: Session) -> None:
    run = regulatory_capital.create_capital_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=_period_id(db), scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded", run
    db.expire_all()


def _run_liquidity_baseline(db: Session) -> None:
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=_period_id(db), scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded", run
    db.expire_all()


def _tier1(db: Session) -> Decimal:
    preview = regulatory_capital.get_bsd2_preview(db, MAKER, SAMPLE_BANK_ID, _period_id(db))
    return Decimal(str(preview.tier1_total.value))


class _CanonicalSeeder:
    """Minimal canonical scaffold (batch + lineage + row builders) at as-of."""

    def __init__(self, db: Session, as_of: date = REPORTING_DATE) -> None:
        self.db = db
        batch = IngestionBatch(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=as_of,
        )
        db.add(batch)
        db.flush()
        lineage = LineageRecord(
            organization_id=DEMO_ORG_ID,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="w6-fixture",
            input_lineage_ids=[],
        )
        db.add(lineage)
        db.flush()
        self.common: dict[str, Any] = {
            "organization_id": DEMO_ORG_ID,
            "bank_id": SAMPLE_BANK_ID,
            "as_of_date": as_of,
            "source_system": "EXCEL_CSV",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }

    def counterparty(  # noqa: PLR0913 - keyword-only fixture builder
        self,
        ref: str,
        name: str,
        counterparty_type: str,
        *,
        group_reference: str | None = None,
        tin: str | None = None,
    ) -> CanonicalCounterparty:
        row = CanonicalCounterparty(
            **self.common,
            source_reference=ref,
            name=name,
            counterparty_type=counterparty_type,
            group_reference=group_reference,
            external_identifiers={"tin": tin} if tin else {},
        )
        self.db.add(row)
        self.db.flush()
        return row

    def product(self, code: str, regulatory_category: str | None) -> CanonicalProduct:
        row = CanonicalProduct(
            **self.common,
            source_reference=f"PRODUCT/{code}",
            product_code=code,
            name=code,
            regulatory_category=regulatory_category,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def position(  # noqa: PLR0913 - keyword-only fixture builder
        self,
        ref: str,
        position_type: str,
        balance_ghs: Decimal | str,
        *,
        counterparty: CanonicalCounterparty | None = None,
        product: CanonicalProduct | None = None,
        maturity: date | None = None,
        ifrs9_stage: int | None = None,
        currency: str = "GHS",
        extra_attributes: dict[str, Any] | None = None,
    ) -> None:
        position = CanonicalPosition(
            **self.common,
            source_reference=ref,
            position_type=position_type,
            currency=currency,
        )
        self.db.add(position)
        self.db.flush()
        attributes: dict[str, Any] = {"balance_ghs": str(balance_ghs)}
        if extra_attributes:
            attributes.update(extra_attributes)
        self.db.add(
            CanonicalPositionSnapshot(
                **self.common,
                source_reference=ref,
                position_id=position.id,
                counterparty_id=counterparty.id if counterparty is not None else None,
                product_id=product.id if product is not None else None,
                balance=Decimal(str(balance_ghs)),
                contractual_maturity=maturity,
                ifrs9_stage=ifrs9_stage,
                attributes=attributes,
            )
        )
        self.db.flush()


def _generate(db: Session, return_code: str) -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code=return_code, reporting_date=REPORTING_DATE),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _sections(package: RegulatoryPackage) -> dict[str, dict[str, Any]]:
    return {section["code"]: section for section in package.snapshot["sections"]}


def _pct_of(value: Decimal, nof: Decimal) -> Decimal:
    return (value / nof * Decimal("100")).quantize(PCT)


def _seed_le_book(db: Session, tier1: Decimal) -> dict[str, Decimal]:
    """Canonical exposures sized relative to Tier 1 (= the NOF proxy)."""
    seeder = _CanonicalSeeder(db)
    sovereign_product = seeder.product("SEC.GOG.5Y", "SOVEREIGN_LOCAL_CCY")

    big_corp = seeder.counterparty("CP/BIG", "Akwaaba Industries Ltd", "CORPORATE", tin="C-001")
    alpha = seeder.counterparty(
        "CP/ALPHA", "Volta Alpha Ltd", "CORPORATE", group_reference="VOLTA-GROUP", tin="C-002"
    )
    beta = seeder.counterparty(
        "CP/BETA", "Volta Beta Ltd", "CORPORATE", group_reference="VOLTA-GROUP", tin="C-003"
    )
    small = seeder.counterparty("CP/SMALL", "Adum Traders", "SME", tin="C-004")
    gov = seeder.counterparty("CP/GOG", "Government of Ghana", "SOVEREIGN")

    drawn_big = (tier1 * Decimal("0.10")).quantize(Decimal("0.01"))
    notional_big = (tier1 * Decimal("0.04")).quantize(Decimal("0.01"))
    undrawn_big = notional_big * Decimal("0.5")
    member_each = (tier1 * Decimal("0.06")).quantize(Decimal("0.01"))
    small_amount = (tier1 * Decimal("0.04")).quantize(Decimal("0.01"))
    gov_amount = (tier1 * Decimal("0.15")).quantize(Decimal("0.01"))
    issuer_amount = (tier1 * Decimal("0.11")).quantize(Decimal("0.01"))

    seeder.position(
        "LOAN/BIG",
        "LOAN",
        drawn_big,
        counterparty=big_corp,
        maturity=date(2028, 3, 31),
        ifrs9_stage=2,
        extra_attributes={
            "notional_ghs": str(notional_big),
            "credit_conversion_factor": "0.5",
            "ecl_provision_ghs": "250000",
        },
    )
    seeder.position(
        "LOAN/ALPHA",
        "LOAN",
        member_each,
        counterparty=alpha,
        ifrs9_stage=1,
        maturity=date(2027, 3, 31),
    )
    seeder.position(
        "LOAN/BETA",
        "LOAN",
        member_each,
        counterparty=beta,
        ifrs9_stage=1,
        maturity=date(2027, 9, 30),
    )
    seeder.position("LOAN/SMALL", "LOAN", small_amount, counterparty=small, ifrs9_stage=1)
    seeder.position(
        "SEC/GOV", "SECURITY_HOLDING", gov_amount, counterparty=gov, product=sovereign_product
    )
    # Counterparty-less sovereign security: the issuer attribute is the identity.
    seeder.position(
        "SEC/ISSUER",
        "SECURITY_HOLDING",
        issuer_amount,
        product=sovereign_product,
        extra_attributes={"issuer": "Bank of Ghana"},
    )
    return {
        "big_total": drawn_big + undrawn_big,
        "drawn_big": drawn_big,
        "undrawn_big": undrawn_big,
        "group_total": member_each * 2,
        "member_each": member_each,
        "small": small_amount,
        "gov": gov_amount,
        "issuer": issuer_amount,
    }


# ---------------------------------------------------------------------------
# LE-MONTHLY — registry, derivation, 409s, export round-trip
# ---------------------------------------------------------------------------


def test_le_registry_entry_is_periodic_monthly_confirmed() -> None:
    definition = REGISTRY["LE-MONTHLY"]
    assert definition.family == "large_exposures"
    assert definition.frequency == "monthly"
    assert definition.event_driven is False
    assert definition.fidelity == "CONFIRMED"
    assert definition.default_channel == "orass_sandbox"
    assert definition.generator == "large_exposures"
    # Day 9 is the observed BoG monthly convention (day-count unstated in the
    # directive draft Part VI) — overridable at onboarding.
    assert definition.deadline_rule(REPORTING_DATE) == date(2026, 4, 9)
    assert "Part VI" in definition.directive_citation


def test_le_calendar_expands_monthly_obligation(db_session: Session) -> None:
    seed_sample_bank(db_session)
    obligations = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 1, as_of=date(2026, 4, 5)
    ).obligations
    le_items = [item for item in obligations if item.return_code == "LE-MONTHLY"]
    assert le_items, "the calendar must expand LE-MONTHLY like any periodic return"
    march = [item for item in le_items if item.reporting_date == REPORTING_DATE]
    assert march and march[0].due_date == date(2026, 4, 9)
    assert march[0].return_family == "large_exposures"


def test_le_generation_derives_templates_from_canonical_exposures(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _run_capital_baseline(db_session)
    tier1 = _tier1(db_session)
    amounts = _seed_le_book(db_session, tier1)

    package = _generate(db_session, "LE-MONTHLY")
    assert package.return_family == "large_exposures"
    assert package.snapshot["template_id"] == "bog-le-monthly-v1"
    assert package.snapshot["fidelity"] == "CONFIRMED"
    assert [entry["module"] for entry in package.source_runs] == ["capital"]

    sections = _sections(package)
    assert set(sections) == {
        "template_1",
        "template_1a",
        "template_2",
        "template_3",
        "template_4",
    }

    # Template 1: only the NON-exempt exposures ≥10% NOF, largest first.
    t1_rows = sections["template_1"]["rows"]
    assert [row["description"] for row in t1_rows] == [
        "Akwaaba Industries Ltd",
        "VOLTA-GROUP",
    ]
    big = t1_rows[0]
    assert Decimal(big["value"]) == amounts["big_total"]
    assert Decimal(big["drawn_ghs"]) == amounts["drawn_big"]
    assert Decimal(big["undrawn_ccf_ghs"]) == amounts["undrawn_big"]
    assert Decimal(big["pct_nof"]) == _pct_of(amounts["big_total"], tier1)
    assert big["connection"] == "single"
    assert big["tin"] == "C-001"
    assert big["ifrs9_stage"] == "2"
    group = t1_rows[1]
    assert group["connection"] == "group"
    assert Decimal(group["value"]) == amounts["group_total"]
    assert Decimal(group["pct_nof"]) == _pct_of(amounts["group_total"], tier1)
    total = sections["template_1"]["total"]
    assert total["equals_sum_of_rows"] is True
    assert Decimal(total["value"]) == amounts["big_total"] + amounts["group_total"]

    # Template 1a: membership rows for the group reported in Template 1.
    t1a_rows = sections["template_1a"]["rows"]
    assert {row["description"] for row in t1a_rows} == {"Volta Alpha Ltd", "Volta Beta Ltd"}
    for row in t1a_rows:
        assert row["group_reference"] == "VOLTA-GROUP"
        assert Decimal(row["value"]) == amounts["member_each"]
        assert "group reference" in row["basis_of_connection"]

    # Template 2: every entity (exempt included), largest first, within the cap.
    t2_names = [row["description"] for row in sections["template_2"]["rows"]]
    assert t2_names == [
        "Government of Ghana",
        "Akwaaba Industries Ltd",
        "VOLTA-GROUP",
        "Bank of Ghana",
        "Adum Traders",
    ]
    t2_by_name = {row["description"]: row for row in sections["template_2"]["rows"]}
    assert Decimal(t2_by_name["Akwaaba Industries Ltd"]["provisions_ghs"]) == Decimal("250000")

    # Template 3: exempt (sovereign counterparty + sovereign-category issuer).
    t3_rows = sections["template_3"]["rows"]
    assert {row["description"] for row in t3_rows} == {"Government of Ghana", "Bank of Ghana"}
    assert all("canonical category" in row["exempt_basis"] for row in t3_rows)

    # Template 4 is empty by construction: no CRM data, pre-CRM == post-CRM.
    assert sections["template_4"]["rows"] == []
    assert Decimal(sections["template_4"]["total"]["value"]) == Decimal("0")

    totals = {row["code"]: row["value"] for row in package.snapshot["totals"]}
    assert Decimal(totals["tier1_ghs"]) == tier1
    assert Decimal(totals["nof_ghs"]) == tier1  # documented Tier-1 proxy
    assert totals["large_exposure_count"] == "2"
    assert Decimal(totals["largest_exposure_pct_nof"]) == _pct_of(amounts["gov"], tier1)

    metadata = package.snapshot["metadata"]
    assert "Tier 1" in metadata["nof_basis"]
    assert metadata["large_exposure_threshold_pct_nof"] == "10"


def test_le_top_100_cap_truncates_with_info_finding(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _run_capital_baseline(db_session)
    seeder = _CanonicalSeeder(db_session)
    for index in range(103):
        counterparty = seeder.counterparty(
            f"CP/N{index:03d}", f"Counterparty {index:03d}", "CORPORATE"
        )
        seeder.position(
            f"LOAN/N{index:03d}",
            "LOAN",
            Decimal(1_000_000 + index),
            counterparty=counterparty,
        )

    package = _generate(db_session, "LE-MONTHLY")
    sections = _sections(package)
    assert len(sections["template_2"]["rows"]) == 100
    # Largest first: the highest-balance counterparty leads the table.
    assert sections["template_2"]["rows"][0]["description"] == "Counterparty 102"
    findings = package.snapshot["metadata"]["generation_findings"]
    assert any(item["rule"] == "le.top100_truncated" for item in findings)

    # The truncation note rides the validation report as an INFO finding.
    read = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert read.status == "validated"
    assert read.validation_report is not None
    truncation = [
        finding
        for finding in read.validation_report.findings
        if finding.rule == "le.top100_truncated"
    ]
    assert truncation and truncation[0].severity == "INFO"


def test_le_validates_and_exports_round_trip(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    seed_sample_bank(db_session)
    _run_capital_baseline(db_session)
    _seed_le_book(db_session, _tier1(db_session))
    package = _generate(db_session, "LE-MONTHLY")

    read = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert read.status == "validated", read.validation_report
    assert read.validation_report is not None and read.validation_report.passed is True

    xlsx = export_package(db_session, MAKER, package, "xlsx")
    pdf = export_package(db_session, MAKER, package, "pdf")
    db_session.commit()
    assert xlsx.size_bytes > 0 and pdf.size_bytes > 0
    slug = db_session.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    payloads = {}
    for obj in storage.list(slug, "outputs"):
        _, stream = storage.read(obj.location)
        payloads[obj.location.object_path] = stream.read()
    assert payloads[pdf.object_path].startswith(b"%PDF")
    workbook = load_workbook(io.BytesIO(payloads[xlsx.object_path]))
    assert workbook.sheetnames == [
        "Return Metadata",
        "LE Template 1 Large Exposures",
        "LE Template 1a Connected",
        "LE Template 2 Top 100",
        "LE Template 3 Exempted",
        "LE Template 4 Other Pre-CRM",
        "Fidelity & Provenance",
    ]


def test_le_409_without_baseline_capital_run(db_session: Session) -> None:
    seed_sample_bank(db_session)
    with pytest.raises(HTTPException) as exc_info:
        _generate(db_session, "LE-MONTHLY")
    assert exc_info.value.status_code == 409
    assert "no_baseline_run" in str(exc_info.value.detail)


def test_le_409_without_canonical_positions(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _run_capital_baseline(db_session)
    with pytest.raises(HTTPException) as exc_info:
        _generate(db_session, "LE-MONTHLY")
    assert exc_info.value.status_code == 409
    assert "no_canonical_positions" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# LMT — canonical monitoring-tool sections
# ---------------------------------------------------------------------------


def _seed_lmt_book(db: Session) -> None:
    seeder = _CanonicalSeeder(db)
    depositor_a = seeder.counterparty("CP/DEP-A", "Kanda Pensions Trust", "NBFI")
    depositor_b = seeder.counterparty("CP/DEP-B", "Osu Manufacturing Ltd", "CORPORATE")
    seeder.position("LOAN/L1", "LOAN", Decimal("8000000"), maturity=date(2026, 4, 15))
    seeder.position("SEC/S1", "SECURITY_HOLDING", Decimal("5000000"), maturity=date(2027, 6, 30))
    seeder.position("DEP/A", "DEPOSIT", Decimal("6000000"), counterparty=depositor_a)
    seeder.position(
        "DEP/B",
        "DEPOSIT",
        Decimal("3000000"),
        counterparty=depositor_b,
        maturity=date(2026, 5, 15),
    )
    seeder.position("DEP/ANON", "DEPOSIT", Decimal("1000000"))


def test_lmt_carries_ladder_concentration_and_unencumbered_sections(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    seed_sample_bank(db_session)
    _run_liquidity_baseline(db_session)
    _seed_lmt_book(db_session)

    package = _generate(db_session, "LMT")
    sections = _sections(package)
    assert {"maturity_ladder", "funding_concentration", "unencumbered_assets"} <= set(sections)

    ladder = {row["code"]: row for row in sections["maturity_ladder"]["rows"]}
    assert set(ladder) == {
        "overnight",
        "2-7d",
        "8-30d",
        "1-3m",
        "3-6m",
        "6-12m",
        ">1y",
        "non_contractual",
    }
    assert Decimal(ladder["8-30d"]["assets_ghs"]) == Decimal("8000000")  # loan, 15 days out
    assert Decimal(ladder["1-3m"]["liabilities_ghs"]) == Decimal("3000000")  # 45-day deposit
    assert Decimal(ladder[">1y"]["assets_ghs"]) == Decimal("5000000")  # 2027 security
    # Undated deposits report non-contractual (Table 2's final column), never overnight.
    assert Decimal(ladder["non_contractual"]["liabilities_ghs"]) == Decimal("7000000")
    assert "cumulative_gap_ghs" not in ladder["non_contractual"]
    total = sections["maturity_ladder"]["total"]
    assert total["equals_sum_of_rows"] is True
    assert Decimal(total["value"]) == Decimal("3000000")  # 13M assets - 10M liabilities

    concentration = sections["funding_concentration"]["rows"]
    assert [(row["description"], row["value"]) for row in concentration] == [
        ("Kanda Pensions Trust", "6000000"),
        ("Osu Manufacturing Ltd", "3000000"),
    ]
    assert Decimal(concentration[0]["pct_total_deposits"]) == Decimal("60")
    assert Decimal(sections["funding_concentration"]["total"]["value"]) == Decimal("10000000")

    unencumbered = sections["unencumbered_assets"]["rows"]
    assert unencumbered, "seeded HQLA-classified securities facts must appear"
    assert all(row["hqla_level"] == "L1" for row in unencumbered)

    totals = {row["code"]: row["value"] for row in package.snapshot["totals"]}
    assert Decimal(totals["contractual_gap_total_ghs"]) == Decimal("3000000")
    assert Decimal(totals["top10_depositor_share_pct"]) == Decimal("90")

    # The unattributed deposit is counted in the total but flagged as unrankable.
    findings = package.snapshot["metadata"]["generation_findings"]
    assert any(item["rule"] == "lmt.unattributed_deposits" for item in findings)

    read = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert read.status == "validated", read.validation_report

    artifact = export_package(db_session, MAKER, package, "xlsx")
    db_session.commit()
    slug = db_session.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    payload = b""
    for obj in storage.list(slug, "outputs"):
        if obj.location.object_path == artifact.object_path:
            _, stream = storage.read(obj.location)
            payload = stream.read()
    workbook = load_workbook(io.BytesIO(payload))
    assert "Contractual Maturity Mismatch" in workbook.sheetnames
    assert "Concentration of Funding" in workbook.sheetnames
    assert "Available Unencumbered Assets" in workbook.sheetnames


def test_lmt_without_canonical_positions_omits_position_tools(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _run_liquidity_baseline(db_session)
    package = _generate(db_session, "LMT")
    sections = _sections(package)
    # No canonical positions: the ladder and depositor ranking are honestly
    # absent; the HQLA-fact tool still renders from the seeded facts.
    assert "maturity_ladder" not in sections
    assert "funding_concentration" not in sections
    assert "unencumbered_assets" in sections
    assert {"hqla", "outflows", "inflows", "lcr_summary"} <= set(sections)


# ---------------------------------------------------------------------------
# IRRBB — BoG ±450 bp parameterization and conditional return rows
# ---------------------------------------------------------------------------


def _irr_baseline_run(db: Session) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == DEMO_ORG_ID,
            RegulatoryRun.bank_id == SAMPLE_BANK_ID,
            RegulatoryRun.module == "irr",
            RegulatoryRun.scenario_code == "baseline",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc())
        .limit(1)
    )
    assert run is not None
    return run


def test_irrbb_bog_450_params_flow_into_engine_outputs(db_session: Session) -> None:
    seed_sample_bank(db_session)
    shocks = db_session.scalars(
        select(ParamStressShock).where(
            ParamStressShock.module == "irr",
            ParamStressShock.scenario_code.in_(("parallel_up_450", "parallel_down_450")),
        )
    ).all()
    assert {(row.scenario_code, row.shock_key) for row in shocks} == {
        ("parallel_up_450", "parallel_bp"),
        ("parallel_down_450", "parallel_bp"),
    }
    assert {row.shock_value for row in shocks} == {Decimal("450"), Decimal("-450")}

    # GAP-5 closed the former engine gap: when the active parameter set
    # carries the BoG GHS ±450 bp shocks, the engine computes them as
    # INFORMATIONAL scenarios — present in the outputs, excluded from the
    # Basel outlier test (worst scenario/breach stay Basel-only).
    regulatory_irr.run_all_irr_scenarios(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        IrrScenarioBatchCreate(reporting_period_id=_period_id(db_session)),
    )
    metrics = _irr_baseline_run(db_session).metrics
    scenario_codes = {entry["scenario_code"] for entry in metrics["eve_by_scenario"]}
    assert {"parallel_up_450", "parallel_down_450"} <= scenario_codes
    assert "eve_up_450_ghs" in metrics and "ear_up_450_ghs" in metrics
    # The supervisory outlier verdict is unchanged by the add-ons.
    assert metrics["worst_scenario"] not in ("parallel_up_450", "parallel_down_450")
    for entry in metrics["eve_by_scenario"]:
        if entry["scenario_code"] in ("parallel_up_450", "parallel_down_450"):
            assert entry["breach"] is False


def test_irrbb_450_rows_render_only_when_metrics_carry_them(db_session: Session) -> None:
    seed_sample_bank(db_session)
    regulatory_irr.run_all_irr_scenarios(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        IrrScenarioBatchCreate(reporting_period_id=_period_id(db_session)),
    )

    # Simulate an uncalibrated bank: the engine now computes ±450 whenever the
    # param set carries the shocks (GAP-5), so strip the keys to model a param
    # set without the BoG calibration — the rows must be honestly absent.
    run = _irr_baseline_run(db_session)
    run.metrics = {
        key: value
        for key, value in run.metrics.items()
        if key
        not in ("eve_up_450_ghs", "eve_down_450_ghs", "ear_up_450_ghs", "ear_down_450_ghs")
    }
    db_session.commit()
    package = _generate(db_session, "IRRBB-PILOT")
    sections = _sections(package)
    eve_codes = {row["code"] for row in sections["eve_scenarios"]["rows"]}
    ear_codes = {row["code"] for row in sections["earnings_at_risk"]["rows"]}
    assert not {"eve_up_450_ghs", "eve_down_450_ghs"} & eve_codes
    assert not {"ear_up_450_ghs", "ear_down_450_ghs"} & ear_codes
    assert package.snapshot["metadata"]["bog_ghs_450_rows_present"] is False

    # Once a run's metrics actually carry the BoG ±450 outputs, the rows render.
    run = _irr_baseline_run(db_session)
    run.metrics = {
        **run.metrics,
        "eve_up_450_ghs": "-25000000",
        "eve_down_450_ghs": "26000000",
        "ear_up_450_ghs": "-16000000",
        "ear_down_450_ghs": "16000000",
    }
    db_session.commit()

    regenerated = _generate(db_session, "IRRBB-PILOT")
    sections = _sections(regenerated)
    eve_rows = {row["code"]: row for row in sections["eve_scenarios"]["rows"]}
    ear_rows = {row["code"]: row for row in sections["earnings_at_risk"]["rows"]}
    assert eve_rows["eve_up_450_ghs"]["value"] == "-25000000"
    assert eve_rows["eve_down_450_ghs"]["value"] == "26000000"
    assert ear_rows["ear_up_450_ghs"]["value"] == "-16000000"
    assert ear_rows["ear_down_450_ghs"]["value"] == "16000000"
    assert "BoG GHS calibration" in eve_rows["eve_up_450_ghs"]["description"]
    assert regenerated.snapshot["metadata"]["bog_ghs_450_rows_present"] is True
