from app.models import DeskMethodology
from app.services import implied_rating


def test_default_rating_methodology_activates_automatically(db_session) -> None:
    methodology = implied_rating.ensure_default_methodology(db_session)

    assert methodology.methodology_code == implied_rating.METHODOLOGY_CODE
    assert methodology.status == "approved"
    assert methodology.approved_by == "system:auto"
    assert methodology.parameters["confidence_level"] == "0.90"
    assert len(methodology.parameters["grade_cutpoints"]) == 21

    parsed = implied_rating._methodology(  # noqa: SLF001 - validates persisted parameter contract
        methodology.parameters, "pit_grade_pd_anchors_pct"
    )

    assert parsed.operating_environment_matrix == ((0, 0), (0, 1))
    assert parsed.grade_pd_anchors_pct["b-"] == 6
    assert db_session.query(DeskMethodology).count() == 1