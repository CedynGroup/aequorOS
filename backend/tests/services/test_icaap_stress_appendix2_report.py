"""ICAAP stress-test submission — BoG Appendix II Tables 1–6 (docs/stress.md §3.8).

Phase 5 end-to-end over the deterministic seeded book: a Board-ATTESTED
enterprise-stress run is re-tabulated into the exact Appendix II table structure,
generated on the real package lifecycle (maker-checker, immutability, content
digest, default signing policy) and exported to pdf/xlsx. Also proves the
governance gate: the return REFUSES without a Board-attested enterprise-stress
run, and the with/without-management-actions blocks appear only when the run
modelled an approved management-actions plan (¶67(f)).
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pdfplumber
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    EnterpriseStressSignoff,
    RegulatoryPackage,
    User,
)
from app.schemas.enterprise_stress import EnterpriseStressRunCreate
from app.schemas.enterprise_stress_signoff import (
    StressSignoffAttestation,
    StressSignoffCreate,
    StressSignoffTransition,
)
from app.schemas.management_actions import (
    ManagementActionPlanApproval,
    ManagementActionPlanCreate,
    ManagementActionPlanTransition,
)
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.schemas.stress import MacroScenarioApproval, MacroScenarioCreate, MacroScenarioTransition
from app.services import (
    enterprise_stress,
    enterprise_stress_signoff,
    macro_scenarios,
    management_action_plans,
)
from app.services.attestation import digests
from app.services.attestation import workflow as attestation
from app.services.regulatory_reporting import generation
from app.services.regulatory_reporting.exports import export_package
from app.services.regulatory_reporting.registry import REGISTRY
from app.services.regulatory_reporting.templates import (
    APPENDIX2_EXPOSURE_CLASS_LABELS,
    build_rendered_return,
    get_template,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.storage.inmemory import InMemoryStorageClient

pytestmark = pytest.mark.usefixtures("fx_run_authority", "irrbb_run_authority")

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
CHECKER_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CHECKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=CHECKER_ID, roles=("approver",))
REPORTING_DATE = date(2026, 3, 31)
RETURN_CODE = "ICAAP-STRESS-APPENDIX2"
SDI_RETURN_CODE = "SDI-STRESS-ANNUAL"


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


def _seed_checker(db: Session) -> None:
    if db.get(User, CHECKER_ID) is not None:
        return
    db.add(
        User(
            id=CHECKER_ID,
            organization_id=DEMO_ORG_ID,
            email="stress-board@aequoros.example",
            display_name="Board Member",
            role="approver",
        )
    )
    db.commit()


def _severe_paths() -> list[dict[str, str | int]]:
    levels = {
        "gdp_growth": ("0.05", "0.00"),
        "interest_rate": ("0.20", "0.25"),
        "inflation": ("0.15", "0.21"),
        "unemployment": ("0.06", "0.09"),
        "fx_usd_ghs": ("12.5", "15.0"),
        "gse_index": ("5000", "3500"),
        "gog_yield": ("0.22", "0.26"),
    }
    paths: list[dict[str, str | int]] = []
    for variable, (base, stress) in levels.items():
        for year in (1, 2, 3):
            paths.append(
                {
                    "variable": variable,
                    "year_index": year,
                    "base_value": base,
                    "stress_value": stress,
                }
            )
    return paths


def _approved_scenario(db: Session, code: str = "adverse_2027") -> UUID:
    created = macro_scenarios.create_scenario(
        db,
        MAKER,
        MacroScenarioCreate.model_validate(
            {
                "code": code,
                "name": "2027 severe downturn",
                "scenario_type": "adverse",
                "severity": "severe",
                "horizon_years": 3,
                "narrative": "GDP contraction, cedi depreciation, rate spike.",
                "source": "BoG MPC + internal desk",
                "paths": _severe_paths(),
                "reason": "Author the annual adverse scenario.",
            }
        ),
    )
    macro_scenarios.submit_scenario(
        db, MAKER, created.id, MacroScenarioTransition(reason="Ready for approval.")
    )
    macro_scenarios.approve_scenario(
        db, CHECKER, created.id, MacroScenarioApproval(reason="Reviewed and approved.")
    )
    return created.id


def _approved_plan(db: Session, code: str = "recovery_2027") -> UUID:
    created = management_action_plans.create_plan(
        db,
        MAKER,
        ManagementActionPlanCreate.model_validate(
            {
                "code": code,
                "name": "Capital-restoration plan",
                "bank_id": None,
                "actions": [
                    {
                        "action_id": "dividend_suspension",
                        "kind": "revise_dividend",
                        "label": "Suspend dividends",
                        "trigger_kind": "always",
                        "effective_year": 1,
                        "dividend_reduction_pct": "100",
                    },
                    {
                        "action_id": "capital_raise",
                        "kind": "raise_capital",
                        "label": "Equity issuance",
                        "trigger_kind": "always",
                        "effective_year": 1,
                        "sizing": "fill_residual",
                        "capital_raise_ghs": "1",
                        "counts_as_paid_up": True,
                    },
                ],
                "reason": "Author the recovery plan.",
            }
        ),
    )
    management_action_plans.submit_plan(
        db, MAKER, created.id, ManagementActionPlanTransition(reason="submit")
    )
    management_action_plans.approve_plan(
        db, CHECKER, created.id, ManagementActionPlanApproval(reason="approve")
    )
    return created.id


def _run_enterprise_stress(
    db: Session,
    scenario_id: UUID,
    plan_id: UUID | None = None,
    *,
    car_target_pct: Decimal | None = None,
) -> UUID:
    read = enterprise_stress.run_enterprise_stress_test(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        EnterpriseStressRunCreate(
            scenario_id=scenario_id,
            reporting_period_id=_period_id(db),
            management_action_plan_id=plan_id,
            car_target_pct=car_target_pct,
            reason="Annual ICAAP stress test.",
        ),
    )
    return read.run_id


SCENARIO_NARRATIVE = (
    "Enterprise-wide adverse scenario covering credit, liquidity, market and IRRBB "
    "across the banking book."
)
ASSUMPTIONS_RATIONALE = (
    "Documented linear elasticities; expert-judgement overlays challenged by the CRO."
)
METHODOLOGY_SUMMARY = "Bottom-up credit migration + coherent macro fan-out."
BOARD_CHALLENGE = "Challenged the FX severity; retained as plausible."
CREDIBILITY_RATIONALE = (
    "The Board reviewed and challenged the framework and results; the assumptions and "
    "severity are credible."
)


_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _attested_signoff(  # noqa: PLR0913 - one keyword per narrative element
    db: Session,
    run_id: UUID,
    *,
    scenario_narrative: str = SCENARIO_NARRATIVE,
    assumptions_rationale: str = ASSUMPTIONS_RATIONALE,
    methodology_summary: str | None = METHODOLOGY_SUMMARY,
    board_challenge: str | None = BOARD_CHALLENGE,
    credibility_rationale: str = CREDIBILITY_RATIONALE,
    checker: TenantContext = CHECKER,
) -> UUID:
    """A Board-attested sign-off over ``run_id`` carrying these narratives.

    The sign-off schemas refuse control characters (ICAAP P0 fix round), so a
    narrative that carries one can only exist as a row stored BEFORE that
    validation — which is exactly what the export-robustness tests need. Such a
    value is written onto the row directly after the validated lifecycle has
    run, with a plain placeholder going through the API schemas.
    """
    raw = {
        "scenario_narrative": scenario_narrative,
        "assumptions_rationale": assumptions_rationale,
        "methodology_summary": methodology_summary,
        "board_challenge": board_challenge,
        "credibility_rationale": credibility_rationale,
    }
    legacy = {
        key: value
        for key, value in raw.items()
        if value is not None and _CONTROL_CHARACTER.search(value)
    }
    clean = {key: ("Placeholder." if key in legacy else value) for key, value in raw.items()}
    signoff = enterprise_stress_signoff.create_signoff(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        StressSignoffCreate(
            run_id=run_id,
            scenario_narrative=clean["scenario_narrative"],  # type: ignore[arg-type]
            assumptions_rationale=clean["assumptions_rationale"],  # type: ignore[arg-type]
            methodology_summary=clean["methodology_summary"],
            reason="Prepare the stress-run sign-off.",
        ),
    )
    enterprise_stress_signoff.submit_signoff(
        db, MAKER, SAMPLE_BANK_ID, signoff.id, StressSignoffTransition(reason="submit")
    )
    enterprise_stress_signoff.attest_signoff(
        db,
        checker,
        SAMPLE_BANK_ID,
        signoff.id,
        StressSignoffAttestation(
            credibility_rationale=clean["credibility_rationale"],  # type: ignore[arg-type]
            board_challenge=clean["board_challenge"],
            reason="Board attestation.",
        ),
    )
    if legacy:
        row = db.get(EnterpriseStressSignoff, signoff.id)
        assert row is not None
        for key, value in legacy.items():
            setattr(row, key, value)
        db.commit()
    return signoff.id


def _generate(db: Session) -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code=RETURN_CODE, reporting_date=REPORTING_DATE),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _generate_sdi(db: Session) -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code=SDI_RETURN_CODE, reporting_date=REPORTING_DATE),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _section(snapshot: dict[str, Any], code: str) -> dict[str, Any] | None:
    return next((s for s in snapshot["sections"] if s["code"] == code), None)


def _prepare(db: Session, *, plan: bool = False) -> RegulatoryPackage:
    materialize_canonical_test_book(db)
    _seed_checker(db)
    scenario_id = _approved_scenario(db)
    plan_id = _approved_plan(db) if plan else None
    run_id = _run_enterprise_stress(db, scenario_id, plan_id)
    _attested_signoff(db, run_id)
    return _generate(db)


def test_generates_appendix_ii_tables_from_attested_run(db_session: Session) -> None:
    package = _prepare(db_session)
    snapshot = package.snapshot
    assert snapshot["template_id"] == "bog-icaap-stress-appendix2-v1"
    assert package.return_family == "icaap_stress"
    assert package.status == "generated"
    assert package.content_digest

    # Table 1 summary: current + 3 base + 3 stress capital positions.
    positions = _section(snapshot, "t1_summary_positions")
    assert positions is not None
    labels = [row["code"] for row in positions["rows"]]
    assert labels == [
        "current",
        "base_y1",
        "base_y2",
        "base_y3",
        "stress_y1",
        "stress_y2",
        "stress_y3",
    ]

    # Table 1 vulnerability granularity: loss by CRD exposure class per year (¶67(g)).
    impact = _section(snapshot, "t1_impact_of_adverse")
    assert impact is not None and impact["rows"]
    assert {row["year"] for row in impact["rows"]} == {"1", "2", "3"}

    # All six Appendix II tables + governance section are present.
    for code in (
        "t1_capital_required",
        "t2_capital_projection",
        "t3_profit_and_loss",
        "t4_financial_position",
        "t5_rwa",
        "t6_risk_drivers",
        "governance",
    ):
        section = _section(snapshot, code)
        assert section is not None and section["rows"], code

    # Table 6 carries the 7 macro drivers × 3 years.
    assert len(_section(snapshot, "t6_risk_drivers")["rows"]) == 21  # type: ignore[index]

    # The directive invariant: stressed Total Pillar-1 RWA (Table 5) equals
    # Table 1's stressed RWA — carried verbatim from the enterprise-stress run.
    t5_by_label = {
        row["code"]: row["value"]
        for row in _section(snapshot, "t5_rwa")["rows"]  # type: ignore[index]
    }
    for row in positions["rows"]:
        if row["code"].startswith("stress_y"):
            assert t5_by_label[row["code"]] == row["total_rwa"], row["code"]

    # Provenance: sourced from exactly the one enterprise-stress run.
    assert [entry["module"] for entry in package.source_runs] == ["enterprise_stress"]

    # Without a management-actions plan the with/without blocks are omitted.
    assert _section(snapshot, "t1_management_actions") is None
    assert _section(snapshot, "t1_post_capitalisation") is None
    assert snapshot["metadata"]["with_management_actions"] is False

    # Governance provenance is carried on the snapshot (¶20, ¶67(b)(c)).
    governance = snapshot["metadata"]["governance"]
    assert governance["status"] == "attested"
    assert governance["scenario_narrative"]
    assert governance["credibility_rationale"]
    assert governance["attested_by"]


def test_sdi_stress_return_uses_attested_evidence_without_basel_table2(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    db_session.flush()
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session, code="sdi_adverse_2027")
    run_id = _run_enterprise_stress(db_session, scenario_id)
    _attested_signoff(db_session, run_id)

    package = _generate_sdi(db_session)

    assert package.return_family == "sdi"
    assert package.return_code == SDI_RETURN_CODE
    assert _section(package.snapshot, "t2_capital_projection") is None
    assert _section(package.snapshot, "t1_summary_positions") is not None
    assert _section(package.snapshot, "t5_rwa") is not None
    assert package.snapshot["metadata"]["basel_table2_included"] is False
    assert package.snapshot["metadata"]["report_scope"].endswith("Table 2 excluded.")
    assert [entry["module"] for entry in package.source_runs] == ["enterprise_stress"]


def test_default_signing_policy_applies_to_the_stress_return(db_session: Session) -> None:
    package = _prepare(db_session)
    # The return inherits the platform default signing policy: a signature is
    # required before it can be filed (docs/attestation_esignature.md).
    policy = attestation.package_policy(db_session, MAKER, package)
    assert policy.require_signature is True
    assert policy.require_signed_pdf is True


def test_with_and_without_management_actions_blocks(db_session: Session) -> None:
    package = _prepare(db_session, plan=True)
    snapshot = package.snapshot
    assert snapshot["metadata"]["with_management_actions"] is True
    # The with-actions blocks (¶67(f)) now render alongside the pre-action
    # (Post-Adverse) positions.
    actions = _section(snapshot, "t1_management_actions")
    post_cap = _section(snapshot, "t1_post_capitalisation")
    assert actions is not None and actions["rows"]
    assert post_cap is not None and post_cap["rows"]
    governance = snapshot["metadata"]["governance"]
    assert governance["with_actions_stays_above_all_minima"] is not None


def test_refuses_without_an_attested_stress_run(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session)
    run_id = _run_enterprise_stress(db_session, scenario_id)

    # No sign-off yet → refuse.
    with pytest.raises(HTTPException) as no_signoff:
        _generate(db_session)
    assert no_signoff.value.status_code == 409
    assert no_signoff.value.detail["error_code"] == "no_attested_stress_run"  # type: ignore[index]

    # A prepared-but-not-attested sign-off is still not enough.
    signoff = enterprise_stress_signoff.create_signoff(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        StressSignoffCreate(
            run_id=run_id,
            scenario_narrative="n",
            assumptions_rationale="r",
            reason="prepare",
        ),
    )
    enterprise_stress_signoff.submit_signoff(
        db_session, MAKER, SAMPLE_BANK_ID, signoff.id, StressSignoffTransition(reason="submit")
    )
    with pytest.raises(HTTPException) as pending:
        _generate(db_session)
    assert pending.value.detail["error_code"] == "no_attested_stress_run"  # type: ignore[index]


def test_exports_to_xlsx_and_pdf(db_session: Session, storage: InMemoryStorageClient) -> None:
    package = _prepare(db_session)

    xlsx = export_package(db_session, MAKER, package, "xlsx")
    assert xlsx.size_bytes > 0 and xlsx.checksum_sha256
    pdf = export_package(db_session, MAKER, package, "pdf")
    assert pdf.size_bytes > 0 and pdf.checksum_sha256

    slug = db_session.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    payload = None
    for obj in storage.list(slug, "outputs"):
        if obj.location.object_path == xlsx.object_path:
            _, stream = storage.read(obj.location)
            payload = stream.read()
    assert payload is not None
    workbook = load_workbook(io.BytesIO(payload))
    # One sheet per Appendix II table plus metadata / provenance.
    assert "Return Metadata" in workbook.sheetnames
    assert any("Table 1" in name for name in workbook.sheetnames)
    assert any("Table 6" in name for name in workbook.sheetnames)


def test_maker_cannot_attest_own_signoff(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session)
    run_id = _run_enterprise_stress(db_session, scenario_id)
    signoff = enterprise_stress_signoff.create_signoff(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        StressSignoffCreate(
            run_id=run_id,
            scenario_narrative="n",
            assumptions_rationale="r",
            reason="prepare",
        ),
    )
    enterprise_stress_signoff.submit_signoff(
        db_session, MAKER, SAMPLE_BANK_ID, signoff.id, StressSignoffTransition(reason="submit")
    )
    # Maker ≠ checker (¶16, ¶20): the preparer/submitter cannot attest.
    with pytest.raises(HTTPException) as maker_exc:
        enterprise_stress_signoff.attest_signoff(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            signoff.id,
            StressSignoffAttestation(credibility_rationale="c", reason="self-attest"),
        )
    assert maker_exc.value.detail["error_code"] == "maker_is_checker"  # type: ignore[index]

    # A second run also cannot mint a duplicate sign-off.
    with pytest.raises(HTTPException) as dup:
        enterprise_stress_signoff.create_signoff(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            StressSignoffCreate(
                run_id=run_id,
                scenario_narrative="n2",
                assumptions_rationale="r2",
                reason="dup",
            ),
        )
    assert dup.value.detail["error_code"] == "signoff_exists"  # type: ignore[index]


# --- ICAAP P0 (2026-09-19): Table 5 Pillar 2, governed minima, narrative, labels ----


def _stored_bytes(db: Session, storage: InMemoryStorageClient, object_path: str) -> bytes:
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    for obj in storage.list(slug, "outputs"):
        if obj.location.object_path == object_path:
            _, stream = storage.read(obj.location)
            return stream.read()
    raise AssertionError(f"no stored object at {object_path}")


def _pdf_text(payload: bytes) -> str:
    with pdfplumber.open(io.BytesIO(payload)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    # Wrapped lines rejoin with a space so a sentence can be matched whole.
    return " ".join(text.split())


def _findings(snapshot: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in snapshot["metadata"].get("generation_findings", [])
        if entry.get("rule") == rule
    ]


PILLAR2_FIELDS = (
    "pillar2_credit_concentration",
    "pillar2_irrbb",
    "pillar2_sovereign",
    "pillar2_country_and_fx",
    "pillar2_reputational",
    "pillar2_others",
)


def test_table5_carries_every_bog_pillar2_row_and_never_a_zero_for_an_unassessed_risk(
    db_session: Session,
) -> None:
    package = _prepare(db_session)
    snapshot = package.snapshot
    pillar2 = _section(snapshot, "t5_pillar2")
    assert pillar2 is not None
    rows = {row["code"]: row for row in pillar2["rows"]}
    assert list(rows) == [
        "current",
        "base_y1",
        "base_y2",
        "base_y3",
        "stress_y1",
        "stress_y2",
        "stress_y3",
    ]
    for code, row in rows.items():
        # Every BoG Table 5 Pillar 2 row is present as a field on every column.
        assert set(PILLAR2_FIELDS) <= set(row), code
        values = [row[field] for field in PILLAR2_FIELDS]
        if code == "current" or code.startswith("base_y"):
            # B3: the Current and Base columns carry no Pillar 2 assessment yet —
            # "not modelled", never the 0.000 a summed-over-nothing total printed.
            assert values == [None] * 6, code
            assert row["pillar2_total"] is None
            assert row["pillar2_coverage"] == "Not modelled"
        else:
            modelled = [Decimal(value) for value in values if value is not None]
            if modelled:
                assert Decimal(row["pillar2_total"]) == sum(modelled, Decimal("0"))
                if len(modelled) < len(values):
                    assert row["pillar2_coverage"].startswith("Partial — excludes ")
            else:
                assert row["pillar2_total"] is None
    rwa_rows = {row["code"]: row for row in _section(snapshot, "t5_rwa")["rows"]}  # type: ignore[index]
    assert rwa_rows["current"]["pillar2_total"] is None
    assert rwa_rows["base_y1"]["pillar2_total"] is None
    # The understatement is declared, not hidden.
    (warning,) = _findings(snapshot, "appendix2_pillar2_coverage")
    assert warning["severity"] == "WARNING"
    assert "Current (as-of)" in warning["detail"]


def test_table5_prints_not_modelled_and_the_bog_pillar2_labels(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    package = _prepare(db_session)
    pdf = export_package(db_session, MAKER, package, "pdf")
    text = _pdf_text(_stored_bytes(db_session, storage, pdf.object_path))
    assert "Table 5 — Pillar 2 Capital Requirements by Risk" in text
    for label in (
        "Credit Concentration",
        "IRRBB",
        "Sovereign",
        "Country and FX",
        "Reputational",
        "Others",
        "Total Pillar 2 Capital Requirements",
    ):
        assert label in text, label
    assert "Not modelled" in text

    xlsx = export_package(db_session, MAKER, package, "xlsx")
    workbook = load_workbook(io.BytesIO(_stored_bytes(db_session, storage, xlsx.object_path)))
    sheet = next(workbook[name] for name in workbook.sheetnames if "Pillar 2" in name)
    values = [cell.value for row in sheet.iter_rows() for cell in row]
    assert "Not modelled" in values


def test_the_car_minimum_is_stamped_with_its_governed_provenance(db_session: Session) -> None:
    package = _prepare(db_session)
    metadata = package.snapshot["metadata"]
    provenance = {entry["param_code"]: entry for entry in metadata["parameter_provenance"]}
    car = provenance["car_min"]
    assert Decimal(car["applied_value"]) == Decimal(metadata["car_target_pct"])
    assert Decimal(car["applied_value"]) == Decimal("13")
    assert car["basis"] == "governed"
    governed = car["governed"]
    assert governed["param_code"] == "car_min"
    assert Decimal(governed["value"]) == Decimal("13")
    assert governed["scope_type"] == "institution_class"
    assert governed["scope_key"] == "bank"
    assert "¶71" in governed["source_citation"]
    assert governed["confirmation_status"] == "confirmed"
    assert governed["effective_from"]
    assert "paid_up_min" in provenance

    rendered = build_rendered_return(
        get_template("bog-icaap-stress-appendix2-v1"),  # type: ignore[arg-type]
        package.snapshot,
        package.source_runs,
        package_id=str(package.id),
        package_version=package.version,
    )
    car_note = next(
        note
        for note in rendered.report_notes
        if note.startswith("Minimum total capital ratio applied")
    )
    assert car_note.startswith("Minimum total capital ratio applied: 13%.")
    assert "Governed parameter car_min = 13%" in car_note
    # The seed row's date is the platform record's, never presented as the
    # instrument's commencement date (DV-005), and the scope is in words.
    assert "platform parameter record dated" in car_note
    assert "not a regulatory commencement date" in car_note
    assert "applies to banks, GH" in car_note
    assert "effective from" not in car_note
    assert "institution_class" not in car_note


def test_an_internal_target_above_the_floor_is_declared_stricter(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session)
    run_id = _run_enterprise_stress(db_session, scenario_id, car_target_pct=Decimal("15"))
    _attested_signoff(db_session, run_id)
    package = _generate(db_session)
    car = next(
        entry
        for entry in package.snapshot["metadata"]["parameter_provenance"]
        if entry["param_code"] == "car_min"
    )
    assert Decimal(car["applied_value"]) == Decimal("15")
    assert car["basis"] == "stricter_than_governed"
    assert Decimal(car["governed"]["value"]) == Decimal("13")


BOARD_CHAIR_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BOARD_CHAIR = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=BOARD_CHAIR_ID, roles=("approver",)
)


def _seed_board_chair(db: Session) -> None:
    if db.get(User, BOARD_CHAIR_ID) is not None:
        return
    db.add(
        User(
            id=BOARD_CHAIR_ID,
            organization_id=DEMO_ORG_ID,
            email="board-chair@aequoros.example",
            display_name="Ama Mensah",
            job_title="Board Chair",
            role="approver",
        )
    )
    db.commit()


def test_attested_narratives_render_in_the_pdf(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    materialize_canonical_test_book(db_session)
    _seed_board_chair(db_session)
    scenario_id = _approved_scenario(db_session)
    run_id = _run_enterprise_stress(db_session, scenario_id)
    _attested_signoff(db_session, run_id, checker=BOARD_CHAIR)
    package = _generate(db_session)
    narrative = _section(package.snapshot, "stress_narrative")
    assert narrative is not None
    assert [row["code"] for row in narrative["rows"]] == [
        "scenario_narrative",
        "assumptions_rationale",
        "methodology_summary",
        "board_challenge",
        "credibility_rationale",
        "attested_by",
    ]
    attested = next(row for row in narrative["rows"] if row["code"] == "attested_by")
    # Name and designation, not the user id (audit m13).
    assert attested["value"].startswith("Ama Mensah, Board Chair (attested ")
    assert str(BOARD_CHAIR_ID) not in attested["value"]

    pdf = export_package(db_session, MAKER, package, "pdf")
    text = _pdf_text(_stored_bytes(db_session, storage, pdf.object_path))
    assert "Stress Test Narrative (Board-attested)" in text
    for statement in (
        SCENARIO_NARRATIVE,
        ASSUMPTIONS_RATIONALE,
        METHODOLOGY_SUMMARY,
        BOARD_CHALLENGE,
        CREDIBILITY_RATIONALE,
    ):
        assert statement in text, statement
    assert "Ama Mensah, Board Chair" in text
    assert _findings(package.snapshot, "appendix2_narrative_completeness") == []


def test_a_changed_narrative_changes_the_content_digest(db_session: Session) -> None:
    package = _prepare(db_session)
    original = digests.content_digest(package.snapshot)
    altered = copy.deepcopy(package.snapshot)
    narrative = next(s for s in altered["sections"] if s["code"] == "stress_narrative")
    narrative["rows"][0]["value"] = "A different scenario narrative."
    assert digests.content_digest(altered) != original


def test_the_narrative_pdf_is_byte_deterministic(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    package = _prepare(db_session)
    first = export_package(db_session, MAKER, package, "pdf")
    first_bytes = _stored_bytes(db_session, storage, first.object_path)
    second = export_package(db_session, MAKER, package, "pdf")
    assert second.checksum_sha256 == first.checksum_sha256
    assert _stored_bytes(db_session, storage, second.object_path) == first_bytes


def test_an_unrecorded_narrative_element_prints_not_stated_and_warns(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session)
    run_id = _run_enterprise_stress(db_session, scenario_id)
    _attested_signoff(db_session, run_id, methodology_summary=None, board_challenge=None)
    package = _generate(db_session)
    rows = {
        row["code"]: row
        for row in _section(package.snapshot, "stress_narrative")["rows"]  # type: ignore[index]
    }
    assert rows["methodology_summary"]["value"] is None
    assert rows["board_challenge"]["value"] is None
    (warning,) = _findings(package.snapshot, "appendix2_narrative_completeness")
    assert warning["severity"] == "WARNING"
    assert "Methodology summary" in warning["detail"]
    assert "Board challenge" in warning["detail"]

    pdf = export_package(db_session, MAKER, package, "pdf")
    text = _pdf_text(_stored_bytes(db_session, storage, pdf.object_path))
    assert text.count("Not stated") >= 2


def test_narrative_markup_and_formula_text_are_inert_in_every_artifact(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """User-typed narrative can never inject reportlab markup into the PDF or a
    formula into the workbook / CSV."""
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session)
    run_id = _run_enterprise_stress(db_session, scenario_id)
    _attested_signoff(
        db_session,
        run_id,
        scenario_narrative='=HYPERLINK("http://example.invalid","x") <b>not bold</b> & <font',
        board_challenge="Line one.\nLine two <unclosed",
    )
    package = _generate(db_session)

    pdf = export_package(db_session, MAKER, package, "pdf")
    text = _pdf_text(_stored_bytes(db_session, storage, pdf.object_path))
    assert "<b>not bold</b> & <font" in text
    assert "Line two <unclosed" in text

    xlsx = export_package(db_session, MAKER, package, "xlsx")
    workbook = load_workbook(io.BytesIO(_stored_bytes(db_session, storage, xlsx.object_path)))
    sheet = next(workbook[name] for name in workbook.sheetnames if "Narrative" in name)
    injected = [
        cell
        for row in sheet.iter_rows()
        for cell in row
        if isinstance(cell.value, str) and cell.value.startswith("=HYPERLINK")
    ]
    assert injected, "the narrative cell is present"
    assert all(cell.data_type == "s" for cell in injected)

    csv = export_package(db_session, MAKER, package, "csv")
    with zipfile.ZipFile(io.BytesIO(_stored_bytes(db_session, storage, csv.object_path))) as zf:
        entry = next(name for name in zf.namelist() if name.endswith("stress_narrative.csv"))
        content = zf.read(entry).decode("utf-8")
    assert "'=HYPERLINK" in content


def test_appendix_ii_prints_the_directives_labels_not_engine_keys(db_session: Session) -> None:
    package = _prepare(db_session)
    impact = _section(package.snapshot, "t1_impact_of_adverse")
    assert impact is not None
    descriptions = {row["description"] for row in impact["rows"]}
    assert descriptions <= set(APPENDIX2_EXPOSURE_CLASS_LABELS.values())
    assert "GOG" not in descriptions and "RETAIL SME" not in descriptions
    drivers = {
        row["description"]
        for row in _section(package.snapshot, "t6_risk_drivers")["rows"]  # type: ignore[index]
    }
    assert "FX rates (USD to GH Cedi)" in drivers
    assert "Average yield on Government of Ghana securities" in drivers
    assert "FX USD GHS" not in drivers


def test_a_pre_p0_snapshot_still_renders_without_the_new_blocks(db_session: Session) -> None:
    """Existing packages stay untouched: a snapshot generated before the new
    sections / metadata existed renders with no 409 and without them."""
    package = _prepare(db_session)
    legacy = copy.deepcopy(package.snapshot)
    legacy["sections"] = [
        section
        for section in legacy["sections"]
        if section["code"] not in {"t5_pillar2", "stress_narrative"}
    ]
    for key in ("parameter_provenance", "generation_findings", "report_notes"):
        legacy["metadata"].pop(key, None)
    rendered = build_rendered_return(
        get_template("bog-icaap-stress-appendix2-v1"),  # type: ignore[arg-type]
        legacy,
        package.source_runs,
        package_id=str(package.id),
        package_version=package.version,
    )
    codes = {section.layout.section_code for section in rendered.sections}
    assert "t5_pillar2" not in codes and "stress_narrative" not in codes
    assert "t5_rwa" in codes
    assert rendered.report_notes == ()


def test_no_appendix_ii_header_or_citation_hard_codes_the_car_floor() -> None:
    for template_id in ("bog-icaap-stress-appendix2-v1", "bog-sdi-stress-annual-v1"):
        template = get_template(template_id)
        assert template is not None
        for layout in template.sections:
            assert "13%" not in layout.source_citation, (template_id, layout.section_code)
            for column in layout.columns:
                assert "13%" not in column.header, (template_id, column.header)
    capital = get_template("bog-bsd2-capital-v1")
    assert capital is not None
    for layout in capital.sections:
        assert "13%" not in layout.source_citation


def test_the_67g_granularity_is_not_claimed() -> None:
    """¶67(g) may only be cited as NOT provided (audit §4.8 / M23)."""
    definition = REGISTRY["ICAAP-STRESS-APPENDIX2"]
    assert "¶67(g)" in definition.directive_citation
    assert "not provided" in definition.directive_citation
    template = get_template(definition.template_id)
    assert template is not None
    for layout in template.sections:
        if "¶67(g)" in layout.source_citation:
            assert "not provided" in layout.source_citation, layout.section_code


def test_the_sdi_packet_carries_the_narrative_and_its_own_governed_floor(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    db_session.flush()
    _seed_checker(db_session)
    scenario_id = _approved_scenario(db_session, code="sdi_adverse_2027")
    run_id = _run_enterprise_stress(db_session, scenario_id)
    _attested_signoff(db_session, run_id)
    package = _generate_sdi(db_session)

    assert _section(package.snapshot, "stress_narrative") is not None
    car = next(
        entry
        for entry in package.snapshot["metadata"]["parameter_provenance"]
        if entry["param_code"] == "car_min"
    )
    # The SDI floor is its own (Act 930 s.29), never the bank 13%.
    assert Decimal(car["applied_value"]) == Decimal("10")
    assert car["governed"]["scope_key"] == "sdi"
    pdf = export_package(db_session, MAKER, package, "pdf")
    text = _pdf_text(_stored_bytes(db_session, storage, pdf.object_path))
    assert "Minimum total capital ratio applied: 10%" in text
    assert SCENARIO_NARRATIVE in text
    assert "13% CAR" not in text
