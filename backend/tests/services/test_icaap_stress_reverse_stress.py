"""ICAAP data companion — reverse-stress summary (ICAAP P0, 2026-09-19).

``ICAAP-STRESS`` now carries the latest stored reverse-stress run for its
reporting period, re-tabulated through the SAME row builder the Board/ALCO
stress pack uses, with the run bound in the package's source runs. Without a
run the section is omitted and the package says so; nothing is inferred.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import pdfplumber
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryPackage
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.regulatory_capital import CapitalScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.schemas.reverse_stress import ReverseStressRunCreate
from app.services import (
    regulatory_capital,
    regulatory_forecasting,
    regulatory_liquidity,
    reverse_stress,
)
from app.services.regulatory_reporting import generation
from app.services.regulatory_reporting.exports import export_package
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    return client


def _period_id(db: Session):  # noqa: ANN202 - a UUID column value
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


def _seed(db: Session, *, with_reverse: bool) -> None:
    materialize_canonical_test_book(db)
    period_id = _period_id(db)
    forecast = regulatory_forecasting.create_forecast_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        ForecastRunCreate(reporting_period_id=period_id, scenario_code="base"),
    )
    assert forecast.status == "succeeded", forecast
    if not with_reverse:
        return
    for scenario in ("baseline", "combined"):
        run = regulatory_liquidity.create_liquidity_run(
            db,
            MAKER,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="liquidity", reporting_period_id=period_id, scenario_code=scenario
            ),
        )
        assert run.status == "succeeded", run
    batch = regulatory_capital.run_all_capital_scenarios(
        db, MAKER, SAMPLE_BANK_ID, CapitalScenarioBatchCreate(reporting_period_id=period_id)
    )
    assert all(run.status == "succeeded" for run in batch.runs)
    reverse_stress.run_reverse_stress(
        db, MAKER, SAMPLE_BANK_ID, ReverseStressRunCreate(reporting_period_id=period_id)
    )


def _generate(db: Session, return_code: str = "ICAAP-STRESS") -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code=return_code, reporting_date=REPORTING_DATE),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _section(snapshot: dict[str, Any], code: str) -> dict[str, Any] | None:
    return next((s for s in snapshot["sections"] if s["code"] == code), None)


def _pdf_text(db: Session, storage: InMemoryStorageClient, package: RegulatoryPackage) -> str:
    artifact = export_package(db, MAKER, package, "pdf")
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    payload = next(
        storage.read(obj.location)[1].read()
        for obj in storage.list(slug, "outputs")
        if obj.location.object_path == artifact.object_path
    )
    with pdfplumber.open(io.BytesIO(payload)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    return " ".join(text.split())


def test_the_companion_carries_the_reverse_stress_frontier_and_binds_its_run(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    _seed(db_session, with_reverse=True)
    package = _generate(db_session)
    section = _section(package.snapshot, "reverse_stress")
    assert section is not None
    assert [row["code"] for row in section["rows"]] == ["liquidity_frontier", "capital_frontier"]

    # The same rows, from the same run, as the Board/ALCO stress pack prints.
    pack = _generate(db_session, "STRESS-PACK")
    frontier = _section(pack.snapshot, "reverse_stress_frontier")
    assert frontier is not None
    assert section["rows"] == frontier["rows"]

    metadata = package.snapshot["metadata"]
    run_id = metadata["reverse_stress_run_id"]
    assert run_id == pack.snapshot["metadata"]["reverse_stress_run_id"]
    bound = [entry for entry in package.source_runs if entry["module"] == "reverse_stress"]
    assert [entry["run_id"] for entry in bound] == [run_id]
    (note,) = metadata["report_notes"]
    assert note.startswith("Reverse stress test (before management actions): ")
    for row in section["rows"]:
        # Humanised: the scenario in words, the machine code kept on the row.
        assert row["description"] in note
        assert row["scenario"] and "_" not in row["scenario"]
        assert row["scenario_code"]
    assert (
        metadata["reverse_stress_narrative"]
        == pack.snapshot["metadata"]["reverse_stress_narrative"]
    )

    text = _pdf_text(db_session, storage, package)
    assert "Reverse Stress Test Summary" in text
    assert "Report note: Reverse stress test (before management actions):" in text


def test_without_a_reverse_stress_run_the_section_is_omitted_and_says_so(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    _seed(db_session, with_reverse=False)
    package = _generate(db_session)
    assert _section(package.snapshot, "reverse_stress") is None
    assert all(entry["module"] != "reverse_stress" for entry in package.source_runs)
    metadata = package.snapshot["metadata"]
    assert "reverse_stress_run_id" not in metadata
    (note,) = metadata["report_notes"]
    assert note.startswith("No reverse stress test has been run for this reporting period")
    (finding,) = metadata["generation_findings"]
    assert finding["severity"] == "INFO" and finding["rule"] == "icaap_reverse_stress"
    text = _pdf_text(db_session, storage, package)
    assert "Reverse Stress Test Summary" not in text
    assert "No reverse stress test has been run" in text
