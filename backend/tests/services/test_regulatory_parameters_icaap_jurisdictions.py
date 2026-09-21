"""The Nigerian and Kenyan ICAAP rows: seeded once, pending, and honest.

``202609200063`` is the fifth catalogue to seed into one global table, and the
first to seed a code a previous one already seeded — because NG and KE quote
Ghana's codes and it is the JURISDICTION that is new. That makes two rules
sharper here than anywhere else:

* **Disjointness is on (scope, code, jurisdiction), not on the code.** A row
  seeded twice for the same key does not fail loudly: the collision rule dates
  the second a day earlier, it quietly becomes the ACTIVE generation, and it
  supersedes a row nobody proposed (D-053). So the key has to carry the
  jurisdiction, and the Ghanaian rows have to survive untouched.
* **The citations carry the sourcing caveat, and they are testable copy.**
  Neither the CBN nor the CBK text is in this repository and nobody here has
  read either one (D-076); the values come from the 2026-09-19 extraction
  record behind the two ``SOURCES.md`` files, whose ``sha256`` cannot be
  re-verified from a checkout. Every row therefore ships ``pending`` and says
  so in words that name the real gap. Ghana's rows say "awaiting stakeholder
  confirmation", which describes a different state — *the regulator has not
  confirmed our reading* rather than *we have not read the regulator* — and a
  test below keeps that phrasing out of these rows.

The ``REPRESENTATIVE:`` prefix is pinned too, because it is load-bearing rather
than decorative: ``services/icaap/params.ParameterRow.representative`` tests
``startswith``, and that flag is what puts the label on readiness, on the
Pillar 2 register and in a frozen report's parameter provenance. Moving the
prefix off the front of a string — or explaining the sourcing before it —
silently removes the label from every one of those surfaces.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.icaap.frameworks import registry
from app.domain.policy import parameter_shapes
from app.models import Jurisdiction, RegulatoryParameter
from app.services import regulatory_parameters
from app.services.icaap import parameters as workspace_parameters
from app.services.icaap.params import REPRESENTATIVE_PREFIX, ParameterRow
from tests.fixtures import reference_data

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "alembic" / "versions"
MIGRATION = VERSIONS / "202609200063_icaap_jurisdiction_parameters.py"
JURISDICTIONS_MIGRATION = VERSIONS / "202607230017_jurisdictions_registry.py"
#: ``regulatory_parameter.source_citation`` is String(240).
CITATION_MAX = 240
#: Rows that carry the ``REPRESENTATIVE:`` prefix: the platform's 5x5 matrix,
#: which per the extraction record neither regulator prescribes, and the
#: internal-control tolerance the Pillar 2 register compares sources against.
REPRESENTATIVE_CODES = frozenset(
    {
        workspace_parameters.MATERIALITY_MIN_SCORE,
        workspace_parameters.MATERIALITY_MIN_IMPACT,
        workspace_parameters.MATERIALITY_RATING_BANDS,
        workspace_parameters.PILLAR2_SOURCE_TOLERANCE_PCT,
    }
)
#: Every row that states platform policy rather than a regulator's rule. The
#: readiness amber window and the no-diversification default say so in words
#: instead of carrying the prefix, exactly as their Ghanaian twins do.
PLATFORM_CODES = REPRESENTATIVE_CODES | {
    workspace_parameters.DEADLINE_AMBER_DAYS,
    workspace_parameters.DIVERSIFICATION_BENEFIT_ALLOWED,
}
CATALOGUE = workspace_parameters.ICAAP_JURISDICTION_SEED_PARAMETERS


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["scope_type"]),
        str(row["scope_key"]),
        str(row["param_code"]),
        str(row["jurisdiction_code"]),
    )


def _spec_id(spec: workspace_parameters.IcaapParamSeed) -> str:
    return f"{spec.jurisdiction_code}-{spec.param_code}"


# --- what is here, and what is deliberately not ----------------------------


def test_the_catalogue_covers_exactly_nigeria_and_kenya() -> None:
    assert sorted(workspace_parameters.ICAAP_SEEDED_JURISDICTIONS) == ["KE", "NG"]
    per_jurisdiction = Counter(spec.jurisdiction_code for spec in CATALOGUE)
    # Nigeria: the deadline, the two review intervals, the three matrix codes
    # and the three platform-policy rows the register and readiness resolve for
    # every cycle. Kenya: the same, plus the two horizons its checklist quotes
    # and Nigeria's does not.
    assert per_jurisdiction == Counter({"NG": 9, "KE": 11})


def test_the_review_interval_codes_are_the_pillar_two_ones() -> None:
    """These two rows seed a code another catalogue owns for Ghana.

    Named in ``services/icaap/parameters`` so the jurisdiction rows can quote
    them; pinned here so a rename in the Pillar 2 catalogue cannot leave these
    rows pointing at a code nothing reads.
    """
    assert {
        workspace_parameters.REVIEW_MAX_MONTHS,
        workspace_parameters.INDEPENDENT_REVIEW_MAX_MONTHS,
    } <= regulatory_parameters.P2_PARAMETER_CODES


def test_no_new_parameter_code_was_invented() -> None:
    """Every code already exists for Ghana; only the jurisdiction is new.

    If this ever fails, the seeded-code union in
    ``tests/architecture/test_icaap_no_regulatory_literals.py`` has to learn
    about the new code as well — it checks codes, not jurisdictions.
    """
    known = (
        workspace_parameters.ICAAP_PARAM_CODES
        | regulatory_parameters.P2_PARAMETER_CODES
        | regulatory_parameters.FILING_PARAMETER_CODES
        | regulatory_parameters.P5_PARAMETER_CODES
    )
    assert {spec.param_code for spec in CATALOGUE} <= known


def test_the_preferred_horizon_codes_are_not_seeded() -> None:
    """Kenya's "ideally five years" is asked for in words, not as a row.

    P5-E reworded ``ke_4iva_horizon`` and ``ke_002b_horizon`` to reference only
    the governed MINIMUM and to ask whether the longer horizon was used, which
    is why no framework quotes an ``_ideal`` code. Seeding one now would create
    a governed row nothing reads; restoring the wording changes item text and
    is a new framework VERSION, not a parameter row.
    """
    ideal = {
        "icaap_stress_horizon_years_ideal",
        "icaap_capital_planning_horizon_years_ideal",
    }
    assert {spec.param_code for spec in CATALOGUE} & ideal == frozenset()
    for framework in registry.load_all():
        assert framework.param_refs() & ideal == frozenset(), framework.code


def test_no_ghana_calibration_was_copied_across_the_border() -> None:
    """The IRRBB standardised framework and the granularity adjustment refuse.

    No CBN or CBK figure has been read for any of them, so those methods must
    keep answering ``missing_parameter`` for a Nigerian or Kenyan bank rather
    than quietly run on Ghana's numbers. The same goes for the disclosure
    period: neither instrument requires publication, and both frameworks
    declare ``disclosure: null``.
    """
    seeded = {spec.param_code for spec in CATALOGUE}
    assert seeded & regulatory_parameters.P5_PARAMETER_CODES == frozenset()
    assert workspace_parameters.DISCLOSURE_SUBMISSION_MONTHS not in seeded
    for framework in registry.load_all():
        if framework.jurisdiction in workspace_parameters.ICAAP_SEEDED_JURISDICTIONS:
            assert framework.disclosure is None, framework.code


# --- the disjointness rule -------------------------------------------------


def test_no_row_is_seeded_twice_across_the_five_catalogues() -> None:
    """The row-by-row seeders, which the hermetic fixture runs together.

    ``regulatory_parameters.seed_rows`` is not in the list because the P2,
    filing and P5 rows are spliced into it; it is the bulk arm, checked for
    duplicates of its own in ``test_regulatory_parameters_icaap_filing.py``.
    """
    rows = [
        *workspace_parameters.seed_rows(),
        *workspace_parameters.jurisdiction_seed_rows(),
        *regulatory_parameters.p2_seed_rows(),
        *regulatory_parameters.filing_seed_rows(),
        *regulatory_parameters.p5_seed_rows(),
    ]
    duplicates = [key for key, count in Counter(_seed_key(r) for r in rows).items() if count > 1]
    assert duplicates == [], (
        "A duplicate (scope, code, jurisdiction) does not raise. The collision rule "
        "back-dates the second row, which then becomes the ACTIVE generation of a value "
        "nobody proposed (D-053)."
    )


def test_the_bulk_catalogue_never_reaches_nigeria_or_kenya() -> None:
    """``SEED_PARAMETERS`` fires only on an empty table and is Ghana's.

    A row landing in both arms would be inserted twice on a database that took
    the bulk seed and then the row-by-row one.
    """
    bulk = {_seed_key(row) for row in regulatory_parameters.seed_rows()}
    assert all(key[3] == "GH" for key in bulk)
    assert bulk & {_seed_key(row) for row in workspace_parameters.jurisdiction_seed_rows()} == set()


def test_the_ghana_rows_are_untouched_by_the_new_catalogue() -> None:
    """Every code here also governs Ghana; none of those rows may move."""
    ghana = {_seed_key(row) for row in workspace_parameters.seed_rows()}
    added = {_seed_key(row) for row in workspace_parameters.jurisdiction_seed_rows()}
    assert ghana & added == set()
    assert all(key[3] in {"NG", "KE"} for key in added)
    assert all(key[3] == "GH" for key in ghana)


# --- the catalogue itself --------------------------------------------------


@pytest.mark.parametrize("spec", CATALOGUE, ids=_spec_id)
def test_every_row_is_bank_scoped_pending_and_cites_its_source(
    spec: workspace_parameters.IcaapParamSeed,
) -> None:
    assert spec.jurisdiction_code in {"NG", "KE"}
    assert spec.source_citation
    assert len(spec.source_citation) <= CITATION_MAX
    # Second-hand provenance is not verified provenance (D-076). Nothing here
    # may present as settled, whichever regulator printed the paragraph.
    assert spec.confirmation_status == "pending"
    assert (spec.value is None) != (spec.value_json is None)


@pytest.mark.parametrize("spec", CATALOGUE, ids=_spec_id)
def test_no_citation_claims_the_primary_text_was_read(
    spec: workspace_parameters.IcaapParamSeed,
) -> None:
    """The caveat is in the row, not only in a report nobody will reread.

    A row derived from a regulator's paragraph says the text is not held here
    and has not been read. A platform judgement says REPRESENTATIVE instead,
    which is the stronger statement — it is not the regulator's number at all.
    And no row borrows Ghana's "awaiting stakeholder confirmation", which
    describes the opposite gap.
    """
    citation = spec.source_citation
    assert "awaiting stakeholder confirmation" not in citation.casefold()
    if spec.param_code in PLATFORM_CODES:
        assert citation.startswith(REPRESENTATIVE_PREFIX) or citation.startswith("AequorOS")
        return
    regulator = "CBN" if spec.jurisdiction_code == "NG" else "CBK"
    assert f"the {regulator} text is not held or read here" in citation
    assert "Pending verification." in citation


@pytest.mark.parametrize(
    "spec",
    [spec for spec in CATALOGUE if spec.param_code in REPRESENTATIVE_CODES],
    ids=_spec_id,
)
def test_a_platform_judgement_is_labelled_where_the_label_is_read(
    spec: workspace_parameters.IcaapParamSeed,
) -> None:
    """The prefix must be at the FRONT, because that is what is tested for.

    Readiness, the Pillar 2 register and the frozen snapshot all learn that a
    figure is the platform's from ``ParameterRow.representative``, which is a
    ``startswith``. A citation that explains its sourcing first and says
    REPRESENTATIVE afterwards reads fine and flags nothing.
    """
    assert spec.source_citation.startswith(REPRESENTATIVE_PREFIX)
    row = ParameterRow(
        code=spec.param_code,
        value=None if spec.value is None else Decimal(spec.value),
        value_json=spec.value_json,
        unit=spec.unit,
        confirmation_status=spec.confirmation_status,
        source_citation=spec.source_citation,
        effective_from=regulatory_parameters.SEED_EFFECTIVE_FROM,
        parameter_id="seed",
    )
    assert row.representative is True
    assert row.provenance()["representative"] is True


@pytest.mark.parametrize(
    "spec",
    [spec for spec in CATALOGUE if spec.param_code not in PLATFORM_CODES],
    ids=_spec_id,
)
def test_a_regulator_derived_row_is_not_dressed_up_as_the_platform_s(
    spec: workspace_parameters.IcaapParamSeed,
) -> None:
    """The inverse, so the label means something.

    A deadline the CBN printed is not a representative calibration; flagging it
    as one would train a reader to ignore the flag on the rows that need it.
    ``pending`` is what these rows carry, and it is the truthful signal.
    """
    assert not spec.source_citation.startswith(REPRESENTATIVE_PREFIX)


def test_the_platform_values_are_the_same_in_every_jurisdiction() -> None:
    """The matrix and the amber window are one platform default, not three.

    Only the sentence naming the instrument differs; if the VALUES drifted, two
    banks would be assessed against different matrices for no stated reason.
    """
    ghana = {
        spec.param_code: (spec.value, spec.value_json, spec.unit)
        for spec in (
            *workspace_parameters.ICAAP_SEED_PARAMETERS,
            *regulatory_parameters.ICAAP_P2_SEED_PARAMETERS,
        )
        if spec.param_code in PLATFORM_CODES
    }
    assert set(ghana) == PLATFORM_CODES
    for spec in CATALOGUE:
        if spec.param_code in PLATFORM_CODES:
            assert (spec.value, spec.value_json, spec.unit) == ghana[spec.param_code], _spec_id(
                spec
            )


@pytest.mark.parametrize(
    "row",
    workspace_parameters.jurisdiction_seed_rows(),
    ids=lambda r: f"{r['jurisdiction_code']}-{r['param_code']}",
)
def test_every_seeded_value_matches_its_declared_shape(row: dict[str, Any]) -> None:
    code = str(row["param_code"])
    assert code in parameter_shapes.SHAPES, "a code with no registered shape is uneditable"
    parameter_shapes.validate(code, row["value_numeric"], row["value_json"])  # type: ignore[arg-type]


def test_the_ghana_rows_still_emit_an_identical_dict() -> None:
    """``IcaapParamSeed`` gained a field; the existing rows must not have moved."""
    for row in workspace_parameters.seed_rows():
        assert row["jurisdiction_code"] == "GH"
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


# --- the hermetic database and the registry --------------------------------


def test_the_hermetic_fixture_seeds_every_row_exactly_once(db_session: Session) -> None:
    """``create_all`` runs no migration, so the shared fixture is the only seeder.

    Without it every Nigerian or Kenyan hermetic test would answer
    ``missing_parameter``, which reads as a broken framework rather than as an
    unseeded database.
    """
    expected = {
        (spec.param_code, spec.jurisdiction_code): spec
        for spec in workspace_parameters.ICAAP_JURISDICTION_SEED_PARAMETERS
    }
    rows = list(
        db_session.scalars(
            select(RegulatoryParameter).where(
                RegulatoryParameter.jurisdiction_code.in_(["NG", "KE"])
            )
        )
    )
    counts = Counter((row.param_code, row.jurisdiction_code) for row in rows)
    assert set(counts) == set(expected)
    assert [key for key, count in counts.items() if count > 1] == []
    for row in rows:
        spec = expected[(row.param_code, row.jurisdiction_code)]
        assert (row.scope_type, row.scope_key) == ("institution_class", "bank")
        assert row.status == "approved"
        assert row.effective_to is None
        assert row.confirmation_status == "pending"
        assert row.source_citation == spec.source_citation
        assert row.value_numeric == (None if spec.value is None else Decimal(spec.value))
        assert row.value_json == spec.value_json


def test_the_fixture_registry_matches_the_jurisdictions_migration() -> None:
    """The fixture grew NG and KE; it must still mirror what is deployed.

    A hermetic bank resolves its regulator's name from this registry, so a
    fixture row that drifted from the migration would make the whole suite
    agree with itself and disagree with production.
    """
    module = _load(JURISDICTIONS_MIGRATION, "_jurisdictions_seed_for_icaap")
    deployed = {
        row[0]: {
            "country_name": row[1],
            "currency_code": row[2],
            "currency_name": row[3],
            "locale": row[4],
            "central_bank_name": row[5],
            "regulator_short": row[6],
            "submission_portal": row[7],
            "timezone": row[8],
        }
        for row in module.SEED_ROWS
    }
    for fixture in reference_data.JURISDICTIONS:
        expected = deployed[str(fixture["code"])]
        for field, value in expected.items():
            assert fixture[field] == value, (fixture["code"], field)


def test_the_hermetic_database_knows_both_regulators(db_session: Session) -> None:
    rows = {
        row.code: row.regulator_short
        for row in db_session.scalars(select(Jurisdiction)).all()
    }
    assert rows["NG"] == "CBN"
    assert rows["KE"] == "CBK"


# --- the migration agrees with the catalogue -------------------------------


def test_the_catalogue_and_the_migration_agree() -> None:
    module = _load(MIGRATION, "icaap_jurisdiction_parameters_migration")
    pinned = {
        (jurisdiction, code): (value, value_json, unit, status, citation)
        for jurisdiction, code, value, value_json, unit, status, citation in module.SEEDS
    }
    live = {
        (spec.jurisdiction_code, spec.param_code): (
            spec.value,
            None if spec.value_json is None else dict(spec.value_json),
            spec.unit,
            spec.confirmation_status,
            spec.source_citation,
        )
        for spec in CATALOGUE
    }
    assert pinned == live


def test_the_migration_chains_after_the_previous_head() -> None:
    module = _load(MIGRATION, "icaap_jurisdiction_parameters_revision")
    assert module.revision == "202609200063"
    assert module.down_revision == "202609190062"


def test_the_migration_scopes_its_downgrade_to_the_new_jurisdictions() -> None:
    """A code-only DELETE would take Ghana's rows with it.

    Read from the file rather than executed, because the hermetic suite runs no
    migration at all and the failure mode is silent data loss on a real one.
    """
    module = _load(MIGRATION, "icaap_jurisdiction_parameters_downgrade")
    assert module.JURISDICTIONS == ("NG", "KE")
    assert module._jurisdictions_sql() == "'NG', 'KE'"
    source = MIGRATION.read_text(encoding="utf-8")
    delete = source.split("def downgrade()")[1]
    assert "jurisdiction_code IN ({_jurisdictions_sql()})" in delete
    assert "'GH'" not in delete
