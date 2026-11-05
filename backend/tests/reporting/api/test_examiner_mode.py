"""Examiner read-model checks over a disposable canonical bank book.

The reader holds an explicit AUDIT/restricted view binding plus each source
module's view binding. Grants, stored evidence and snapshot tampering share the
hermetic fixture transaction; permission-denial depth lives in
``tests/reporting/api/test_examiner_authorization_integration.py``.

This module's other-suite cases live in ``test_examiner_mode_integration.py``.
"""

from __future__ import annotations

from app.core.security import ROLES, has_role


def test_examiner_ladder_reads_but_never_mutates() -> None:
    assert ROLES == ("admin", "approver", "analyst", "examiner", "viewer")
    assert has_role(["examiner"], "viewer") is True
    assert has_role(["examiner"], "examiner") is True
    assert has_role(["examiner"], "analyst") is False
    assert has_role(["viewer"], "examiner") is False
