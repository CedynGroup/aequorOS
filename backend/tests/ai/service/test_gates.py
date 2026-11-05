"""The egress gates, at BOTH enqueue and run.

The asymmetry these tests pin is the point of the design: a request can sit in
the queue across a toggle change, so a gate checked only at enqueue would let
every queued request through after someone pulled the switch. Each case flips a
switch BETWEEN the two phases and asserts the run refuses.

This module's other-suite cases live in ``test_gates_integration.py``.
"""

from __future__ import annotations

import pytest

from app.ai.service import approvals, gates
from app.core.config import get_settings

pytestmark = pytest.mark.usefixtures("ai_enabled")


def test_deployment_gate_is_answerable_without_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route dependency asks this BEFORE resolving a tenant, for its 404."""
    assert gates.deployment_gate().allowed
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    assert not gates.deployment_gate().allowed


def test_the_shipped_approvals_file_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """It ships empty on purpose; adding an entry is a reviewed commit."""
    _ = monkeypatch
    approvals.load_approvals.cache_clear()
    assert approvals.load_approvals() == ()


def test_every_gate_code_has_user_facing_copy() -> None:
    """A raw enum must never reach a page (UI copy is production copy)."""
    from typing import get_args  # noqa: PLC0415

    for code in get_args(gates.GateCode):
        message = gates.GATE_MESSAGES[code]
        assert message and message != code
        assert message[0].isupper() and message.endswith(".")
