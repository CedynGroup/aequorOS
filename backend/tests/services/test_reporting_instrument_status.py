"""BoG audit R7/R8: only effective instruments create filing obligations.

Basis: BoG LMTD (Exposure Draft, February 2026) Part I ¶8–9,
Part II ¶7; BoG Large Exposures Directive (Sept 2025) ¶12, ¶57–58
(effective 1 Jan 2027); BoG LCR Directive, 2026 (referenced, LMTD ¶4;
unpublished) [confirm]. NSFR has no published BoG instrument.
"""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.registry import REGISTRY as AUTHORITY_REGISTRY
from app.domain.bi.catalogue import catalogue
from app.domain.regulatory_instruments import InstrumentStatus, instrument_status_on
from app.models import Bank
from app.services.regulatory_reporting import calendar, packages
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

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
    assert not {"LMT", "LCR-NSFR"} & {item.return_code for item in result.obligations}
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


def test_return_templates_carry_status_and_conditional_commencement() -> None:
    """BoG LMTD (Exposure Draft, February 2026) Part I ¶8; LED ¶12."""
    templates = {
        item.code: item
        for item in packages.list_return_templates(as_of=date(2026, 10, 9)).templates
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
    assert not {"SDI-LMT-MONTHLY", "SDI-LE-MONTHLY"} & {
        item.return_code for item in result.obligations
    }


def test_final_template_status_changes_on_commencement() -> None:
    """BoG Large Exposures Directive (Sept 2025) ¶12 (effective 1 Jan 2027)."""
    templates = {
        item.code: item for item in packages.list_return_templates(as_of=date(2027, 1, 1)).templates
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
