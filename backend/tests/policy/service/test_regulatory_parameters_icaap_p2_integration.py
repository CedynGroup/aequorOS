"""The ICAAP governed codes are seeded EXACTLY ONCE, and every seed is well formed.

Two workstreams seed ICAAP parameters into one global table: the workspace
(``202609190055`` via ``app/icaap/service/parameters.py``, eight codes) and the
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

This module's other-suite cases live in ``test_regulatory_parameters_icaap_p2.py``.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.icaap.service import parameters as workspace_parameters
from app.models import RegulatoryParameter
from app.policy.service import regulatory_parameters


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
