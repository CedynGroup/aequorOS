"""What leaves the process, and — mostly — what does not.

This file is the privacy boundary of the BI commentary feature written down as
assertions. Every test here is of the form "this string is NOT in the payload",
because the failure mode is silent: a payload that carries one figure too many
works perfectly and is discovered by a regulator, not by a user.

The five classes asserted absent, in both modes:

* the institution's, organisation's and users' names, and every registry name
  (country, currency, regulator, central bank) — those are ``{{E:key}}`` keys;
* every identifier: the bank's platform id, the fact ids, the mart build;
* every DATE, including the reporting date and the period compared against;
* every MONETARY AMOUNT, whatever the mode says;
* the platform's own insight sentences, which carry rendered figures.

Plus the mode rule itself: descriptor-only sends no figure at all, and it is the
mode a tenant that has made no choice gets.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import catalogue
from app.domain.bi.catalogue.members import VALUE_TYPES
from app.models import Bank, Organization, User
from app.services.attestation.digests import canonical_json
from app.services.bi.commentary import payload as payload_module
from app.services.bi.commentary import prompt as prompt_module
from app.services.bi.insights import facts, rules
from tests.support.helpers import ORG_1

AS_OF = date(2026, 6, 30)
PRIOR = date(2026, 5, 31)
BANK_ID = "BK-COMM0001"
BANK_NAME = "Commentary Test Bank"

#: A filed, certified ratio — the figure a standard-mode request may carry.
RATIO = "engine.car_pct.crd.official"
#: A monetary amount — never carried, in either mode.
AMOUNT = "engine.total_capital_ghs.crd.official"
#: A supervisory-monitoring ratio: analysis, not a filed line.
ADVISORY = "engine.par_90_pct.crd.official"

#: The prompt digests as committed. A wording change without a version bump fails
#: here rather than silently invalidating every tenant's prompt cache and every
#: approved configuration (the version is part of the approval key).
STATIC_PROMPT_SHA256 = "0814e288d15ea6172c385d746f38db420fd93e2ae86f5c4e5745587ace216076"
MODE_PROMPT_SHA256 = {
    "descriptor_only": "0cb8df7f4381d4de38514326e0a3e971090c9cac86511254df4f217e3a2767c0",
    "standard": "4eaa9b826d2ad6e0a54a1a7504eb98436f1640e96bd6937a04c643c7cec106b0",
}


@pytest.fixture
def bank(db_session: Session) -> Bank:
    existing = db_session.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    row = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name=BANK_NAME,
        short_name="Commentary",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _provenance() -> facts.FactProvenance:
    return facts.FactProvenance(fact_id=uuid4(), derived_at=datetime.now(tz=UTC), build_id=uuid4())


def _movement(
    measure_id: str,
    prior: str | None,
    current: str | None,
    *,
    missing_reason: facts.MissingReason | None = None,
    scope: facts.FactScope = facts.WHOLE_INSTITUTION,
) -> facts.MovementFact:
    return facts.movement_fact(
        catalogue().measure(measure_id),
        as_of=AS_OF,
        prior_as_of=PRIOR,
        provenance=_provenance(),
        prior=None if prior is None else Decimal(prior),
        current=None if current is None else Decimal(current),
        missing_reason=missing_reason,
        scope=scope,
    )


def _sheet(*items: facts.Fact) -> facts.FactSheet:
    return facts.fact_sheet(
        institution_id=BANK_ID,
        as_of=AS_OF,
        catalogue_version=catalogue().version,
        facts=items,
        generated_at=datetime.now(tz=UTC),
    )


def _build(
    db: Session, bank_row: Bank, sheet: facts.FactSheet, *, descriptor_only: bool
) -> payload_module.CommentaryBuild:
    return payload_module.build_payload(
        db,
        bank=bank_row,
        sheet=sheet,
        insight_set=rules.derive_insights(sheet),
        descriptor_only=descriptor_only,
    )


def _text(build: payload_module.CommentaryBuild) -> str:
    """The payload exactly as it would be sent, as one searchable string."""
    return canonical_json(build.payload)


def _figures(build: payload_module.CommentaryBuild) -> list[dict[str, Any]]:
    return [figure for entry in build.payload["facts"] for figure in entry.get("figures", [])]


def _entry_for(build: payload_module.CommentaryBuild, measure_id: str) -> dict[str, Any]:
    """The payload entry for one measure, found by the label the catalogue gave it."""
    label = catalogue().measure(measure_id).label
    matches = [entry for entry in build.payload["facts"] if entry["label"] == label]
    assert len(matches) == 1, f"expected exactly one entry labelled {label!r}"
    return matches[0]


# --- the mode ----------------------------------------------------------------


def test_descriptor_only_sends_no_figure_at_all(db_session: Session, bank: Bank) -> None:
    """The DEFAULT posture. Every figure is placed, none is disclosed."""
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"))
    build = _build(db_session, bank, sheet, descriptor_only=True)

    assert build.mode == "descriptor_only"
    offers = _figures(build)
    assert offers, "the model must still be offered placeholders to point at"
    assert all(figure.get("value_withheld") is True for figure in offers)
    assert all("value" not in figure for figure in offers)
    body = _text(build)
    for figure in ("12.10", "13.40", "12.1", "13.4", "1.30", "1.3"):
        assert figure not in body


def test_descriptor_only_still_says_everything_worth_saying(
    db_session: Session, bank: Bank
) -> None:
    """A withheld figure is not a withheld MEANING: the descriptors carry it."""
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"))
    build = _build(db_session, bank, sheet, descriptor_only=True)

    entry = build.payload["facts"][0]
    assert entry["descriptors"]["moved"] == "higher"
    assert entry["descriptors"]["size"] == "material"
    assert entry["descriptors"]["assessment"] == "favourable"
    assert "data_trust" not in entry["descriptors"]
    assert entry["descriptors"]["certified"] is True
    assert entry["label"] == catalogue().measure(RATIO).label


def test_standard_mode_carries_a_ratio_and_never_an_amount(db_session: Session, bank: Bank) -> None:
    """The opt-in sends ratios. An amount is an institution's size, so it stays."""
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"), _movement(AMOUNT, "900", "1100"))
    build = _build(db_session, bank, sheet, descriptor_only=False)

    assert build.mode == "standard"
    ratio_entry = _entry_for(build, RATIO)
    amount_entry = _entry_for(build, AMOUNT)
    assert all("value" in figure for figure in ratio_entry["figures"])
    assert all(figure.get("value_withheld") is True for figure in amount_entry["figures"])
    assert all("value" not in figure for figure in amount_entry["figures"])

    body = _text(build)
    # The ratio's own figure is present, in the precision the fact holds it.
    assert "13.4 %" in body
    for amount in ("1100", "900", "200", "in the reporting currency"):
        assert amount not in body


def test_amount_bindings_still_resolve_for_the_reader(db_session: Session, bank: Bank) -> None:
    """Withholding a value from the MODEL never withholds it from the bank."""
    sheet = _sheet(_movement(AMOUNT, "900", "1100"))
    build = _build(db_session, bank, sheet, descriptor_only=True)

    displays = {binding.display for binding in build.bindings.values()}
    assert any("1100" in display for display in displays)


# --- what is never in the payload -------------------------------------------


def test_no_name_no_identifier_and_no_date_travels(db_session: Session, bank: Bank) -> None:
    organization = db_session.get(Organization, ORG_1)
    assert organization is not None
    users = [
        name
        for name in db_session.scalars(User.__table__.select().with_only_columns(User.display_name))
        if name
    ]
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"))
    build = _build(db_session, bank, sheet, descriptor_only=False)
    body = _text(build)

    for forbidden in (
        BANK_NAME,
        "Commentary",
        organization.name,
        bank.id,
        "GHS",
        "Ghana",
        "Bank of Ghana",
        "BoG",
        AS_OF.isoformat(),
        PRIOR.isoformat(),
        str(AS_OF.year),
        sheet.institution_id,
        catalogue().measure(RATIO).id,
    ):
        assert forbidden not in body, f"{forbidden!r} must never leave the process"
    for name in users:
        assert str(name) not in body
    # The entity list offers KEYS and ROLES, never a value.
    assert build.payload["entities"]
    for offer in build.payload["entities"]:
        assert set(offer) == {"key", "role"}


def test_the_platforms_own_sentences_never_travel(db_session: Session, bank: Bank) -> None:
    """``Insight.detail`` carries rendered figures, so it is exactly what must not
    be sent — not even in descriptor-only mode, where it would be a back door."""
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"), _movement(ADVISORY, "4.10", "7.90"))
    insight_set = rules.derive_insights(sheet)
    assert insight_set.insights, "the sheet must produce statements for this to prove anything"
    build = payload_module.build_payload(
        db_session,
        bank=bank,
        sheet=sheet,
        insight_set=insight_set,
        descriptor_only=True,
    )
    body = _text(build)
    for insight in insight_set.insights:
        assert insight.headline not in body
        assert insight.detail not in body
        for qualifier in insight.qualifiers:
            assert qualifier not in body
    # What DOES travel is the shape of the judgement.
    assert build.payload["priorities"]
    for priority in build.payload["priorities"]:
        assert set(priority) == {"class", "facts", "assessment", "emphasis"}


def test_a_fact_about_a_named_slice_of_the_book_never_travels(
    db_session: Session, bank: Bank
) -> None:
    """A branch or product name is tenant data with no entity key, so the fact is
    dropped rather than described."""
    branch = facts.FactScope(
        dimension_id="position.branch", value_code="BR-01", value_label="Kaneshie Branch"
    )
    sheet = _sheet(
        _movement(RATIO, "12.10", "13.40"),
        _movement(ADVISORY, "4.10", "7.90", scope=branch),
    )
    build = _build(db_session, bank, sheet, descriptor_only=True)

    assert build.scoped_facts_withheld == 1
    assert build.fact_count == 1
    body = _text(build)
    assert "Kaneshie" not in body
    assert "BR-01" not in body


def test_a_sheet_with_nothing_quotable_is_refused_rather_than_sent(
    db_session: Session, bank: Bank
) -> None:
    """No figure means no grounded paragraph. The caller serves the deterministic
    commentary instead; nothing is sent and no row is written."""
    sheet = _sheet(_movement(RATIO, None, None, missing_reason="not_supplied"))
    with pytest.raises(payload_module.NoCommentableFactsError):
        _build(db_session, bank, sheet, descriptor_only=True)


def test_a_missing_figure_is_carried_as_a_gap_and_never_as_a_zero(
    db_session: Session, bank: Bank
) -> None:
    sheet = _sheet(
        _movement(RATIO, "12.10", "13.40"),
        _movement(ADVISORY, None, None, missing_reason="not_supplied"),
    )
    build = _build(db_session, bank, sheet, descriptor_only=True)

    gaps = [entry for entry in build.payload["facts"] if entry["available"] is False]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["unavailable_reason"] == "not_supplied"
    assert "figures" not in gap
    assert gap["descriptors"]["moved"] == "not_assessed"
    assert gap["descriptors"]["size"] == "not_assessed"


# --- the rules the mode rests on --------------------------------------------


def test_every_value_type_is_either_sendable_or_withheld() -> None:
    """A new value type is WITHHELD until somebody decides otherwise."""
    assert not payload_module.VALUE_BEARING_TYPES & payload_module.WITHHELD_TYPES
    assert set(VALUE_TYPES) == (payload_module.VALUE_BEARING_TYPES | payload_module.WITHHELD_TYPES)
    assert "amount" in payload_module.WITHHELD_TYPES
    assert "date" in payload_module.WITHHELD_TYPES


def test_the_digest_is_value_based(db_session: Session, bank: Bank) -> None:
    """Two requests over the same figures hash the same; a changed figure does not.

    The live plane re-derives its facts on every refresh, minting new fact ids and
    a new timestamp over figures that have not moved. A digest that moved with them
    could not answer the only question asked of it — is this what was sent?
    """
    first = _build(
        db_session, bank, _sheet(_movement(RATIO, "12.10", "13.40")), descriptor_only=False
    )
    again = _build(
        db_session, bank, _sheet(_movement(RATIO, "12.10", "13.40")), descriptor_only=False
    )
    changed = _build(
        db_session, bank, _sheet(_movement(RATIO, "12.10", "13.50")), descriptor_only=False
    )

    assert first.sha256 == again.sha256
    assert first.sha256 != changed.sha256
    assert payload_module.payload_digest(first.payload) == first.sha256


def test_the_mode_changes_the_digest(db_session: Session, bank: Bank) -> None:
    """Two different requests were made, and the record has to say which."""
    sheet = _sheet(_movement(RATIO, "12.10", "13.40"))
    strict = _build(db_session, bank, sheet, descriptor_only=True)
    opted_in = _build(db_session, bank, sheet, descriptor_only=False)
    assert strict.sha256 != opted_in.sha256


# --- the prompt --------------------------------------------------------------


def test_the_prompt_puts_the_payload_last_and_caches_both_system_blocks() -> None:
    """Order is the caching strategy: the volatile part cannot disturb the prefix."""
    request = prompt_module.build_request({"schema": "x"}, "descriptor_only")

    assert request.feature == "bi_commentary"
    assert request.prompt_version == prompt_module.PROMPT_VERSION
    assert len(request.system) == 2
    assert all(block.cache for block in request.system)
    assert request.system[0].text == prompt_module.STATIC_SYSTEM_PROMPT
    assert request.system[1].text == prompt_module.MODE_PROMPTS["descriptor_only"]
    assert request.user_content == '{"schema":"x"}'


def test_the_prompt_version_is_pinned_to_the_prompt_text() -> None:
    assert prompt_module.PROMPT_VERSION == "bi-commentary-v2"
    assert prompt_module.static_prompt_sha256() == STATIC_PROMPT_SHA256
    for mode, digest in MODE_PROMPT_SHA256.items():
        assert prompt_module.prompt_digest(mode) == digest


def test_the_static_prompt_forbids_the_things_the_validator_refuses() -> None:
    """The prompt and the grounding rules must agree, or the feature fails
    validation on every request and silently serves only the fallback."""
    text = prompt_module.STATIC_SYSTEM_PROMPT
    for rule in ("Never write a digit", "{{F:<id>}}", "{{E:<key>}}", "Never name a person"):
        assert rule in text
    assert "recommend" in text  # the advice prohibition is stated
    for mode_text in prompt_module.MODE_PROMPTS.values():
        assert "{{F:<id>}}" in mode_text
