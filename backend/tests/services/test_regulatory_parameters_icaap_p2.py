"""The ICAAP governed codes are seeded EXACTLY ONCE, and every seed is well formed.

Two workstreams seed ICAAP parameters into one global table: the workspace
(``202609190055`` via ``app/services/icaap/parameters.py``, eight codes) and the
Pillar 2 engine (``202609190056`` via ``regulatory_parameters``, twenty). Six
codes were drafted into both sets. A code seeded twice does not fail loudly —
``uq_regulatory_parameter_generation`` would reject the second INSERT at the same
effective date, but the seed logic dates a collision a day earlier, so the
second copy would quietly become the ACTIVE generation and the first would be
superseded by a row nobody proposed. That is exactly the kind of silent
divergence the control plane exists to prevent, so the disjointness is pinned
here rather than left to review.

A third catalogue (``202609200063``) now seeds SOME of these same codes for
Nigeria and Kenya. That is not a duplicate: the uniqueness key carries the
jurisdiction, and resolution matches it exactly with no fallback. Its own
disjointness across all five catalogues is pinned in
``test_regulatory_parameters_icaap_jurisdictions.py``.

Also pinned: every seeded value matches its declared shape (D-037), every
REPRESENTATIVE calibration ships ``pending`` (D-039), every citation fits the
column, and the ``202609190056`` migration's pinned rows equal the catalogue's.
"""

from __future__ import annotations

import ast
import importlib.util
from collections import Counter
from decimal import Decimal
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
MIGRATION = BACKEND / "alembic" / "versions" / "202609190056_icaap_pillar2_parameters.py"
#: ``regulatory_parameter.source_citation`` is String(240).
CITATION_MAX = 240
REPRESENTATIVE = "REPRESENTATIVE:"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("icaap_p2_parameters_migration", MIGRATION)
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


# --- the disjointness rule ------------------------------------------------


def test_no_icaap_code_is_seeded_twice() -> None:
    """P1's eight and P2's twenty are disjoint, and neither repeats itself."""
    workspace = workspace_parameters.ICAAP_PARAM_CODES
    pillar2 = regulatory_parameters.P2_PARAMETER_CODES
    assert len(workspace) == 8
    assert len(pillar2) == 20
    assert workspace & pillar2 == frozenset(), (
        "A code seeded by both migrations would end up with two generations, the "
        "second silently superseding the first. Drop it from one of them."
    )
    rows = [*workspace_parameters.seed_rows(), *regulatory_parameters.p2_seed_rows()]
    duplicates = [key for key, count in Counter(_seed_key(r) for r in rows).items() if count > 1]
    assert duplicates == []


def test_the_whole_seed_catalogue_has_no_duplicate_generation() -> None:
    """Not only the ICAAP codes: the P2 rows are spliced into SEED_PARAMETERS."""
    rows = regulatory_parameters.seed_rows()
    duplicates = [key for key, count in Counter(_seed_key(r) for r in rows).items() if count > 1]
    assert duplicates == []
    codes = {str(row["param_code"]) for row in rows}
    assert codes >= regulatory_parameters.P2_PARAMETER_CODES


def test_the_hermetic_fixture_seeds_every_icaap_code_exactly_once(db_session: Session) -> None:
    """``create_all`` runs no migration, so the fixture is the only seeder here.

    Partitioned by jurisdiction since ``202609200063``: these codes now also
    govern Nigeria and Kenya, where it is the JURISDICTION that is new and not
    the code. Every assertion this test made about the Ghanaian rows still
    holds of them, and the rows beside them must belong to a jurisdiction the
    catalogue declares rather than to one that arrived by accident.
    """
    expected = workspace_parameters.ICAAP_PARAM_CODES | regulatory_parameters.P2_PARAMETER_CODES
    rows = list(
        db_session.scalars(
            select(RegulatoryParameter).where(RegulatoryParameter.param_code.in_(expected))
        )
    )
    counts = Counter(
        (row.scope_type, row.scope_key, row.param_code, row.jurisdiction_code) for row in rows
    )
    assert [key for key, count in counts.items() if count > 1] == []
    assert {code for _st, _sk, code, j in counts if j == "GH"} == expected
    allowed = {"GH", *workspace_parameters.ICAAP_SEEDED_JURISDICTIONS}
    for row in rows:
        assert row.status == "approved"
        assert row.effective_to is None
        assert row.scope_type == "institution_class"
        assert row.scope_key == "bank"
        assert row.jurisdiction_code in allowed


# --- the P2 catalogue itself ----------------------------------------------


@pytest.mark.parametrize(
    "spec", regulatory_parameters.ICAAP_P2_SEED_PARAMETERS, ids=lambda s: s.param_code
)
def test_every_p2_seed_is_bank_scoped_ghana_and_carries_a_citation(
    spec: regulatory_parameters.ParamSpec,
) -> None:
    assert (spec.scope_type, spec.scope_key) == ("institution_class", "bank")
    assert spec.source_citation
    assert len(spec.source_citation) <= CITATION_MAX
    assert spec.confirmation_status in {"confirmed", "pending"}
    # Exactly one of the two value arms (the seed builder enforces it too).
    assert (spec.value is None) != (spec.value_json is None)


@pytest.mark.parametrize(
    "spec", regulatory_parameters.ICAAP_P2_SEED_PARAMETERS, ids=lambda s: s.param_code
)
def test_a_representative_calibration_is_never_shipped_as_confirmed(
    spec: regulatory_parameters.ParamSpec,
) -> None:
    """D-039: an unsourced calibration must say so and must not read as settled."""
    if spec.source_citation.startswith(REPRESENTATIVE):
        assert spec.confirmation_status == "pending"


@pytest.mark.parametrize(
    "row", regulatory_parameters.p2_seed_rows(), ids=lambda r: str(r["param_code"])
)
def test_every_seeded_value_matches_its_declared_shape(row: dict[str, Any]) -> None:
    code = str(row["param_code"])
    assert code in parameter_shapes.SHAPES, "a P2 code with no registered shape is uneditable"
    value = row["value_numeric"]
    assert value is None or isinstance(value, Decimal)
    parameter_shapes.validate(code, value, row["value_json"])  # type: ignore[arg-type]


def test_the_workspace_codes_also_have_shapes() -> None:
    """P1's eight are console-editable too — including the one table among them."""
    for row in workspace_parameters.seed_rows():
        code = str(row["param_code"])
        assert code in parameter_shapes.SHAPES
        parameter_shapes.validate(code, row["value_numeric"], row["value_json"])  # type: ignore[arg-type]


def test_a_seed_with_both_or_neither_value_is_refused() -> None:
    both = regulatory_parameters.ParamSpec(
        "institution_class", "bank", "x", "1", "percent", "cite", "pending", {"schema": "s"}
    )
    with pytest.raises(ValueError, match="exactly one of value or value_json"):
        regulatory_parameters._seed_row(both, "platform_seed")  # noqa: SLF001
    neither = regulatory_parameters.ParamSpec(
        "institution_class", "bank", "x", None, "percent", "cite", "pending", None
    )
    with pytest.raises(ValueError, match="exactly one of value or value_json"):
        regulatory_parameters._seed_row(neither, "platform_seed")  # noqa: SLF001


def test_no_parameter_direction_entry_was_added_for_the_p2_codes() -> None:
    """These have no tenant register layer to clamp; an inert direction entry
    would move the governed-codes golden and imply a tightening rule that does
    not exist."""
    assert (
        regulatory_parameters.P2_PARAMETER_CODES & set(regulatory_parameters.PARAMETER_DIRECTION)
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
        for spec in regulatory_parameters.ICAAP_P2_SEED_PARAMETERS
    }
    assert pinned == live
    assert tuple(module.PARAM_CODES) == tuple(
        spec.param_code for spec in regulatory_parameters.ICAAP_P2_SEED_PARAMETERS
    )
    assert module.EFFECTIVE_FROM == regulatory_parameters.SEED_EFFECTIVE_FROM
    assert module.SEED_ACTOR == regulatory_parameters.SEED_ACTOR
    assert module.down_revision == "202609190055"


def test_the_migration_pins_its_own_rows_and_imports_no_catalogue() -> None:
    """Security audit L-4: a later catalogue edit must not change what a
    shipped revision seeded."""
    source = MIGRATION.read_text(encoding="utf-8")
    body = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(body)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "seed_rows" not in imported
    assert "regulatory_parameters" not in (
        node.module or "" for node in ast.walk(body) if isinstance(node, ast.ImportFrom)
    )


def test_the_migration_does_not_collide_with_the_workspace_seeds() -> None:
    workspace = _workspace_migration_codes()
    module = _migration()
    assert set(module.PARAM_CODES) & workspace == set()


def _workspace_migration_codes() -> set[str]:
    path = BACKEND / "alembic" / "versions" / "202609190055_icaap_workspace.py"
    spec = importlib.util.spec_from_file_location("icaap_workspace_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.PARAM_CODES)
