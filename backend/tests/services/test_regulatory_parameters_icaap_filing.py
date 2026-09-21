"""The ICAAP FILING codes are seeded exactly once, and say what they are.

Three ICAAP workstreams now seed into one global table: the workspace
(``202609190055``, eight codes), the Pillar 2 engine (``202609190056``, twenty)
and the filing plane (``202609190059``, two). The disjointness rule that
``test_regulatory_parameters_icaap_p2.py`` established for the first two has to
hold for the third, and for the same reason: a code seeded twice does not fail
loudly. The second INSERT would be dated a day earlier by the collision rule,
quietly become the ACTIVE generation, and supersede a row nobody proposed.

The filing plane deliberately adds only TWO codes. The deadline months, the
disclosure months and the stress horizon it reads are already governed by
``202609190055``; re-seeding them here to "keep P3's parameters together" is
exactly the duplicate this file exists to prevent.

Also pinned here, because both are judgement calls rather than mechanics:

* ``icaap_report_first_as_of_date`` is a DATE, carried structurally. It is the
  one ICAAP parameter whose value is not a number, and it ships ``pending``
  because 31 December 2026 is the platform's reading of an exposure draft, not
  BoG's statement (D-010, D-032).
* neither code enters ``PARAMETER_DIRECTION``. There is no tenant board register
  layer for either, so a direction entry would be inert while implying a
  tightening rule that does not exist — the same call, for the same reason, that
  the Pillar 2 codes made.
"""

from __future__ import annotations

import ast
import importlib.util
from collections import Counter
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.policy import parameter_shapes
from app.models import RegulatoryParameter
from app.services import regulatory_parameters
from app.services.icaap import parameters as workspace_parameters

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "alembic" / "versions"
MIGRATION = VERSIONS / "202609190059_icaap_filing_parameters.py"
#: ``regulatory_parameter.source_citation`` is String(240).
CITATION_MAX = 240


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration() -> ModuleType:
    return _load(MIGRATION, "icaap_filing_parameters_migration")


def _seed_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["scope_type"]),
        str(row["scope_key"]),
        str(row["param_code"]),
        str(row["jurisdiction_code"]),
    )


# --- the disjointness rule ------------------------------------------------


def test_no_icaap_code_is_seeded_twice() -> None:
    workspace = workspace_parameters.ICAAP_PARAM_CODES
    pillar2 = regulatory_parameters.P2_PARAMETER_CODES
    filing = regulatory_parameters.FILING_PARAMETER_CODES
    assert len(filing) == 2
    assert filing & workspace == frozenset(), (
        "The filing plane reads the deadline and horizon codes but does not own "
        "them; 202609190055 seeds those. A second generation of the same code "
        "would silently supersede the first."
    )
    assert filing & pillar2 == frozenset()
    rows = [
        *workspace_parameters.seed_rows(),
        *regulatory_parameters.p2_seed_rows(),
        *regulatory_parameters.filing_seed_rows(),
    ]
    duplicates = [key for key, count in Counter(_seed_key(r) for r in rows).items() if count > 1]
    assert duplicates == []


def test_the_whole_seed_catalogue_still_has_no_duplicate_generation() -> None:
    """The filing rows are spliced into ``SEED_PARAMETERS`` like the P2 ones."""
    rows = regulatory_parameters.seed_rows()
    duplicates = [key for key, count in Counter(_seed_key(r) for r in rows).items() if count > 1]
    assert duplicates == []
    codes = {str(row["param_code"]) for row in rows}
    assert codes >= regulatory_parameters.FILING_PARAMETER_CODES


def test_the_hermetic_fixture_seeds_every_filing_code_exactly_once(db_session: Session) -> None:
    """``create_all`` runs no migration, so the shared fixture is the only seeder.

    Without this the ICAAP eligibility check would answer ``missing_parameter``
    in every hermetic test and every Playwright journey, which reads as a broken
    feature rather than as an unseeded database.
    """
    expected = regulatory_parameters.FILING_PARAMETER_CODES
    rows = list(
        db_session.scalars(
            select(RegulatoryParameter).where(RegulatoryParameter.param_code.in_(expected))
        )
    )
    counts = Counter(
        (row.scope_type, row.scope_key, row.param_code, row.jurisdiction_code) for row in rows
    )
    assert {code for _st, _sk, code, _j in counts} == expected
    assert [key for key, count in counts.items() if count > 1] == []
    for row in rows:
        assert row.status == "approved"
        assert row.effective_to is None
        assert (row.scope_type, row.scope_key, row.jurisdiction_code) == (
            "institution_class",
            "bank",
            "GH",
        )


# --- the catalogue itself --------------------------------------------------


@pytest.mark.parametrize(
    "spec", regulatory_parameters.ICAAP_FILING_SEED_PARAMETERS, ids=lambda s: s.param_code
)
def test_every_filing_seed_is_bank_scoped_and_cites_its_source(
    spec: regulatory_parameters.ParamSpec,
) -> None:
    assert (spec.scope_type, spec.scope_key) == ("institution_class", "bank")
    assert spec.source_citation
    assert len(spec.source_citation) <= CITATION_MAX
    # Both readings are the platform's, from an exposure draft BoG has not
    # finalised. Shipping either as settled would let a bank file against it.
    assert spec.confirmation_status == "pending"
    assert (spec.value is None) != (spec.value_json is None)


@pytest.mark.parametrize(
    "row", regulatory_parameters.filing_seed_rows(), ids=lambda r: str(r["param_code"])
)
def test_every_seeded_value_matches_its_declared_shape(row: dict[str, Any]) -> None:
    code = str(row["param_code"])
    assert code in parameter_shapes.SHAPES, "a code with no registered shape is uneditable"
    parameter_shapes.validate(code, row["value_numeric"], row["value_json"])  # type: ignore[arg-type]


def test_the_first_as_of_date_is_a_real_date_and_is_not_settled() -> None:
    """D-024 / D-032: the value is data, and it says it is provisional."""
    (spec,) = [
        candidate
        for candidate in regulatory_parameters.ICAAP_FILING_SEED_PARAMETERS
        if candidate.param_code == "icaap_report_first_as_of_date"
    ]
    assert spec.value is None, "a date is not a number; it is carried structurally"
    assert spec.value_json is not None
    body = dict(spec.value_json)
    assert body["schema"] == "icaap-effective-date-v1"
    assert date.fromisoformat(str(body["date"])) == date(2026, 12, 31)
    assert "pending confirmation" in spec.source_citation


def test_no_parameter_direction_entry_was_added_for_the_filing_codes() -> None:
    """These have no tenant register layer to clamp; an inert direction entry
    would move the governed-codes golden and imply a tightening rule that does
    not exist. Same call as the Pillar 2 codes."""
    assert (
        regulatory_parameters.FILING_PARAMETER_CODES
        & set(regulatory_parameters.PARAMETER_DIRECTION)
        == frozenset()
    )


# --- the migration agrees with the catalogue ------------------------------


def test_the_catalogue_and_the_migration_agree() -> None:
    module = _migration()
    pinned = {
        code: (value, value_json, unit, status, citation)
        for code, value, value_json, unit, status, citation in module.SEEDS
    }
    live = {
        spec.param_code: (
            spec.value,
            None if spec.value_json is None else dict(spec.value_json),
            spec.unit,
            spec.confirmation_status,
            spec.source_citation,
        )
        for spec in regulatory_parameters.ICAAP_FILING_SEED_PARAMETERS
    }
    assert pinned == live
    assert tuple(module.PARAM_CODES) == tuple(
        spec.param_code for spec in regulatory_parameters.ICAAP_FILING_SEED_PARAMETERS
    )
    assert module.EFFECTIVE_FROM == regulatory_parameters.SEED_EFFECTIVE_FROM
    assert module.SEED_ACTOR == regulatory_parameters.SEED_ACTOR
    assert module.down_revision == "202609190058"


def test_the_migration_pins_its_own_rows_and_imports_no_catalogue() -> None:
    """Security audit L-4: a later catalogue edit must not change what a
    shipped revision seeded."""
    body = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(body)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "seed_rows" not in imported
    assert "filing_seed_rows" not in imported
    assert "regulatory_parameters" not in (
        node.module or "" for node in ast.walk(body) if isinstance(node, ast.ImportFrom)
    )


def test_the_migration_does_not_collide_with_the_earlier_icaap_seeds() -> None:
    workspace = set(_load(VERSIONS / "202609190055_icaap_workspace.py", "ws").PARAM_CODES)
    pillar2 = set(_load(VERSIONS / "202609190056_icaap_pillar2_parameters.py", "p2").PARAM_CODES)
    filing = set(_migration().PARAM_CODES)
    assert filing & workspace == set()
    assert filing & pillar2 == set()


# --- the artifact-kind vocabulary is stated once --------------------------


def test_every_artifact_kind_vocabulary_agrees() -> None:
    """A kind the model admits but the export route refuses is a half-cutover.

    ``docx_working`` was added in five places at once (DB CHECK, ORM tuple, wire
    schema, service alias, exporter alias) and the export route's own ``Literal``
    was missed, which compiled fine in Python and broke the generated TypeScript
    client. The vocabularies are compared here so the next kind cannot repeat it.

    The route's ``Literal`` is deliberately NOT in this comparison: it carries one
    extra member, ``xlsx_official``, which is an alias the route resolves to
    ``xlsx`` before anything else sees it. It is asserted as a superset instead.
    """
    from typing import get_args, get_type_hints  # noqa: PLC0415

    from app.features import manage_regulatory_reporting as routes  # noqa: PLC0415
    from app.models.regulatory_reporting import ARTIFACT_KINDS  # noqa: PLC0415
    from app.schemas.regulatory_reporting import ArtifactKind  # noqa: PLC0415
    from app.services.regulatory_reporting import exports  # noqa: PLC0415
    from app.services.regulatory_reporting import workflow as reporting_workflow  # noqa: PLC0415

    model = set(ARTIFACT_KINDS)
    assert model == set(get_args(ArtifactKind.__value__))
    assert model == set(get_args(reporting_workflow.ArtifactKind.__value__))
    assert model == set(get_args(exports.ExportKind.__value__))

    route_kinds = get_type_hints(routes.export_regulatory_package, include_extras=False)["kind"]
    assert set(get_args(route_kinds)) >= model, (
        "the export route must accept every kind the model admits; the extra "
        "'xlsx_official' is a route-level alias for the sealed 'xlsx' export"
    )
    # Every working kind is a real kind, and none of them is ever filed.
    assert model >= reporting_workflow.WORKING_ARTIFACT_KINDS
