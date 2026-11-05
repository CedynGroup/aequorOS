"""``app.bi.service.subscriptions``: whose authority, which minute, and how many.

The three properties this suite exists for, in the order they matter:

1. **A delivery carries the RECIPIENT's authority, not the owner's.**
   :func:`test_a_delivery_is_rendered_as_the_recipient_and_not_as_the_owner` is
   the test the whole feature rests on: one owner who can see everything, two
   recipients who cannot see the same things, and the assertion that the narrow
   recipient's mailbox holds nothing at all while the broad one's artifact is
   attributed to THEM. The artifact is CSV so the attribution is inspectable in
   the bytes — the provenance block's "Exported by" field is the recipient's own
   address, which is only true if the render happened under their context.
2. **Confidential content is never attached.** The decision is the export
   policy's, taken from the catalogue's own sensitivity declarations BEFORE a row
   is read — so a record-level subscription has no query log row at all, which is
   the observable form of "the rows never left the database".
3. **A run cannot send twice.** Proved three ways: a second call to the same run
   sends nothing, a second enqueue of the same minute queues nothing, and the
   delivery row's unique key refuses the duplicate at the database
   (``tests/bi/models/test_bi_notification_models.py``).

**No socket is ever opened.** ``smtplib.SMTP`` is replaced by a fake that records
the messages, exactly as the notification mirror's own suite does it, and the real
``app/services/mailer.py`` path is exercised through it.

This module's other-suite cases live in ``test_subscriptions_integration.py``.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services import scheduler


def test_the_subscriptions_flag_alone_keeps_the_tick_chain_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hazard this project has already paid for once.

    A scheduled branch whose flag is absent from ``any_scheduling_enabled`` never
    runs: the tick short-circuits as inert and does not even reschedule itself,
    so the feature is silently dead on a deployment that enabled only it.
    """

    monkeypatch.setenv("BI_SUBSCRIPTIONS_ENABLED", "1")
    get_settings.cache_clear()
    assert scheduler.any_scheduling_enabled(get_settings()) is True
