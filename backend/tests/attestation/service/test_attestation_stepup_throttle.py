"""Brute-force defence on the signing step-up (audit finding P0-4).

The step-up endpoint is the last control between a stolen — or merely
over-privileged — session and a legally binding regulatory attestation. It used
to call ``verify_password`` directly and write nothing back, so an authenticated
analyst could guess an approver's password at it forever. The properties
asserted here are the ones that would be DEFECTS if they regressed:

* a wrong password is COUNTED, durably, in the same two ``users`` columns the
  sign-in path uses — so the two surfaces share one budget of guesses rather
  than granting ``max_failed`` each;
* the count survives the refusal (it is committed before the exception, not
  rolled back with it) and survives a process restart, because a
  ``--workers 4`` deployment behind more than one replica cannot defend itself
  with a counter that lives in one process's memory;
* crossing the threshold LOCKS, the lock is checked BEFORE the hash (so the
  right password does not open a locked account), and the lock is the same one
  sign-in writes and reads;
* nothing is minted after the lock — the control fails closed;
* an unknown membership and a wrong password are indistinguishable, in wording
  and in the Argon2 work they burn;
* an id_token with no ``auth_time`` is refused rather than treated as fresh, or
  the "prove presence now" check would be decorative;
* a signing authorisation is burned exactly once even when two readers both saw
  it unspent;
* and the legitimate preparer → approver ceremony still completes with the
  throttle in place.

This module's other-suite cases live in
``test_attestation_stepup_throttle_integration.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.attestation.schemas import StepUpRequest


# ---------------------------------------------------------------------------
# Contract bounds
# ---------------------------------------------------------------------------
def test_the_step_up_contract_bounds_both_proofs() -> None:
    """Verifying a password costs ~64 MiB of Argon2. An unbounded field is a
    cheap way to make the signing endpoint expensive."""
    with pytest.raises(ValidationError):
        StepUpRequest(signing_role="preparer", password="x" * 1025)
    with pytest.raises(ValidationError):
        StepUpRequest(signing_role="preparer", password="")
    with pytest.raises(ValidationError):
        StepUpRequest(signing_role="preparer", id_token="x" * 8193)

    assert StepUpRequest(signing_role="preparer", password="x" * 1024).password
    assert StepUpRequest(signing_role="preparer", password=None).password is None
