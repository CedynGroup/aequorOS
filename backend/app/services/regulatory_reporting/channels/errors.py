"""Typed submission-channel errors (docs/regulatory_reporting.md §5).

Mirrors the market-data adapter's ``MarketDataError`` discipline
(``app/adapters/market_data/errors.py``): ``operator_message`` is the only
part that may reach an operator-facing surface; ``internal_detail`` carries
diagnostic context for AequorOS engineering logs and is never rendered to a
bank. ``str(error)`` deliberately renders only the operator message so an
accidentally surfaced exception cannot leak channel internals.
"""

from __future__ import annotations


class ChannelError(Exception):
    """Base for all submission-channel failures."""

    def __init__(self, operator_message: str, internal_detail: str = "") -> None:
        super().__init__(operator_message)
        self.operator_message = operator_message
        self.internal_detail = internal_detail


class ChannelPreconditionError(ChannelError):
    """The package is not in a submittable state for this channel."""


class ChannelDowntimeError(ChannelError):
    """The channel is configured/observed as down.

    Per BoG Notice BG/FMD/2026/07 the operator must fall back to the guided
    email workflow, then re-upload to ORASS once system functionality is
    restored for the submission to be deemed complete
    (docs/research/bog_orass_submission_channels.md §2.3).
    """
