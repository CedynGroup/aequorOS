"""Which ICAAP frameworks this deployment publishes.

The domain registry knows how to read framework JSON and nothing about the
environment it is running in. This module is the one place that asks the
environment, so the answer to "may this deployment load an unreviewed
framework?" is given once, against the allow-list, rather than at each of the
registry's call sites.

Why the question exists at all: 15 of the 17 Ghana sections are still
``pending_primary_text`` (D-006), so a non-rehearsal freeze cannot succeed
against the real instrument and P3's filing path would have no end-to-end
proof. ``ICAAP_EXTRA_FRAMEWORKS_DIR`` gives the tests a complete framework to
freeze — and is refused outside ``local``/``test``, because a framework loaded
from an unreviewed directory is indistinguishable from a real one once a cycle
has pinned it.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.icaap.frameworks import registry


def sync_extra_roots() -> None:
    """Install the configured extra framework directories. Idempotent and cheap.

    ``registry.set_extra_roots`` returns immediately when nothing changed, so
    this costs a settings lookup (cached) per call and drops the framework load
    cache only when the configuration actually moves.
    """
    settings = get_settings()
    registry.set_extra_roots(settings.icaap.extra_framework_roots(settings.app.app_env))


__all__ = ["sync_extra_roots"]
