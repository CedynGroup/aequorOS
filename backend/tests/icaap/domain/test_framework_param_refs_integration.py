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

from app.icaap.domain.frameworks.schema import Framework
from tests.icaap.domain.test_framework_param_refs import (
    DEFAULT_ENABLED,
    ENABLED_FRAMEWORKS,
    ENABLED_IDS,
    OPT_IN_FRAMEWORKS,
    OPT_IN_IDS,
    OUTSTANDING_JURISDICTION_SEEDS,
    SEEDED,
)


@pytest.mark.parametrize("framework", OPT_IN_FRAMEWORKS, ids=OPT_IN_IDS)
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


@pytest.mark.parametrize("framework", ENABLED_FRAMEWORKS, ids=ENABLED_IDS)
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
