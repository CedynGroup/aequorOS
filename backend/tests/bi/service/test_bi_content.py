"""The rules behind saved dashboards and calculated measures.

Each test here is a property the product depends on, not a shape the code happens
to produce:

* **a shared dashboard carries no authority.** ``resolve_widgets`` is not given
  the owner and cannot consult them: the same canvas resolves to figures for a
  broad reader and to refusal markers for a narrow one, and the refused widget
  carries no query at all.
* **the four visibility rules are four rules.** Private reaches nobody else;
  named sharing reaches exactly the named; role sharing reaches the holders of
  that binding over this institution; organization sharing reaches this tenant's
  identities with institution coverage and — structurally — nobody outside the
  tenant.
* **a formula is the server's to read.** The stored ``referenced_members`` column
  is a recorded copy for audit, and the authorization walk re-derives the ids from
  the TEXT: a doctored column cannot widen what a measure discloses.
* **a promotion is maker-checker, and the approval is frozen at a formula.**
  Proposer ≠ approver through the platform's own policy types; an edit after
  approval clears the certification; a decision against a moved formula refuses.

This module's other-suite cases live in ``test_bi_content_integration.py``.
"""

from __future__ import annotations

from app.bi.service import content
from app.identity.service import grant_administration
from tests.bi.service.test_bi_content_integration import VIEWER
from tests.support.helpers import USER_1


def test_the_sod_verdict_is_the_platforms_own_shape() -> None:
    """Reused, not reinvented: the surfaces that display an SoD decision already
    read this shape."""

    allow = content.promotion_sod_decision(proposed_by_user_id=USER_1, approver_user_id=VIEWER)
    block = content.promotion_sod_decision(proposed_by_user_id=USER_1, approver_user_id=USER_1)

    assert isinstance(allow, grant_administration.SodDecision)
    assert allow.outcome is grant_administration.SodOutcome.ALLOW
    assert block.outcome is grant_administration.SodOutcome.BLOCK
    assert isinstance(block.findings[0], grant_administration.SodFinding)
