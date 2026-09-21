"""The risk register and the risk appetite statement.

The register's verdict and the appetite's floor both come from the governed
control plane, so both tests change a console value and watch the answer move
without a code change (D-024 §5).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import RegulatoryParameter
from app.schemas.icaap import IcaapCycleRead
from app.schemas.icaap_risk_capital import (
    IcaapAppetiteMetricCreate,
    IcaapAppetiteMetricUpdate,
    IcaapCustomRiskCreate,
    IcaapRiskPut,
)
from app.services.icaap import appetite, risks


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


def _score(  # noqa: PLR0913 - the addressed risk plus its two scores
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    risk_key: str,
    likelihood: int,
    impact: int,
    *,
    base_rev: int | None = None,
):
    return risks.put_risk(
        db,
        access,
        cycle.id,
        risk_key,
        IcaapRiskPut(
            base_rev=base_rev,
            likelihood_score=likelihood,
            impact_score=impact,
            materiality_rationale="Concentrated book and a thin buffer.",
            reason="Score the risk.",
        ),
    )


# --- risk register ---------------------------------------------------------


def test_the_register_lists_every_framework_category_before_anything_is_stored(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    register = risks.get_register(canonical_book, access, cycle.id)
    assert register.summary.category_count == len(register.risks)
    assert all(risk.stored is False for risk in register.risks)
    assert register.summary.assessed_risk_count == 0
    assert {"credit", "irrbb", "liquidity"} <= {risk.risk_key for risk in register.risks}


def test_the_matrix_and_its_thresholds_come_from_the_control_plane(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    matrix = risks.get_register(canonical_book, access, cycle.id).matrix
    assert matrix.material_min_score == 10
    assert matrix.material_min_impact == 4
    assert len(matrix.cells) == len(matrix.likelihood_levels) * len(matrix.impact_levels)
    codes = {entry.param_code for entry in matrix.parameters}
    assert codes == set(risks.MATERIALITY_CODES)
    assert all(entry.confirmation_status == "pending" for entry in matrix.parameters)


def test_scoring_a_risk_creates_its_row_and_derives_the_verdict(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = _score(canonical_book, access, cycle, "credit", 4, 3)
    assert saved.materiality_score == 12
    assert saved.rating_key == "high"
    assert saved.verdict == "material"
    assert saved.verdict_source == "matrix"
    assert saved.row_rev == 1
    assert saved.thresholds_current is True


def test_a_risk_below_the_score_but_at_the_impact_threshold_is_material(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = _score(canonical_book, access, cycle, "operational", 2, 4)
    assert saved.materiality_score == 8
    assert saved.verdict == "material", "impact alone can make a risk material"


def test_lowering_the_threshold_in_the_console_changes_the_next_verdict(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-024 §5: staff edit the row, and the answer moves without a release."""
    first = _score(canonical_book, access, cycle, "model", 3, 3)
    assert first.verdict == "not_material", "9 is below the seeded minimum of 10"

    canonical_book.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="icaap_materiality_material_min_score",
            jurisdiction_code="GH",
            value_numeric=Decimal("9"),
            unit="score",
            source_citation="Test: the Board lowered the materiality threshold.",
            confirmation_status="pending",
            effective_from=date(2025, 1, 1),
            status="approved",
            proposed_by="test-suite",
            approved_by="test-suite",
        )
    )
    canonical_book.commit()

    register = risks.get_register(canonical_book, access, cycle.id)
    stored = next(risk for risk in register.risks if risk.risk_key == "model")
    assert stored.thresholds_current is False, "the row says it predates the change"

    second = _score(canonical_book, access, cycle, "model", 3, 3, base_rev=first.row_rev)
    assert second.verdict == "material"
    assert second.thresholds_current is True


def test_a_concurrent_edit_is_refused_with_the_current_version(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = _score(canonical_book, access, cycle, "credit", 4, 3)
    with pytest.raises(HTTPException) as caught:
        _score(canonical_book, access, cycle, "credit", 5, 5, base_rev=saved.row_rev - 1)
    body = _detail(caught)
    assert body["error_code"] == "row_rev_conflict"
    assert body["current_rev"] == saved.row_rev


def test_a_scored_risk_must_say_why_it_is_or_is_not_material(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as caught:
        risks.put_risk(
            canonical_book,
            access,
            cycle.id,
            "credit",
            IcaapRiskPut(likelihood_score=4, impact_score=3, reason="Score only."),
        )
    assert _detail(caught)["error_code"] == "rationale_required"


def test_an_override_needs_its_reason(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as caught:
        risks.put_risk(
            canonical_book,
            access,
            cycle.id,
            "credit",
            IcaapRiskPut(
                likelihood_score=4,
                impact_score=3,
                override="not_material",
                materiality_rationale="Scored, then overridden.",
                reason="Override the matrix.",
            ),
        )
    assert _detail(caught)["error_code"] == "override_reason_required"


def test_an_emerging_risk_can_only_be_added_under_an_open_category(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    created = risks.create_custom_risk(
        canonical_book,
        access,
        cycle.id,
        IcaapCustomRiskCreate(
            category_key="emerging",
            title="Third-party cloud concentration",
            reason="Identified in the annual risk workshop.",
        ),
    )
    assert created.is_custom is True
    assert created.risk_key.startswith("emerging_")

    with pytest.raises(HTTPException) as caught:
        risks.create_custom_risk(
            canonical_book,
            access,
            cycle.id,
            IcaapCustomRiskCreate(category_key="credit", title="Invented", reason="Not allowed."),
        )
    assert _detail(caught)["error_code"] == "unknown_risk_key"


# --- risk appetite ---------------------------------------------------------


def _metric(**overrides: Any) -> IcaapAppetiteMetricCreate:
    payload: dict[str, Any] = {
        "metric_key": "car",
        "label": "Total capital ratio",
        "measure_kind": "quantitative",
        "qualitative_statement": "The Board will hold capital above the regulatory minimum.",
        "direction": "floor",
        "appetite_value": Decimal("16"),
        "tolerance_value": Decimal("14.5"),
        "capacity_value": Decimal("13"),
        "value_source": "block_fact",
        "board_approved_on": date(2025, 11, 30),
        "board_approval_reference": "Board minute 2025/11",
        "reason": "Record the Board's appetite.",
    }
    payload.update(overrides)
    return IcaapAppetiteMetricCreate.model_validate(payload)


def test_the_levels_must_run_in_the_direction_of_the_metric(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = appetite.create_metric(canonical_book, access, cycle.id, _metric())
    assert saved.direction == "floor"
    assert saved.regulatory_param_code == "car_min"
    assert saved.regulatory_reference is not None
    assert saved.regulatory_reference.value == Decimal("13")


def test_a_capacity_weaker_than_the_regulatory_minimum_is_refused(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """A bank cannot set its own last line below the regulator's."""
    with pytest.raises(HTTPException) as caught:
        appetite.create_metric(
            canonical_book, access, cycle.id, _metric(capacity_value=Decimal("12.5"))
        )
    body = _detail(caught)
    assert body["error_code"] == "appetite_ordering_invalid"
    assert "capacity_weaker_than_regulatory" in body["violations"]
    assert body["reference"] == "car_min"


def test_levels_out_of_order_are_refused_with_the_violations_named(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as caught:
        appetite.create_metric(
            canonical_book,
            access,
            cycle.id,
            _metric(appetite_value=Decimal("13.5"), tolerance_value=Decimal("15")),
        )
    assert _detail(caught)["violations"]


def test_a_metric_with_no_governed_floor_says_so_rather_than_inventing_one(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-036: the Ghana LCR directive is not public, so lcr_min is unseeded."""
    saved = appetite.create_metric(
        canonical_book,
        access,
        cycle.id,
        _metric(
            metric_key="lcr",
            label="Liquidity coverage ratio",
            appetite_value=Decimal("130"),
            tolerance_value=Decimal("115"),
            capacity_value=Decimal("100"),
            qualitative_statement="The Board keeps liquidity well above the minimum.",
        ),
    )
    assert saved.regulatory_param_code == "lcr_min"
    assert saved.reference_missing is True
    assert saved.evaluation is not None
    assert saved.evaluation.regulatory_reference_absent is True


def test_tightening_the_governed_floor_invalidates_a_saved_capacity_on_read(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The verdict is re-derived on every read, not frozen at save time."""
    appetite.create_metric(canonical_book, access, cycle.id, _metric())
    canonical_book.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="car_min",
            jurisdiction_code="GH",
            value_numeric=Decimal("14"),
            unit="percent",
            source_citation="Test: the regulator raised the minimum capital ratio.",
            confirmation_status="pending",
            effective_from=date(2025, 1, 1),
            status="approved",
            proposed_by="test-suite",
            approved_by="test-suite",
        )
    )
    canonical_book.commit()
    read = appetite.get_appetite(canonical_book, access, cycle.id)
    metric = next(entry for entry in read.metrics if entry.metric_key == "car")
    assert "capacity_weaker_than_regulatory" in metric.violations


def test_a_qualitative_entry_carries_no_levels_and_no_evaluation(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = appetite.create_metric(
        canonical_book,
        access,
        cycle.id,
        _metric(
            metric_key="conduct_statement",
            label="Conduct",
            measure_kind="qualitative",
            direction=None,
            appetite_value=None,
            tolerance_value=None,
            capacity_value=None,
            value_source=None,
            qualitative_statement="No appetite for conduct failings affecting customers.",
        ),
    )
    assert saved.evaluation is None
    assert saved.appetite_value is None


def test_an_update_requires_the_version_it_was_read_at(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = appetite.create_metric(canonical_book, access, cycle.id, _metric())
    with pytest.raises(HTTPException) as caught:
        appetite.update_metric(
            canonical_book,
            access,
            cycle.id,
            saved.id,
            IcaapAppetiteMetricUpdate(
                base_rev=saved.row_rev + 1,
                appetite_value=Decimal("17"),
                reason="Raise the appetite.",
            ),
        )
    assert _detail(caught)["error_code"] == "row_rev_conflict"


def test_the_catalogue_is_published_so_the_dashboard_holds_no_metric_list(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    read = appetite.get_appetite(canonical_book, access, cycle.id)
    keys = {entry.key for entry in read.catalogue}
    assert {"car", "cet1_ratio", "lcr", "npl_ratio"} <= keys
    assert all(entry.direction in {"floor", "ceiling"} for entry in read.catalogue)


def test_the_regulatory_reference_disappears_when_the_row_does(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    saved = appetite.create_metric(canonical_book, access, cycle.id, _metric())
    assert saved.reference_missing is False
    canonical_book.execute(
        delete(RegulatoryParameter).where(RegulatoryParameter.param_code == "car_min")
    )
    canonical_book.commit()
    read = appetite.get_appetite(canonical_book, access, cycle.id)
    metric = next(entry for entry in read.metrics if entry.metric_key == "car")
    assert metric.reference_missing is True
    assert metric.regulatory_reference is None
