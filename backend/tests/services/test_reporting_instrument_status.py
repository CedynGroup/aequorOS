"""BoG audit R7/R8: only effective instruments create filing obligations.

Basis: BoG LMTD (Exposure Draft, February 2026) Part I ¶8–9,
Part II ¶7; BoG Large Exposures Directive (Sept 2025) ¶12, ¶57–58
(effective 1 Jan 2027); BoG LCR Directive, 2026 (referenced, LMTD ¶4;
unpublished) [confirm]. NSFR has no published BoG instrument.
"""

from collections import Counter
from dataclasses import replace
from datetime import date
from typing import TypedDict, cast

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.domain.authority.registry import REGISTRY as AUTHORITY_REGISTRY
from app.domain.bi.catalogue import catalogue
from app.domain.regulatory_instruments import InstrumentStatus, instrument_status_on
from app.models import Bank, RegulatoryParameter
from app.services.regulatory_reporting import calendar, generation, packages
from app.services.regulatory_reporting.eligibility import resolve_eligibility
from app.services.regulatory_reporting.provenance import build_template_provenance
from app.services.regulatory_reporting.registry import REGISTRY
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.authorization import grant_institution_authority

pytestmark = pytest.mark.usefixtures("return_generation_authority")
CTX = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)


@pytest.mark.parametrize("as_of", [date(2026, 10, 9), date(2027, 4, 10)])
def test_draft_and_unpublished_returns_never_create_obligations(
    db_session: Session, as_of: date
) -> None:
    """BoG LMTD (Exposure Draft, February 2026) Part I ¶8 is conditional."""
    materialize_canonical_test_book(db_session)
    result = calendar.list_obligations(db_session, CTX, SAMPLE_BANK_ID, 3, as_of=as_of)
    assert not set(BANK_DRAFTS + ("LMT", "LCR-NSFR")) & {
        item.return_code for item in result.obligations
    }
    assert result.coverage_note is not None
    assert "LMT" in result.coverage_note
    assert "LCR-NSFR" in result.coverage_note


def test_final_return_obligations_start_on_commencement(db_session: Session) -> None:
    """BoG Large Exposures Directive (Sept 2025) ¶12: effective 1 Jan 2027."""
    materialize_canonical_test_book(db_session)
    result = calendar.list_obligations(db_session, CTX, SAMPLE_BANK_ID, 6, as_of=date(2026, 10, 9))
    rows = [item for item in result.obligations if item.return_code == "LE-MONTHLY"]
    assert rows
    assert all(item.reporting_date >= date(2027, 1, 1) for item in rows)
    assert all(item.effective_from == date(2027, 1, 1) for item in rows)
    assert all(item.instrument_status == "in_force" for item in rows)


@pytest.mark.parametrize("code", ["LMT", "LCR-NSFR", "LE-MONTHLY"])
def test_preparation_anchors_have_no_overdue_grade_or_deadline(
    db_session: Session, code: str
) -> None:
    """Act 930 s.93(3) (in force): no penalty estimate for a duty not in force."""
    materialize_canonical_test_book(db_session)
    result = calendar.list_return_anchors(
        db_session, CTX, SAMPLE_BANK_ID, code, 1, as_of=date(2026, 10, 9)
    )
    assert result.anchors
    assert all(not item.in_force for item in result.anchors)
    assert all(item.rag is None and item.due_date is None for item in result.anchors)


def test_return_templates_carry_status_and_conditional_commencement(db_session: Session) -> None:
    """BoG LMTD (Exposure Draft, February 2026) Part I ¶8; LED ¶12."""
    templates = {
        item.code: item
        for item in packages.list_return_templates(
            db_session, CTX, as_of=date(2026, 10, 9)
        ).templates
    }
    assert templates["LMT"].instrument_status == "exposure_draft"
    assert templates["LMT"].effective_from == date(2027, 1, 1)
    assert templates["SDI-LMT-MONTHLY"].instrument_status == "exposure_draft"
    assert templates["SDI-LMT-MONTHLY"].effective_from == date(2027, 1, 1)
    assert templates["LE-MONTHLY"].instrument_status == "final_not_in_force"
    assert templates["SDI-LE-MONTHLY"].effective_from == date(2027, 1, 1)
    assert templates["LCR-NSFR"].instrument_status == "unpublished"
    assert templates["LCR-NSFR"].effective_from is None


def test_lcr_nsfr_authority_is_a_basel_reference() -> None:
    """BoG LCR Directive, 2026 (referenced, LMTD ¶4; unpublished) [confirm]."""
    for entry in AUTHORITY_REGISTRY.all():
        if entry.methodology_id != "basel_bog_liquidity_run":
            continue
        assert not entry.instrument_in_force
        assert entry.advisory_designation.value == "basel_reference"
        assert "as adopted by BoG" not in entry.authority_reference


@pytest.mark.parametrize("status", ["exposure_draft", "unpublished"])
def test_unfinalised_instrument_does_not_commence_by_date(status: InstrumentStatus) -> None:
    """BoG LMTD (Exposure Draft, February 2026) Part I ¶8 is not final law."""
    assert instrument_status_on(status, date(2027, 1, 1), date(2028, 1, 1)) == status


def test_sdi_draft_and_precommencement_returns_are_not_overdue(db_session: Session) -> None:
    """BoG LMTD (Exposure Draft, February 2026) Part II ¶9; LED ¶12 (2027)."""
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    db_session.flush()
    result = calendar.list_obligations(db_session, CTX, SAMPLE_BANK_ID, 1, as_of=date(2026, 10, 9))
    assert not {"SDI-LMT-MONTHLY", "SDI-LE-MONTHLY", "SDI-IRRBB-QUARTERLY", "SDI-STRESS-ANNUAL"} & {
        item.return_code for item in result.obligations
    }


def test_final_template_status_changes_on_commencement(db_session: Session) -> None:
    """BoG Large Exposures Directive (Sept 2025) ¶12 (effective 1 Jan 2027)."""
    templates = {
        item.code: item
        for item in packages.list_return_templates(
            db_session, CTX, as_of=date(2027, 1, 1)
        ).templates
    }
    assert templates["LE-MONTHLY"].instrument_status == "in_force"
    assert templates["SDI-LE-MONTHLY"].instrument_status == "in_force"
    assert templates["LMT"].instrument_status == "exposure_draft"
    assert templates["LCR-NSFR"].instrument_status == "unpublished"


def test_basel_reference_is_consumable_by_the_bi_catalogue() -> None:
    """BoG LCR Directive, 2026 (referenced, LMTD ¶4; unpublished) [confirm]."""
    measures = [
        measure
        for measure in catalogue().engine_measures()
        if measure.engine_rule is not None
        and measure.engine_rule.metric_id in ("lcr_pct", "nsfr_pct")
    ]
    assert measures
    assert all(measure.advisory_designation == "advisory_only" for measure in measures)
    assert all(not measure.certified for measure in measures)


BANK_DRAFTS = (
    "IRRBB-PILOT",
    "ICAAP-REPORT",
    "ICAAP-UPDATE",
    "ICAAP-DISCLOSURE",
    "ICAAP-STRESS",
    "ICAAP-STRESS-APPENDIX2",
    "LAS-QUARTERLY",
    "STRESS-PACK",
)
ALL_DRAFTS = BANK_DRAFTS + ("SDI-IRRBB-QUARTERLY", "SDI-STRESS-ANNUAL")


@pytest.mark.parametrize("as_of", [date(2026, 10, 9), date(2028, 4, 10)])
def test_all_draft_template_disclosures_remain_drafts(db_session: Session, as_of: date) -> None:
    templates = {
        item.code: item
        for item in packages.list_return_templates(db_session, CTX, as_of=as_of).templates
    }
    assert all(templates[code].instrument_status == "exposure_draft" for code in ALL_DRAFTS)


def _capital_authority(db: Session) -> None:
    grant_institution_authority(
        db,
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        user_id=DEMO_USER_ID,
        bundle=RoleBundle.ANALYST,
        module=ModuleScope.CAPITAL,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )


@pytest.mark.parametrize("code", BANK_DRAFTS + ("SDI-IRRBB-QUARTERLY", "SDI-STRESS-ANNUAL"))
def test_all_draft_anchors_preserve_preparation_without_filing_duties(
    db_session: Session, code: str
) -> None:
    materialize_canonical_test_book(db_session)
    _capital_authority(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    if code.startswith("SDI-"):
        bank.institution_type = "savings_and_loans"
        db_session.flush()
    result = calendar.list_return_anchors(
        db_session, CTX, SAMPLE_BANK_ID, code, 3, as_of=date(2028, 4, 10)
    )
    assert result.instrument_status == "exposure_draft"
    if not REGISTRY[code].event_driven:
        assert result.anchors
    assert all(
        not item.in_force and item.rag is None and item.due_date is None for item in result.anchors
    )


class InstrumentDisclosure(TypedDict):
    instrument_status: InstrumentStatus
    instrument_effective_from: str | None
    instrument_label: str


class FilingTiming(TypedDict):
    effective_from: str | None
    pre_effective: bool


class FilingMetadata(TypedDict):
    filing: FilingTiming


class FrozenDisclosure(TypedDict):
    provenance: InstrumentDisclosure
    metadata: FilingMetadata


def test_governed_commencement_reaches_templates_and_sealed_frozen_provenance(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    _capital_authority(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    first_as_of = date(2028, 12, 31)
    rows = list(
        db_session.scalars(
            select(RegulatoryParameter).where(
                RegulatoryParameter.param_code == "icaap_report_first_as_of_date"
            )
        )
    )
    assert rows
    for row in rows:
        row.value_json = {"schema": "icaap-effective-date-v1", "date": first_as_of.isoformat()}
    db_session.flush()
    templates = {
        item.code: item
        for item in packages.list_return_templates(
            db_session, CTX, bank.id, as_of=date(2026, 10, 9)
        ).templates
    }
    for code in ("ICAAP-REPORT", "ICAAP-UPDATE", "ICAAP-DISCLOSURE"):
        assert templates[code].instrument_status == "exposure_draft"
        assert templates[code].effective_from == first_as_of
        frozen = generation.generate_frozen_package(
            db_session,
            CTX,
            bank,
            return_code=code,
            reporting_date=date(2025, 12, 31),
            build=lambda: generation.FrozenSnapshot(snapshot={"sections": []}, source_runs=[]),
            is_rehearsal=True,
            commit=False,
        )
        snapshot = cast(FrozenDisclosure, frozen.snapshot)
        provenance = snapshot["provenance"]
        assert provenance["instrument_status"] == "exposure_draft"
        assert provenance["instrument_effective_from"] == first_as_of.isoformat()
        assert first_as_of.isoformat() in provenance["instrument_label"]
        assert snapshot["metadata"]["filing"]["effective_from"] == first_as_of.isoformat()
        assert snapshot["metadata"]["filing"]["pre_effective"]
        assert frozen.is_rehearsal


def test_template_provenance_uses_resolved_commencement(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    first_as_of = date(2028, 12, 31)
    resolved = resolve_eligibility(db_session, CTX, bank, as_of=date(2026, 10, 9))
    resolved = replace(
        resolved, governed_effective_dates={"icaap_report_first_as_of_date": first_as_of}
    )
    provenance = cast(
        InstrumentDisclosure,
        build_template_provenance(
            definition=REGISTRY["ICAAP-REPORT"],
            eligibility=resolved,
            bank=bank,
            effective_date=date(2025, 12, 31),
            form_code="BSD2",
            workbook="BSD2.xlsx",
            authority_counts={},
            formula_cells_evaluated=0,
        ).to_dict(),
    )
    assert provenance["instrument_status"] == "exposure_draft"
    assert provenance["instrument_effective_from"] == first_as_of.isoformat()


def test_bank_aware_templates_refuse_foreign_tenants(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    foreign = replace(CTX, organization_id="OR-1S000002")
    with pytest.raises(HTTPException) as exc:
        packages.list_return_templates(db_session, foreign, SAMPLE_BANK_ID)
    assert exc.value.status_code == 404


class ReturnCounts(TypedDict):
    rows: int
    rag: dict[str | None, int]
    penalty_ghs: int


class CalendarCounts(TypedDict):
    obligations: int
    rag: dict[str | None, int]
    affected_rows: int
    affected_overdue: int
    affected_penalty_ghs: int
    by_return: dict[str, ReturnCounts]


def test_fixture_calendar_original_initial_and_final_counters(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    as_of = date(2026, 10, 9)

    def measure() -> CalendarCounts:
        rows = calendar.list_obligations(
            db_session, CTX, SAMPLE_BANK_ID, 3, lookback_months=6, as_of=as_of
        ).obligations
        affected = {"LMT", "LE-MONTHLY", "LCR-NSFR", *BANK_DRAFTS}
        overdue = [row for row in rows if row.return_code in affected and row.rag == "overdue"]
        return {
            "obligations": len(rows),
            "rag": dict(Counter(row.rag for row in rows)),
            "affected_rows": sum(row.return_code in affected for row in rows),
            "affected_overdue": len(overdue),
            "affected_penalty_ghs": sum(
                6000 + 600 * (as_of - row.due_date).days for row in overdue
            ),
            "by_return": {
                code: {
                    "rows": len(items := [row for row in rows if row.return_code == code]),
                    "rag": dict(Counter(row.rag for row in items)),
                    "penalty_ghs": sum(
                        6000 + 600 * (as_of - row.due_date).days
                        for row in items
                        if row.rag == "overdue"
                    ),
                }
                for code in sorted(affected)
            },
        }

    final = measure()
    with monkeypatch.context() as patch:
        for code in ALL_DRAFTS:
            patch.setitem(
                REGISTRY,
                code,
                replace(REGISTRY[code], instrument_status="in_force", effective_from=None),
            )
        initial = measure()
        for code in ("LMT", "SDI-LMT-MONTHLY", "LE-MONTHLY", "SDI-LE-MONTHLY", "LCR-NSFR"):
            patch.setitem(
                REGISTRY,
                code,
                replace(REGISTRY[code], instrument_status="in_force", effective_from=None),
            )
        original = measure()
    assert original["obligations"] == 472
    assert initial["obligations"] == 443
    assert final["obligations"] < initial["obligations"]
    print({"original": original, "initial": initial, "final": final})
