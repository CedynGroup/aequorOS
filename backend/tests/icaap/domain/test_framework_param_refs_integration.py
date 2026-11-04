"""Every figure a regulator's checklist quotes is a console row (D-024).

The founder's rule is that no regulatory number lives in code — and framework
JSON is code's twin here, because shipping "within three months" as data would
be just as unreachable from the operator console as shipping it as a literal.
So a requirement that needs a figure writes ``{param:<code>}`` and the renderer
resolves it.

Three things have to be true for that to be worth anything, and each is a test
below:

1. **The item names its codes.** A placeholder the item does not declare in
   ``param_refs`` would render as raw ``{param:…}`` text in a bank's checklist.
   The parser already refuses that; here it is asserted over the published set.
2. **The code is editable.** A code with no registered shape cannot be proposed
   or approved in the console, so the "figure comes from the console" promise
   would be false for it.
3. **The text carries no figure of its own.** A paraphrase that says "three
   years" alongside a placeholder would be a second, unreachable copy of the
   same rule — and the one a reader believes.

Seeding is the fourth thing, and it is deliberately scoped to the frameworks a
deployment actually publishes: see the last two tests.

This module's other-suite cases live in ``test_framework_param_refs.py``.
"""

from __future__ import annotations

import pytest

from app.core.config import IcaapSettings
from app.icaap.domain.frameworks.schema import Framework
from tests.icaap.domain.test_framework_param_refs import IDS, PUBLISHED, SEEDED

#: Framework codes a deployment publishes out of the box. Nigeria and Kenya
#: ship as reference data and are opt-in (D-046 §4), which is why the seeding
#: assertion below is scoped to what is enabled rather than to everything that
#: parses.
DEFAULT_ENABLED = frozenset(IcaapSettings.model_fields["frameworks_enabled"].default.split(","))
#: Frameworks that are published as data but whose governed rows have not been
#: seeded for their jurisdiction yet, and the codes each still needs. This is a
#: CEILING, asserted with ``<=``: seeding a row makes the set shrink and the
#: test still passes, while a new unseeded reference makes it grow and fails.
#: The rows themselves belong to a parameter catalogue, not to the framework
#: data.
#:
#: **Both sets are now EMPTY.** They held six Nigerian and eight Kenyan codes
#: from the day those frameworks shipped until ``202609200063`` seeded them
#: (``app/icaap/service/parameters.ICAAP_JURISDICTION_SEED_PARAMETERS``). The
#: entries stay, empty, rather than being deleted: an empty set is the record
#: that a framework was checked and found complete, and a framework published
#: with nothing recorded here still fails the assertion below. Shrinking this
#: because a row was seeded is the only legitimate edit; widening it to make a
#: red test green would be hiding exactly what it exists to show.
OUTSTANDING_JURISDICTION_SEEDS: dict[str, frozenset[str]] = {
    "cbn_srp_icaap": frozenset(),
    "cbk_icaap_gn": frozenset(),
}


@pytest.mark.parametrize("framework", PUBLISHED, ids=IDS)
def test_a_framework_the_deployment_publishes_has_its_rows_seeded(
    framework: Framework,
) -> None:
    """The invariant that actually bites: an ENABLED framework must resolve.

    A bank whose framework quotes a code with no row for its jurisdiction gets
    a ``missing_parameter`` refusal rather than a deadline or a threshold —
    correct behaviour (D-024 §4), and useless to that bank. So for every
    framework the deployment publishes by default, every code it quotes is
    seeded for its own jurisdiction.
    """
    if framework.code not in DEFAULT_ENABLED:
        pytest.skip(f"{framework.code} is not published by default (ICAAP_FRAMEWORKS_ENABLED)")
    missing = sorted(
        code
        for code in framework.param_refs()
        if framework.jurisdiction not in SEEDED.get(code, set())
    )
    assert not missing, (
        f"{framework.code} is enabled for {framework.jurisdiction} but these codes have no "
        f"row for that jurisdiction: {missing}"
    )


@pytest.mark.parametrize("framework", PUBLISHED, ids=IDS)
def test_an_opt_in_framework_needs_no_more_rows_than_we_already_know_about(
    framework: Framework,
) -> None:
    """The known seeding gap is pinned so it cannot quietly widen.

    Nigeria and Kenya ship as reference data before their rows exist. That is a
    documented precondition for turning them on, not a surprise — so the set of
    codes each still needs is recorded here as a ceiling. Seeding one shrinks
    the set and this still passes; quoting a new unseeded code grows it and
    this fails.
    """
    if framework.code in DEFAULT_ENABLED:
        pytest.skip(f"{framework.code} is published by default and is checked in full above")
    outstanding = OUTSTANDING_JURISDICTION_SEEDS.get(framework.code)
    assert outstanding is not None, (
        f"{framework.code} is published but not enabled, and nothing records which of its "
        "governed codes still need rows before it can be turned on"
    )
    missing = {
        code
        for code in framework.param_refs()
        if framework.jurisdiction not in SEEDED.get(code, set())
    }
    assert missing <= outstanding, (
        f"{framework.code} now needs rows nobody recorded: {sorted(missing - outstanding)}"
    )
