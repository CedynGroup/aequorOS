"""Independent QA of ICAAP P0 items 7 and 8 — the IRR note and the ¶67(g) wording.

Agent 11 (Test/QA), 2026-09-19. A printed note is a claim about the engine;
each claim is checked here against the engine's actual behaviour (pure
``app.domain.irr.engine`` on a synthetic book), not against the note's own
wording.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from app.domain.irr.engine import (
    IRR_BUCKETS,
    IRR_OPTIONAL_SCENARIO_CODES,
    IRR_SCENARIO_CODES,
    IrrPosition,
    run_irr_scenarios,
)
from app.domain.stress import appendix_ii
from app.domain.stress.management_actions import RecognitionCaps
from app.domain.stress.projection import EnterpriseProjectionInputs, project_enterprise
from app.services.regulatory_reporting.registry import REGISTRY
from app.services.regulatory_reporting.templates import TEMPLATES, get_template
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    bog_forecast_params,
    sample_bank_latest_facts,
    severe_paths,
)

CURVE = {midpoint: Decimal("20") for _name, midpoint in IRR_BUCKETS}
BASEL = {
    "parallel_up_200": {"parallel_bp": Decimal("200")},
    "parallel_down_200": {"parallel_bp": Decimal("-200")},
    "short_up_250": {"short_bp": Decimal("250"), "decay_years": Decimal("3")},
    "short_down_250": {"short_bp": Decimal("-250"), "decay_years": Decimal("3")},
    "steepener": {"short_bp": Decimal("-65"), "long_bp": Decimal("90")},
    "flattener": {"short_bp": Decimal("80"), "long_bp": Decimal("-60")},
}
GHS_450 = {
    "parallel_up_450": {"parallel_bp": Decimal("450")},
    "parallel_down_450": {"parallel_bp": Decimal("-450")},
}


def _book(asset_heavy: bool) -> list[IrrPosition]:
    """A long-dated book: assets 5y+, liabilities overnight (or the reverse)."""
    long_side, short_side = ("asset", "liability") if asset_heavy else ("liability", "asset")
    return [
        IrrPosition(
            long_side, "5y+", Decimal("1000"), Decimal("20"), "fixed", Decimal("7.0"), "qa"
        ),
        IrrPosition(
            short_side, "overnight", Decimal("900"), Decimal("20"), "float", Decimal("0.003"), "qa"
        ),
    ]


def _severe_projection():  # noqa: ANN202 - the projection dataclass, unchanged
    """The severe enterprise projection the Appendix II domain tests run on."""
    return project_enterprise(
        EnterpriseProjectionInputs(  # type: ignore[arg-type]
            scenario_code="SEVERE-2027",
            scenario_paths=severe_paths(),
            facts=sample_bank_latest_facts(),
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            horizon_years=3,
        )
    )


def _notes(template_id: str, section_code: str) -> str:
    template = get_template(template_id)
    assert template is not None
    layout = next(s for s in template.sections if s.section_code == section_code)
    return " ".join(layout.notes)


def test_450_rows_are_computed_only_with_the_calibration_and_never_breach() -> None:
    without = run_irr_scenarios(_book(True), CURVE, BASEL, Decimal("10"), Decimal("15"))
    assert {s.scenario_code for s in without.scenarios} == set(IRR_SCENARIO_CODES)
    with_cal = run_irr_scenarios(
        _book(True), CURVE, {**BASEL, **GHS_450}, Decimal("10"), Decimal("15")
    )
    codes = {s.scenario_code for s in with_cal.scenarios}
    assert set(IRR_OPTIONAL_SCENARIO_CODES) <= codes
    for scenario in with_cal.scenarios:
        if scenario.scenario_code in IRR_OPTIONAL_SCENARIO_CODES:
            # Tier 1 of 10 against a 1,000 long book: the ±450 ΔEVE is far past
            # 15% of Tier 1, yet the row is informational — never a breach.
            assert abs(scenario.delta_eve_pct_tier1) > Decimal("15")
            assert scenario.breach is False
    # The outlier verdict is taken over the Basel six only.
    assert with_cal.worst_scenario_code in IRR_SCENARIO_CODES


def test_the_outlier_column_flags_an_eve_gain_as_the_note_says() -> None:
    """Liability-heavy long book: rates up = EVE GAIN. The note says an EVE gain
    can flag; the engine must behave that way for the note to be true."""
    result = run_irr_scenarios(_book(False), CURVE, BASEL, Decimal("10"), Decimal("15"))
    up = next(s for s in result.scenarios if s.scenario_code == "parallel_up_200")
    assert up.delta_eve > 0 and up.breach is True


def test_irrbb_notes_make_no_claim_the_engine_cannot_back() -> None:
    eve = _notes("bog-irrbb-pilot-v1", "eve_scenarios")
    ear = _notes("bog-irrbb-pilot-v1", "earnings_at_risk")
    # Stale / over-claiming phrases from before P0.
    for phrase in (
        "documented gap",
        "fixed Basel scenario set today",
        "engine-side",
        "pending",
        "never computed",
    ):
        assert phrase not in eve and phrase not in ear, phrase
    # Every scenario the note says is "always computed" is in the mandatory set.
    assert "six Basel scenarios" in eve and len(IRR_SCENARIO_CODES) == 6
    # The note must not claim the short 500 / long 300 shocks are computed.
    assert "short 500 / long 300 shocks are not computed" in eve
    assert not any(re.search(r"(500|300)", code) for code in IRR_SCENARIO_CODES)
    assert not any(re.search(r"(500|300)", code) for code in IRR_OPTIONAL_SCENARIO_CODES)
    # It must not claim the Guideline's loss-only / ±450 outlier test is applied.
    assert "is not yet what this column shows" in eve
    # Bucket count it prints is the engine's.
    assert f"{len(IRR_BUCKETS)} repricing buckets" in eve and len(IRR_BUCKETS) == 9
    # EaR ±450 rows exist in the service only behind the calibration.
    assert "computed and shown when the active parameter set carries" in ear


def test_the_67g_granularity_is_cited_only_as_not_provided_anywhere() -> None:
    offenders: list[str] = []
    for code, definition in REGISTRY.items():
        if "67(g)" in definition.directive_citation and "not provided" not in (
            definition.directive_citation
        ):
            offenders.append(f"registry:{code}")
    for template in TEMPLATES.values():
        texts = [template.source_citation if hasattr(template, "source_citation") else ""]
        texts += list(getattr(template, "notes", ()))
        for layout in template.sections:
            texts += [layout.source_citation, *layout.notes]
        for text in texts:
            if "67(g)" in text and "not provided" not in text:
                offenders.append(f"{template.template_id}:{text[:60]}")
    assert offenders == []


def test_the_allocation_the_citation_describes_is_what_the_engine_does() -> None:
    """The corrected citation says losses are 'allocated by credit-RWA share
    where exposure-level data is absent'. That is a claim made to the REGULATOR
    about how a filed number was produced, so it is checked against the numbers.

    Until 2026-09-20 the second half of this test read
    ``app/domain/stress/appendix_ii.py`` and asserted that the string
    "Allocate the adverse impairment across CRD classes by credit-RWA share"
    appeared in it — which it does, as the FIRST LINE OF A DOCSTRING. Replacing
    the allocator's body with zeros left the test green: the claim the registry
    makes to the supervisor was pinned by a comment.
    """
    citation = REGISTRY["ICAAP-STRESS-APPENDIX2"].directive_citation
    assert "allocated by credit-RWA share" in citation

    projection = _severe_projection()
    tables = appendix_ii.build_appendix_ii(
        projection,
        severe_paths(),
        currency="GHS",
        car_target_pct=Decimal("13"),
        recognition_caps=RecognitionCaps(
            at1_pct_rwa=Decimal("1.5"), tier2_pct_rwa=Decimal("2")
        ),
        paid_up_min=Decimal("400000000"),
    )
    impact = dict(tables.table1_summary.impact_of_adverse)
    assert impact, "no adverse-impact rows at all, so nothing is allocated"

    base_by_year = {year.year: year for year in projection.base}
    allocated_something = False
    for stress_year in projection.stress:
        rows = impact[stress_year.year]
        assert [row.exposure_class for row in rows] == list(
            appendix_ii.CRD_EXPOSURE_CLASSES
        ), "every CRD class is a line, present or nil"

        # 1. The total allocated IS the adverse impairment, to the last unit.
        adverse = max(
            stress_year.pnl.credit_losses - base_by_year[stress_year.year].pnl.credit_losses,
            Decimal(0),
        )
        assert sum((row.loss for row in rows), Decimal(0)) == appendix_ii.thousands(adverse)

        # 2. Each class's share IS its share of the stressed CREDIT RWA — the
        #    claim in the citation. Checked on the largest non-residual class,
        #    which is the one a wrong allocator would move most.
        weights: dict[str, Decimal] = dict.fromkeys(appendix_ii.CRD_EXPOSURE_CLASSES, Decimal(0))
        total_credit_rwa = Decimal(0)
        for line in stress_year.rwa.line_items:
            if line.section != "credit_rwa" or line.weighted_amount <= 0:
                continue
            weights[appendix_ii._crd_class(line)] += line.weighted_amount  # noqa: SLF001
            total_credit_rwa += line.weighted_amount
        if adverse <= 0 or total_credit_rwa <= 0:
            continue
        allocated_something = True
        residual = list(appendix_ii.CRD_EXPOSURE_CLASSES)[-1]
        by_class = {row.exposure_class: row.loss for row in rows}
        for name, weight in weights.items():
            if name == residual:
                continue
            expected = appendix_ii.thousands(
                (adverse * weight / total_credit_rwa).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            )
            assert by_class[name] == expected, (
                f"{stress_year.year} {name}: the filed loss is not this class's "
                f"share of the stressed credit RWA"
            )
        # 3. A class carrying credit RWA is not silently nil.
        biggest = max(
            (name for name in weights if name != residual), key=lambda name: weights[name]
        )
        assert weights[biggest] > 0
        assert by_class[biggest] > 0, "the largest credit-RWA class was allocated nothing"

    assert allocated_something, (
        "the severe path produced no adverse impairment, so this test proved nothing"
    )
