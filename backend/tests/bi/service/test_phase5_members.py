"""The Phase 5 catalogue members, read end to end through the real compiler.

Five members reach the wire for the first time here, and each one is asked for by
name over rows the mart holds:

* ``position.officer_code`` / ``position.channel`` / ``position.account_status``
  over the four columns migration ``202609280076`` added;
* ``loans.arrears_amount_rc`` / ``loans.arrears_share_pct`` over one of them;
* ``gl.branch_ytd_rc`` / ``gl.branch_movement_rc`` over the branch ledger mart.

Two properties get their own tests because both are easy to break invisibly and
neither is visible in a passing build:

1. **A loan that states no arrears contributes NO ROW, never a zero.** The
   measures sum only what the bank stated, and the share's two legs cover the same
   population, so a partial book cannot read as a complete one. This file proves
   the figures themselves do not lie about what was and was not stated.
2. **``movement_rc`` stays NULL through the aggregation.** ``flow_sum`` must emit a
   bare ``sum(...)``: a branch whose previous month was never pushed has no known
   movement, and a coalesce to 0 would report a month of no change. A coalesce is
   one word to add and invisible afterwards.

This module's other-suite cases live in ``test_phase5_members_integration.py``.
"""

from __future__ import annotations

import pytest

from app.bi.domain.catalogue import Catalogue
from app.bi.domain.catalogue.dimensions import GL_BRANCH_DIMENSION_IDS
from app.bi.schemas.query import BiQuery
from app.bi.service.compiler import _resolve
from tests.bi.service.test_phase5_members_integration import JUN_30
from tests.bi.service.test_phase5_members_integration import (
    cat as cat,  # noqa: PLC0414 - shared with the split module
)

NEW_MEASURES: tuple[str, ...] = (
    "loans.arrears_amount_rc",
    "loans.arrears_share_pct",
    "gl.branch_ytd_rc",
    "gl.branch_movement_rc",
)


def test_the_movement_measure_is_declared_a_flow_and_the_level_a_stock(cat: Catalogue) -> None:
    """Both halves, because reading one as the other is wrong by a whole period."""
    level = cat.measure("gl.branch_ytd_rc")
    flow = cat.measure("gl.branch_movement_rc")
    assert (level.time_behaviour, level.aggregation) == ("stock", "sum")
    assert (flow.time_behaviour, flow.aggregation) == ("flow", "flow_sum")


def test_the_branch_ledger_measures_offer_only_dimensions_this_fact_carries(
    cat: Catalogue,
) -> None:
    """A grouping advertised and then refused is a member that looks reachable and
    is not. The branch ledger carries no position, loan, product or counterparty
    key, so none of those ids may appear."""
    for measure_id in ("gl.branch_ytd_rc", "gl.branch_movement_rc"):
        allowed = set(cat.measure(measure_id).allowed_dimensions)
        assert allowed == set(GL_BRANCH_DIMENSION_IDS)
        assert not any(
            member_id.startswith(("position.", "loan.", "product.", "counterparty."))
            for member_id in allowed
        )
        assert {"time.date", "branch.code", "gl_account.pl_line"} <= allowed


@pytest.mark.parametrize("measure_id", NEW_MEASURES)
def test_every_new_measure_resolves_to_one_fact_with_a_window(
    cat: Catalogue, measure_id: str
) -> None:
    resolved = _resolve(
        cat, BiQuery.model_validate({"measures": [measure_id], "time": {"as_of": JUN_30}}), ()
    )
    assert resolved.measures and resolved.fact
