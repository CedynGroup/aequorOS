from __future__ import annotations

import pytest

from scripts.verify_recovery_target import verify_recovery_target


def test_recovery_target_refuses_primary_url() -> None:
    with pytest.raises(RuntimeError, match="must not point"):
        verify_recovery_target(
            "postgresql+psycopg://user:secret@primary.example/risk",
            "postgresql+psycopg://user:secret@primary.example/risk",
        )