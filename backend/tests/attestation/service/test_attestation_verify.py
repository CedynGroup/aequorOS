"""Attestation verification — the five independent checks (docs §3.5).

Every signature in this module is a REAL signature: a self-signed root CA and two
leaf certificates are minted with ``cryptography``, a soft-key ``RawSigner`` is
injected into the real ``signing.certify`` path, and the resulting rows are the
same rows production writes. Nothing is stubbed except the HSM (which
``cryptography`` cannot be) and the object store (in-memory, the same seam the
export tests use).

The load-bearing test is
``test_thoroughly_retampered_figures_fail_content_binding_while_cryptography_passes``:
it proves checks 3 and 4 are genuinely independent. An attacker with database
access rewrites the snapshot AND re-seals every derived column so the package is
internally self-consistent — and the cryptographic checks stay green, because
nothing was done to the signature. Only ``content_binding`` catches it, because
only ``content_binding`` compares live state against what the SIGNATURE recorded.

Two deliberate liberties, both noted where they occur:

* signatures are mutated in place to simulate tampering. Production forbids that
  at the database level (migration 202607250027 revokes UPDATE and installs a
  raising trigger); SQLite has no such enforcement, which is what makes the
  tamper cases testable at all.
* step-up is minted directly rather than driven through OIDC/password
  re-authentication. That path has its own tests; here the subject is what a
  verifier can prove about a signature that already exists.

This module's other-suite cases live in ``test_attestation_verify_integration.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.attestation.service.test_attestation_verify_integration import main


def test_offline_cli_refuses_unknown_inputs_with_a_distinct_exit_code(
    tmp_path: Path,
) -> None:
    """Exit 2 is "could not run"; an operator must never read it as a pass."""
    missing = tmp_path / "nope.json"
    assert main(["--record", str(missing)]) == 2

    wrong_schema = tmp_path / "wrong.json"
    wrong_schema.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
    assert main(["--record", str(wrong_schema)]) == 2
