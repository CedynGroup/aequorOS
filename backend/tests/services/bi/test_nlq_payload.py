"""What leaves the process for a question, and — mostly — what does not.

The privacy boundary of the natural-language surface, written as assertions. Two
halves, because the failure modes are different.

**The question is screened BEFORE it is sent.** A question naming the institution, a
former name, a related party, one of the organisation's own users, or any name in the
global jurisdiction registry is refused and never sent; so is one carrying a URL, an
email address, a control character or the ``{{``/``}}`` placeholder channel. That is
reusing the platform's existing screen rather than a second one, and these tests are
what prove the reuse is wired up: the screen has to be able to FIRE.

**The payload carries no tenant value and no date.** Not a balance, not a ratio, not a
branch code, and not the reporting date the reader selected — the model emits an
intent and the platform computes every date, so there is nothing for a date to be
doing in the payload. Asserted as absence, because a payload carrying one thing too
many works perfectly and is discovered by a regulator rather than by a user.

The gap this cannot close is stated in the report: a customer name the tenant's
registers do not hold, typed into a question, is sent as typed.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import Catalogue, catalogue
from app.models import Bank, Organization, User
from app.schemas.bi_nlq import QUESTION_MAX_CHARS
from app.services.bi import nlq
from app.services.bi.nlq import candidates
from tests.support.helpers import ORG_1

BANK_ID = "BK-NLQPAY01"
BANK_NAME = "Nlq Payload Test Bank"
COLLEAGUE_NAME = "Abena Mensah"
AS_OF = date(2026, 8, 31)


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    organization = db_session.get(Organization, ORG_1)
    if organization is not None:
        organization.name = "Nlq Payload Holdings"
    existing = db_session.get(Bank, BANK_ID)
    if existing is None:
        existing = Bank(
            id=BANK_ID,
            organization_id=ORG_1,
            name=BANK_NAME,
            short_name="NlqPay",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type="universal_bank",
        )
        db_session.add(existing)
    db_session.add(
        User(
            organization_id=ORG_1,
            email="abena@example.test",
            display_name=COLLEAGUE_NAME,
        )
    )
    db_session.commit()
    return existing


def _screen(db: Session, bank: Bank, question: str) -> str:
    return nlq.screen_question(db, organization_id=ORG_1, bank=bank, question=question)


def _built(cat: Catalogue, question: str) -> object:
    chosen = candidates.select(
        cat,
        list(cat.members()),
        question=question,
        institution_class="bank",
        capital_regime="crd",
    )
    return nlq.build_payload(chosen, question=question)


# --- the screen fires ---------------------------------------------------------------------


def test_an_ordinary_question_is_sendable(db_session: Session, bank: Bank) -> None:
    """The control: without it every refusal below could pass on a broken screen."""

    assert _screen(db_session, bank, "  gross loans by branch  ") == "gross loans by branch"


@pytest.mark.parametrize(
    "question",
    [
        pytest.param(f"what are {BANK_NAME}'s gross loans", id="institution_name"),
        pytest.param("gross loans for Nlq Payload Holdings", id="organization_name"),
        pytest.param(f"which branch does {COLLEAGUE_NAME} run", id="colleague_name"),
        pytest.param("show Ghana Cedi exposures", id="jurisdiction_currency_name"),
        pytest.param("email it to me at x@example.test", id="email"),
        pytest.param("fetch https://example.test/report", id="url"),
        pytest.param("gross loans {{E:bank}}", id="placeholder_channel"),
        pytest.param("gross\x00loans", id="control_character"),
    ],
)
def test_a_question_that_may_not_leave_the_platform_is_refused(
    db_session: Session, bank: Bank, question: str
) -> None:
    with pytest.raises(nlq.QuestionWithheld) as refused:
        _screen(db_session, bank, question)
    assert refused.value.code == "question_withheld"
    assert refused.value.message


def test_an_empty_and_an_overlong_question_get_their_own_fixable_message(
    db_session: Session, bank: Bank
) -> None:
    """Three refusals, not one: a reader who typed too much can fix that."""

    with pytest.raises(nlq.QuestionWithheld) as empty:
        _screen(db_session, bank, "   ")
    assert empty.value.code == "question_empty"
    with pytest.raises(nlq.QuestionWithheld) as long:
        _screen(db_session, bank, "loans " * QUESTION_MAX_CHARS)
    assert long.value.code == "question_too_long"


def test_a_long_question_is_still_screened_for_content(db_session: Session, bank: Bank) -> None:
    """The screen's own length bound is a table label's (80 chars), not a sentence's.

    A question of 200 characters must still be screened, or the deny terms would stop
    applying to exactly the questions long enough to hide one.
    """

    padding = "gross loans by branch and product family " * 4
    question = f"{padding}{BANK_NAME}"
    assert len(question) > 80
    assert len(question) <= QUESTION_MAX_CHARS
    with pytest.raises(nlq.QuestionWithheld):
        _screen(db_session, bank, question)


# --- the payload carries no value and no date ---------------------------------------------


def test_the_payload_carries_no_date_at_all(cat: Catalogue) -> None:
    """The structural reason the model can never name a reporting date."""

    body = json.dumps(_built(cat, "gross loans by branch").payload)  # type: ignore[attr-defined]
    for token in (AS_OF.isoformat(), "2026-08-31", "2026", str(AS_OF.year)):
        assert token not in body


def test_the_payload_carries_only_catalogue_metadata(cat: Catalogue) -> None:
    """Ids, labels, descriptions and declared vocabularies — all static code.

    Every string in the payload is either the question or comes from a catalogue
    member, so there is nowhere for a tenant figure to be.
    """

    built = _built(cat, "gross loans by branch")
    payload = built.payload  # type: ignore[attr-defined]
    assert set(payload) == {"question", "figures", "groupings", "drill_paths"}
    known = {member.id for member in cat.members()}
    assert {figure["id"] for figure in payload["figures"]} <= known
    assert {grouping["id"] for grouping in payload["groupings"]} <= known
    labels = {member.label for member in cat.members()}
    assert {figure["name"] for figure in payload["figures"]} <= labels


def test_a_grouping_the_figure_cannot_take_is_not_advertised_against_it(cat: Catalogue) -> None:
    """``can_be_grouped_by`` is intersected with what is OFFERED, so the model is
    never shown a grouping it would then be refused for naming."""

    built = _built(cat, "gross loans by branch")
    offered = {grouping["id"] for grouping in built.payload["groupings"]}  # type: ignore[attr-defined]
    for figure in built.payload["figures"]:  # type: ignore[attr-defined]
        assert set(figure["can_be_grouped_by"]) <= offered
        declared = set(cat.measure(figure["id"]).allowed_dimensions)
        assert set(figure["can_be_grouped_by"]) <= declared


def test_the_digest_is_value_based(cat: Catalogue) -> None:
    """Two equal payloads hash alike, and a changed question does not.

    The worker re-hashes before it calls the model, so this is what makes "a queued
    question can never send something other than what was frozen" true.
    """

    first = _built(cat, "gross loans by branch")
    again = _built(cat, "gross loans by branch")
    other = _built(cat, "gross loans by product family")
    assert first.sha256 == again.sha256  # type: ignore[attr-defined]
    assert first.sha256 != other.sha256  # type: ignore[attr-defined]
    assert nlq.payload_digest(first.payload) == first.sha256  # type: ignore[attr-defined]


def test_the_offered_ids_are_exactly_what_the_payload_names(cat: Catalogue) -> None:
    """The frozen id set is what the worker checks the model's answer against, so it
    must not be able to drift from what was actually shown."""

    built = _built(cat, "gross loans by branch")
    payload = built.payload  # type: ignore[attr-defined]
    named = {figure["id"] for figure in payload["figures"]} | {
        grouping["id"] for grouping in payload["groupings"]
    }
    assert set(built.offered_member_ids) == named  # type: ignore[attr-defined]
