"""The P5 seed rows: what they are, that they are seeded once, and where.

Three hazards this file exists to catch, all of them silent:

1. **A duplicate seed does not fail — it BACK-DATES the live row** and wins
   (D-053). So the P5 code set must be disjoint from every other seed set, and
   a code the Pillar 2 migration already seeded (the outlier threshold) must
   not appear here at all.
2. **The migration pins its rows rather than importing the catalogue**, so a
   later catalogue edit cannot change what a deployed revision seeded. That is
   only true while someone checks the two are equal today.
3. **A code with no registered shape is uneditable in the console** in any
   meaningful sense — the operator API would accept a malformed table. D-024's
   whole point is that staff correct these values without a code change.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.domain.irr import standardised_params as sfp
from app.domain.policy import parameter_shapes
from app.models import RegulatoryParameter
from app.services import regulatory_parameters as rp
from app.services.icaap.parameters import ICAAP_PARAM_CODES

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "202609190061_icaap_p5_irrbb_sf_and_granularity.py"
)


def _migration_module() -> Any:
    spec = importlib.util.spec_from_file_location("p5_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_framework_seeds_every_code_the_engine_requires() -> None:
    """The engine names seventeen codes; sixteen are P5's and one is shared."""
    seeded = rp.P5_PARAMETER_CODES
    required = set(sfp.REQUIRED_CODES)
    missing = required - seeded - {"irrbb_outlier_threshold_pct_tier1"}
    assert missing == set(), missing
    # The outlier threshold belongs to the Pillar 2 seed and is REUSED, never
    # re-seeded: a second row for the same code back-dates the first.
    assert "irrbb_outlier_threshold_pct_tier1" not in seeded
    assert "irrbb_outlier_threshold_pct_tier1" in rp.P2_PARAMETER_CODES


def test_the_option_uplift_is_reserved_and_not_seeded() -> None:
    """Seeding it would suggest options are priced. They are refused (DV-010)."""
    assert "irrbb_sf_option_vol_uplift_pct" not in rp.P5_PARAMETER_CODES
    assert all(
        spec.param_code != "irrbb_sf_option_vol_uplift_pct" for spec in rp.SEED_PARAMETERS
    )


def test_the_seed_sets_are_disjoint_so_no_code_is_seeded_twice() -> None:
    sets = {
        "workspace": set(ICAAP_PARAM_CODES),
        "pillar2": set(rp.P2_PARAMETER_CODES),
        "filing": set(rp.FILING_PARAMETER_CODES),
        "p5": set(rp.P5_PARAMETER_CODES),
    }
    for left, left_codes in sets.items():
        for right, right_codes in sets.items():
            if left < right:
                assert left_codes & right_codes == set(), (left, right)


def test_the_catalogue_carries_each_p5_row_exactly_once() -> None:
    keys = [
        (spec.scope_type, spec.scope_key, spec.param_code, spec.jurisdiction_code)
        for spec in rp.SEED_PARAMETERS
    ]
    assert len(keys) == len(set(keys))


def test_the_concurrently_edited_filing_rows_are_still_present() -> None:
    """The catalogue is edited by several workstreams at once.

    ``icaap_stress_severe_scenarios_min`` and the ICAAP report start date landed
    from another workstream while the P5 rows were being appended; an append
    that had restructured the file rather than added to it would have dropped
    them without a single test noticing.
    """
    codes = {spec.param_code for spec in rp.SEED_PARAMETERS}
    assert "icaap_stress_severe_scenarios_min" in codes
    assert "icaap_report_first_as_of_date" in codes


def test_every_pre_p5_seed_row_still_emits_an_identical_dict() -> None:
    """``ParamSpec`` gained a jurisdiction column; no existing row moved."""
    for row in rp.seed_rows():
        assert row["jurisdiction_code"] == "GH"
    p5_codes = rp.P5_PARAMETER_CODES
    for row in rp.seed_rows():
        if row["param_code"] in p5_codes:
            continue
        assert set(row) == {
            "scope_type",
            "scope_key",
            "param_code",
            "jurisdiction_code",
            "value_numeric",
            "value_json",
            "unit",
            "source_citation",
            "confirmation_status",
            "effective_from",
            "effective_to",
            "status",
            "proposed_by",
            "approved_by",
        }


def test_p5_seed_rows_returns_only_the_p5_rows() -> None:
    rows = rp.p5_seed_rows()
    assert {row["param_code"] for row in rows} == rp.P5_PARAMETER_CODES
    assert all(row["status"] == "approved" for row in rows)
    assert all(row["confirmation_status"] == "pending" for row in rows)


def test_the_migrations_pinned_rows_equal_the_catalogue_today() -> None:
    module = _migration_module()
    pinned = {row[0]: row for row in module.SEEDS}
    catalogue = {spec.param_code: spec for spec in rp.P5_SEED_PARAMETERS}

    assert set(pinned) == set(catalogue)
    for code, spec in catalogue.items():
        _, value, value_json, unit, confirmation, citation = pinned[code]
        assert value == spec.value, code
        assert value_json == (
            None if spec.value_json is None else dict(spec.value_json)
        ), code
        assert unit == spec.unit, code
        assert confirmation == spec.confirmation_status, code
        assert citation == spec.source_citation, code


def test_the_migration_does_not_import_the_live_catalogue() -> None:
    """A later catalogue edit must not change what a deployed revision seeded.

    Checked by AST, not by text: the module's own docstring explains why it
    pins its rows, and a substring check would convict that explanation.
    """
    import ast  # noqa: PLC0415 - one local assertion

    tree = ast.parse(MIGRATION.read_text(), filename=str(MIGRATION))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not any("regulatory_parameters" in name for name in imported), sorted(imported)


def test_every_citation_fits_the_column_it_is_stored_in() -> None:
    """A citation over the column width fails the MIGRATION, not the catalogue.

    ``regulatory_parameter.source_citation`` is ``String(240)``. SQLite ignores
    the width and Postgres does not, so six over-long P5 citations passed every
    hermetic test and only failed when the revision was actually applied. This
    is the cheap place to catch the next one.
    """
    column_type = RegulatoryParameter.__table__.columns["source_citation"].type
    width = getattr(column_type, "length", None)
    assert isinstance(width, int)
    over = {
        spec.param_code: len(spec.source_citation)
        for spec in rp.SEED_PARAMETERS
        if len(spec.source_citation) > width
    }
    assert over == {}, over


@pytest.mark.parametrize("code", sorted(rp.P5_PARAMETER_CODES))
def test_every_p5_code_has_a_registered_shape(code: str) -> None:
    assert code in parameter_shapes.SHAPES, (
        f"{code} has no registered shape, so the operator console would accept a "
        "malformed value for it"
    )


@pytest.mark.parametrize("spec", rp.P5_SEED_PARAMETERS, ids=lambda s: s.param_code)
def test_every_p5_seed_satisfies_its_own_shape(spec: rp.ParamSpec) -> None:
    """A seeded table and a staff-proposed one cannot differ in form."""
    parameter_shapes.validate(
        spec.param_code,
        None if spec.value is None else Decimal(spec.value),
        spec.value_json,
    )


def test_a_malformed_bucket_ladder_is_refused_by_the_shape() -> None:
    """The guard reports rather than merely existing."""
    good = next(
        spec for spec in rp.P5_SEED_PARAMETERS if spec.param_code == "irrbb_sf_time_buckets"
    )
    assert good.value_json is not None
    body = dict(good.value_json)
    buckets = [dict(entry) for entry in body["buckets"]]
    # Two buckets keyed the same silently drops one bucket's flows.
    buckets[1]["key"] = buckets[0]["key"]
    body["buckets"] = buckets

    with pytest.raises(parameter_shapes.ParameterShapeError) as caught:
        parameter_shapes.validate("irrbb_sf_time_buckets", None, body)

    assert "listed twice" in caught.value.message


def test_a_representative_row_says_so_in_its_own_citation() -> None:
    """The marker is DATA, so the label arrives from the console too."""
    representative = {
        spec.param_code
        for spec in rp.P5_SEED_PARAMETERS
        if sfp.REPRESENTATIVE_MARKER in spec.source_citation.upper()
    }
    assert "irrbb_sf_default_cash_flow_profile" in representative
    assert "ga_min_effective_names" in representative
    # And the one the engine declares in code is the same one the console marks.
    assert representative >= sfp.REPRESENTATIVE_CODES


def test_every_standardised_framework_row_is_pending_confirmation() -> None:
    """The guideline is an exposure draft; a printed value is not confirmed."""
    for spec in rp.IRRBB_SF_SEED_PARAMETERS:
        assert spec.confirmation_status == "pending", spec.param_code


# --- the filed shock magnitudes ----------------------------------------------
#
# Audit U-2 (2026-09-20): nothing compared the three copies of these numbers.
# The only "450" assertion in the tree lived in the domain fixture and compared
# that fixture against its own module constant two hundred lines above, so both
# sides moved together. A staff edit to ``_SF_SHOCKS_BY_CURRENCY`` changed every
# filed ΔEVE and the supervisory outlier verdict with it, and the whole suite
# stayed green.
#
# These values are deliberately written out here, in a test, and nowhere in the
# engine (D-024). That is the point: the catalogue and the migration are what
# the platform seeds, and this is the file someone has to open — and explain in
# a review — before a number a bank files can move.

#: What the platform seeds, per shock kind, per currency of the POSITION.
FILED_SHOCK_BP: dict[str, dict[str, str]] = {
    "parallel": {
        "GHS": "450", "USD": "200", "EUR": "225",
        "GBP": "275", "CNY": "225", "OTHER": "325",
    },
    "short": {
        "GHS": "500", "USD": "300", "EUR": "350",
        "GBP": "425", "CNY": "300", "OTHER": "500",
    },
    "long": {
        "GHS": "300", "USD": "225", "EUR": "200",
        "GBP": "250", "CNY": "150", "OTHER": "300",
    },
}

_SHOCK_CODES = {
    "parallel": "irrbb_sf_parallel_shock_bp",
    "short": "irrbb_sf_short_shock_bp",
    "long": "irrbb_sf_long_shock_bp",
}

#: The long-rate column of the Basel standardised framework's own shock table,
#: for the four currencies this guideline calibrates individually. It is NOT a
#: governed value and no engine reads it: it exists so the sentence the
#: long-shock citation prints about Basel can be checked rather than believed.
#: Recorded from the 2026-09-20 regulatory audit's comparison (R-5); the
#: guideline itself is not held in this checkout (audit U-1), which is exactly
#: why the claim needed pinning rather than repeating.
BASEL_LONG_SHOCK_BP: dict[str, str] = {
    "USD": "150", "EUR": "100", "GBP": "150", "CNY": "150",
}


def _seeded_shock_body(kind: str) -> dict[str, str]:
    spec = next(
        spec
        for spec in rp.IRRBB_SF_SEED_PARAMETERS
        if spec.param_code == _SHOCK_CODES[kind]
    )
    assert spec.value_json is not None
    return {
        key: str(value) for key, value in spec.value_json.items() if key != "schema"
    }


@pytest.mark.parametrize("kind", sorted(FILED_SHOCK_BP))
def test_the_seeded_shock_magnitudes_are_the_ones_a_bank_files(kind: str) -> None:
    """Change a filed number and this fails. That is the whole control.

    The catalogue, the migration and the domain fixture all had to agree before
    today; none of them had to agree with anything a reviewer wrote down.
    """
    assert _seeded_shock_body(kind) == FILED_SHOCK_BP[kind]


@pytest.mark.parametrize("kind", sorted(FILED_SHOCK_BP))
def test_the_migration_seeds_the_same_shock_magnitudes(kind: str) -> None:
    """Restated at magnitude, not just as catalogue-equals-migration.

    ``test_the_migrations_pinned_rows_equal_the_catalogue_today`` already proves
    the two agree; it cannot notice that they agree on a changed number.
    """
    pinned = {row[0]: row for row in _migration_module().SEEDS}
    body = pinned[_SHOCK_CODES[kind]][2]

    assert {k: str(v) for k, v in body.items() if k != "schema"} == FILED_SHOCK_BP[kind]


@pytest.mark.parametrize("kind", sorted(FILED_SHOCK_BP))
def test_the_domain_fixture_reproduces_the_production_shock_table(kind: str) -> None:
    """The third copy, and the one the eight golden vectors are computed on.

    The domain suite builds its own control-plane rows so it never imports a
    service module. That purity is right, and it means the goldens can drift
    away from production without a single failure. Checked from THIS side of
    the boundary, where importing both is allowed.
    """
    from tests.domain.irr import test_sf_fixtures as fixture  # noqa: PLC0415

    table = {"parallel": fixture.PARALLEL_BP, "short": fixture.SHORT_BP, "long": fixture.LONG_BP}

    assert dict(table[kind]) == FILED_SHOCK_BP[kind]


def test_the_long_shock_citation_counts_its_divergences_correctly() -> None:
    """Audit R-5: the citation made a checkable claim, and it was wrong.

    It read "one printed value differs from the Basel standard" while three of
    the four individually calibrated currencies differ. The table is governed
    and was NOT touched — a printed value is the regulator's, and correcting a
    sentence by moving a number would be the wrong repair. The sentence is
    ours, so the sentence moved, and it is pinned to the arithmetic here so a
    later edit to a shock cannot leave the claim stale again.
    """
    seeded = _seeded_shock_body("long")
    differing = sorted(
        code for code, basel in BASEL_LONG_SHOCK_BP.items() if seeded[code] != basel
    )
    citation = next(
        spec.source_citation
        for spec in rp.IRRBB_SF_SEED_PARAMETERS
        if spec.param_code == "irrbb_sf_long_shock_bp"
    )

    # The sentence is tied to the arithmetic, not merely checked for a word: a
    # count that leaves this map raises rather than passing quietly, which is
    # what forces the next editor to write the new sentence deliberately.
    spelled = {
        1: "one printed value differs from the Basel standard",
        2: "two printed values differ from the Basel standard",
        3: "three printed values differ from the Basel standard",
        4: "four printed values differ from the Basel standard",
    }
    assert differing == ["EUR", "GBP", "USD"]
    assert spelled[len(differing)] in citation
