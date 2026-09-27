"""The deterministic commentary, tested as the product it is.

The fallback is what a reader gets whenever the model path produces nothing
usable — the switch is off, the tenant has not consented, the vendor refused, the
prose failed grounding — and the reader is not meant to be short-changed by that.
So this file holds it to the same standard as the model path:

* it reads as commentary, not as an error message: no "unavailable", no "failed",
  no apology, and no mention of AI at all;
* it never states a figure the facts do not carry, and never calls a missing
  figure zero or flat;
* it says which of the two empty answers is true — "nothing moved" and "nothing
  has been computed" are different facts about a bank;
* it names no currency, regulator or country literal, because the copy it is
  composed from does not.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.domain.bi.catalogue import catalogue
from app.services.bi.commentary import deterministic_commentary
from app.services.bi.insights import facts, rules

AS_OF = date(2026, 6, 30)
PRIOR = date(2026, 5, 31)
BANK_NAME = "Commentary Test Bank"
_ALL_GREEN: dict[str, str] = dict.fromkeys(("R8", "R9", "R10"), "green")

RATIO = "engine.car_pct.crd.official"
ADVISORY = "engine.par_90_pct.crd.official"
UNRECONCILED = "engine.worst_eve_change_pct_tier1.crd.live"

#: Words that would tell a reader the machine failed. None of them belongs in
#: commentary the platform wrote on purpose.
_APOLOGY_WORDS = (
    "unavailable",
    "failed",
    "error",
    "sorry",
    "could not generate",
    "AI",
    "model",
)


def _provenance() -> facts.FactProvenance:
    return facts.FactProvenance(fact_id=uuid4(), derived_at=datetime.now(tz=UTC), build_id=uuid4())


def _movement(  # noqa: PLR0913 - one measure, two figures and the two badges
    measure_id: str,
    prior: str | None,
    current: str | None,
    *,
    missing_reason: facts.MissingReason | None = None,
    statuses: dict[str, str] | None = None,
    build_overall: str = "green",
) -> facts.MovementFact:
    return facts.movement_fact(
        catalogue().measure(measure_id),
        as_of=AS_OF,
        prior_as_of=PRIOR,
        provenance=_provenance(),
        statuses=_ALL_GREEN if statuses is None else statuses,
        build_overall=build_overall,
        prior=None if prior is None else Decimal(prior),
        current=None if current is None else Decimal(current),
        missing_reason=missing_reason,
    )


def _sheet(*items: facts.Fact) -> facts.FactSheet:
    return facts.fact_sheet(
        institution_id="BK-COMM0001",
        as_of=AS_OF,
        catalogue_version=catalogue().version,
        facts=items,
        generated_at=datetime.now(tz=UTC),
    )


def _commentary(sheet: facts.FactSheet) -> tuple[str, ...]:
    return deterministic_commentary(
        sheet=sheet,
        insight_set=rules.derive_insights(sheet),
        institution_name=BANK_NAME,
        compare_to=PRIOR,
    )


def test_it_opens_by_naming_the_institution_and_both_dates() -> None:
    paragraphs = _commentary(_sheet(_movement(RATIO, "12.10", "13.40")))

    assert paragraphs
    opening = paragraphs[0]
    assert BANK_NAME in opening
    assert AS_OF.isoformat() in opening
    assert PRIOR.isoformat() in opening


def test_it_reports_a_material_move_with_the_platforms_own_figure() -> None:
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"))
    paragraphs = _commentary(sheet)
    body = " ".join(paragraphs)

    assert "What moved" in body
    # The figure is the platform's, in the precision the fact holds it.
    assert "13.4" in body
    assert len(paragraphs) >= 2


def test_it_never_reads_like_an_error() -> None:
    sheet = _sheet(
        _movement(RATIO, "12.10", "13.40"),
        _movement(ADVISORY, None, None, missing_reason="not_supplied"),
        _movement(UNRECONCILED, "1.10", "1.90", build_overall="amber"),
    )
    body = " ".join(_commentary(sheet))

    for word in _APOLOGY_WORDS:
        # Whole words only: "ai" lives inside "against" and "available", and a
        # substring scan would convict honest prose instead of an apology.
        pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
        assert not pattern.search(body), f"the fallback must not mention {word!r}"


def test_a_missing_figure_is_stated_as_missing_and_never_as_zero() -> None:
    sheet = _sheet(
        _movement(RATIO, "12.10", "13.40"),
        _movement(ADVISORY, None, None, missing_reason="not_supplied"),
    )
    body = " ".join(_commentary(sheet))

    assert "What the figures do not show" in body
    assert "It is not zero, and it has not stayed flat." in body


def test_an_unreconciled_book_is_said_to_be_unreconciled() -> None:
    sheet = _sheet(_movement(RATIO, "12.10", "13.40", build_overall="grey"))
    body = " ".join(_commentary(sheet))

    assert "How far these figures are confirmed" in body
    assert "not checked is not the same as checked and correct" in body.casefold()


def test_an_advisory_figure_is_qualified_once_rather_than_per_sentence() -> None:
    sheet = _sheet(_movement(ADVISORY, "4.10", "7.90"), _movement(ADVISORY, "4.10", "7.90"))
    paragraphs = _commentary(sheet)
    body = " ".join(paragraphs)
    qualifier = "This figure is monitored by the supervisor but is not a filed return line."

    assert qualifier in body
    assert body.count(qualifier) == 1


def test_nothing_moved_and_nothing_computed_are_different_answers() -> None:
    quiet = _commentary(_sheet(_movement(RATIO, "13.40", "13.40")))
    empty = _commentary(_sheet())

    assert "None of these figures moved by enough" in " ".join(quiet)
    assert "No headline figure has been computed" in " ".join(empty)
    assert quiet != empty


def test_it_is_a_pure_function_of_the_sheet() -> None:
    """Same figures, same commentary — which is what lets it be written to the row
    before the model is called and served whatever happens next."""
    first = _commentary(_sheet(_movement(RATIO, "12.10", "13.40")))
    again = _commentary(_sheet(_movement(RATIO, "12.10", "13.40")))
    assert first == again


def test_it_names_no_currency_regulator_or_country() -> None:
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"), _movement(ADVISORY, "4.10", "7.90"))
    body = " ".join(_commentary(sheet))

    for literal in ("GHS", "cedi", "Ghana", "Bank of Ghana", "BoG", "$", "₵"):
        assert literal not in body
