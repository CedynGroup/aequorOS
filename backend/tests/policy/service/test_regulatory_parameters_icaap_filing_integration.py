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

This module's other-suite cases live in ``test_regulatory_parameters_icaap_filing.py``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from typing import cast

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import RegulatoryParameter
from app.policy.service import regulatory_parameters
from tests.policy.service.test_regulatory_parameters_icaap_filing import _migration


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


def test_the_migration_pins_its_own_rows_and_imports_no_catalogue(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeds = deepcopy(regulatory_parameters.filing_seed_rows())
    codes = regulatory_parameters.FILING_PARAMETER_CODES

    def refuse_catalogue() -> list[dict[str, object]]:
        raise AssertionError("a shipped migration called the live catalogue")

    monkeypatch.setattr(regulatory_parameters, "seed_rows", refuse_catalogue)
    monkeypatch.setattr(regulatory_parameters, "filing_seed_rows", refuse_catalogue)
    monkeypatch.setattr(regulatory_parameters, "ICAAP_FILING_SEED_PARAMETERS", ())
    db_session.execute(delete(RegulatoryParameter).where(RegulatoryParameter.param_code.in_(codes)))
    module = _migration()
    with Operations.context(MigrationContext.configure(db_session.connection())):
        cast(Callable[[], None], module.upgrade)()
    rows = list(
        db_session.scalars(
            select(RegulatoryParameter).where(RegulatoryParameter.param_code.in_(codes))
        )
    )
    actual = {
        row.param_code: (
            row.value_numeric,
            cast(dict[str, object] | None, row.value_json),
            row.unit,
            row.confirmation_status,
            row.source_citation,
        )
        for row in rows
    }
    expected = {
        str(row["param_code"]): (
            row["value_numeric"],
            row["value_json"],
            row["unit"],
            row["confirmation_status"],
            row["source_citation"],
        )
        for row in seeds
    }
    assert actual == expected
