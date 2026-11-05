"""Creating, listing, retiring and re-basing an ICAAP cycle.

This module's other-suite cases live in ``test_cycles_integration.py``.
"""

from __future__ import annotations

from app.icaap.service import cycles


def test_the_block_catalogue_marks_later_phase_blocks_unavailable() -> None:
    listed = cycles.list_block_types()
    available = {entry.type for entry in listed.block_types if entry.available}
    unavailable = {entry.type for entry in listed.block_types if not entry.available}
    assert {"capital_position", "risk_register", "irrbb_sf"} <= available
    assert {"workflow_summary"} <= unavailable
