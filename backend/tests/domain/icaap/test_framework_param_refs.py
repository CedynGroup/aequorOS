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
"""

from __future__ import annotations

import re

import pytest

from app.core.config import IcaapSettings
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework
from app.domain.policy import parameter_shapes
from app.services.icaap.parameters import (
    jurisdiction_seed_rows as workspace_jurisdiction_seed_rows,
)
from app.services.icaap.parameters import seed_rows as workspace_seed_rows
from app.services.regulatory_parameters import filing_seed_rows, p2_seed_rows, p5_seed_rows
from app.services.regulatory_parameters import seed_rows as core_seed_rows

PUBLISHED = list(registry.load_all())
IDS = [f"{framework.code} {framework.version}" for framework in PUBLISHED]

_PLACEHOLDER = re.compile(r"\{param:[a-z][a-z0-9_]{2,60}\}")
_DIGIT = re.compile(r"\d")

#: Basel and BoG nomenclature in which a digit is part of a NAME, not a value.
#: "Pillar 2 capital" is what the capital is called; "at least 3 years" is a
#: rule somebody must be able to change. Removed before the digit scan so that
#: the scan can then be absolute rather than a list of tolerated numbers.
_NOMENCLATURE = re.compile(
    r"\b(?:Pillar\s+[12]|Tier\s+[12]|CET\s?1|AT\s?1|Basel\s+(?:I{1,3}|IV))\b"
)

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
#: (``app/services/icaap/parameters.ICAAP_JURISDICTION_SEED_PARAMETERS``). The
#: entries stay, empty, rather than being deleted: an empty set is the record
#: that a framework was checked and found complete, and a framework published
#: with nothing recorded here still fails the assertion below. Shrinking this
#: because a row was seeded is the only legitimate edit; widening it to make a
#: red test green would be hiding exactly what it exists to show.
OUTSTANDING_JURISDICTION_SEEDS: dict[str, frozenset[str]] = {
    "cbn_srp_icaap": frozenset(),
    "cbk_icaap_gn": frozenset(),
}


def _seeded_jurisdictions() -> dict[str, set[str]]:
    """param_code -> the jurisdictions an approved seed row exists for."""
    found: dict[str, set[str]] = {}
    for row in (
        *workspace_seed_rows(),
        *workspace_jurisdiction_seed_rows(),
        *p2_seed_rows(),
        *filing_seed_rows(),
        *p5_seed_rows(),
        *core_seed_rows(),
    ):
        found.setdefault(str(row["param_code"]), set()).add(str(row["jurisdiction_code"]))
    return found


SEEDED = _seeded_jurisdictions()


def test_at_least_one_framework_is_published() -> None:
    assert PUBLISHED


@pytest.mark.parametrize("framework", PUBLISHED, ids=IDS)
def test_every_placeholder_is_declared_by_its_item(framework: Framework) -> None:
    for item in framework.all_items():
        assert set(item.placeholders) <= set(item.param_refs), item.id
        for code in item.param_refs:
            assert code in framework.param_refs()


@pytest.mark.parametrize("framework", PUBLISHED, ids=IDS)
def test_every_code_the_framework_quotes_is_editable_in_the_console(
    framework: Framework,
) -> None:
    """A code with no registered shape cannot be proposed or approved.

    That would leave the figure frozen at whatever the seed said, which is the
    state D-024 exists to prevent.
    """
    for code in sorted(framework.param_refs()):
        assert code in parameter_shapes.SHAPES, (
            f"{framework.code} quotes {code}, which has no shape and so cannot be "
            "edited in the operator console"
        )


@pytest.mark.parametrize("framework", PUBLISHED, ids=IDS)
def test_no_requirement_text_carries_a_figure_of_its_own(framework: Framework) -> None:
    """After the placeholders and the Basel names, no digit may remain.

    Deliberately absolute rather than "no digit equal to a governed value":
    the value in the console changes, the paraphrase does not, so a figure that
    merely differs from today's row is the same defect one release later.
    """
    for item in framework.all_items():
        stripped = _NOMENCLATURE.sub("", _PLACEHOLDER.sub("", item.text))
        found = _DIGIT.findall(stripped)
        assert not found, (
            f"{framework.code}/{item.id} writes a figure into its text: {item.text!r}. "
            "Reference a governed parameter instead."
        )


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


@pytest.mark.parametrize("framework", PUBLISHED, ids=IDS)
def test_every_published_framework_has_a_row_for_every_code_it_quotes(
    framework: Framework,
) -> None:
    """The positive form, over EVERY framework rather than the enabled ones.

    The two tests above are a pair of halves: one checks the frameworks the
    deployment publishes by default in full, the other holds the known gap in
    the opt-in ones to a recorded ceiling. With that ceiling now empty they say
    the same thing, so this states it once, positively, over the whole set —
    and it keeps saying it if someone later widens the ceiling or changes what
    is enabled by default. A row deleted from any catalogue fails here first.
    """
    missing = sorted(
        code
        for code in framework.param_refs()
        if framework.jurisdiction not in SEEDED.get(code, set())
    )
    assert not missing, (
        f"{framework.code} ({framework.jurisdiction}) quotes {missing}, which no catalogue "
        f"seeds for {framework.jurisdiction}. A bank there gets a missing_parameter refusal "
        "instead of its deadline, its review intervals or its materiality matrix."
    )
