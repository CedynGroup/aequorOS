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
than decorative: ``icaap/service/params.ParameterRow.representative`` tests
``startswith``, and that flag is what puts the label on readiness, on the
Pillar 2 register and in a frozen report's parameter provenance. Moving the
prefix off the front of a string — or explaining the sourcing before it —
silently removes the label from every one of those surfaces.

This module's other-suite cases live in
``test_regulatory_parameters_icaap_jurisdictions.py``.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.icaap.service import parameters as workspace_parameters
from app.models import Jurisdiction, RegulatoryParameter


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


def test_the_hermetic_database_knows_both_regulators(db_session: Session) -> None:
    rows = {row.code: row.regulator_short for row in db_session.scalars(select(Jurisdiction)).all()}
    assert rows["NG"] == "CBN"
    assert rows["KE"] == "CBK"
