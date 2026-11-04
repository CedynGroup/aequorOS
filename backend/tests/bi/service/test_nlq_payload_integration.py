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

This module's other-suite cases live in ``test_nlq_payload.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.bi.schemas.nlq import QUESTION_MAX_CHARS
from app.bi.service import nlq
from app.models import Bank
from tests.bi.service.test_nlq_payload import BANK_NAME, COLLEAGUE_NAME
from tests.bi.service.test_nlq_payload import (
    bank as bank,  # noqa: PLC0414 - shared with the split module
)
from tests.support.helpers import ORG_1


def _screen(db: Session, bank: Bank, question: str) -> str:
    return nlq.screen_question(db, organization_id=ORG_1, bank=bank, question=question)


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
