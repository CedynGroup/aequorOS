"""What an officer's decision on a return attests to, as one value-based digest.

An approval is an approval OF something. If the figures or the check results can
change after the Approver has read them and before the Validator transmits, the
platform cannot say what was approved — and "the Approver signed off BSD1 for
30 June" becomes a claim nobody can check. So every forward decision carries the
digest of exactly what was in front of the decider, and a decision taken on a
stale digest is refused rather than recorded.

The digest is **value-based**, for the same reason the regulatory ``input_hash``
is (AGENTS.md): only content is hashed, never row ids, never timestamps.

Two deliberate exclusions, each of which would otherwise expire an approval for
nothing:

* **Artifacts.** The filing set is minted on the way to the channel — a missing
  xlsx is exported at submit time — so including artifact hashes would mean the
  act of filing invalidated the approval that authorised it.
* **Attestation state.** Signatures arrive DURING the chain by design. A
  digest that moved on each one would make the second signature expire the
  first officer's approval.

Every numeric value goes through ``chain.normalise_amount`` (D-067): the ICAAP
digest expired a reviewer's approval because ``Decimal("1")`` and
``Decimal("1.000000")`` hashed differently depending on whether SQLAlchemy had
reloaded the row. The rule ships with the engine so this plane cannot
reintroduce it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domain.workflow import chain

SCHEMA = "filing-review-digest-v1"


def normalise(value: Any) -> Any:
    """Canonicalise one value for the digest body, scale included.

    Recursive on purpose: a nested figure is exactly as capable of arriving at a
    different scale as a top-level one, and a normaliser that only looked at the
    outer layer would be the same defect one level down.
    """
    if isinstance(value, Decimal):
        return chain.normalise_amount(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): normalise(item) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, list | tuple):
        return [normalise(item) for item in value]
    return value


def digest_body(  # noqa: PLR0913 - the body is exactly these named parts
    *,
    return_code: str,
    reporting_date: date,
    basis: str,
    version: int,
    content_digest: str | None,
    snapshot_sha256: str | None,
    register_state_digest: str | None,
    checks_passed: bool,
    validation_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The canonical body the digest is taken over — ids and timestamps excluded."""
    report = validation_report or {}
    return normalise(
        {
            "schema": SCHEMA,
            "return": [return_code, reporting_date, basis, version],
            "content_digest": content_digest,
            "snapshot_sha256": snapshot_sha256,
            "register_state_digest": register_state_digest,
            "checks_passed": checks_passed,
            "checks": {
                "passed": bool(report.get("passed", False)),
                "error_count": report.get("error_count"),
                "warning_count": report.get("warning_count"),
                "findings": sorted(
                    [
                        str(finding.get("rule_id") or finding.get("code") or ""),
                        str(finding.get("severity") or ""),
                        str(finding.get("message") or ""),
                    ]
                    for finding in _findings(report)
                ),
            },
        }
    )


def _findings(report: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    raw = report.get("findings") or report.get("results") or []
    if not isinstance(raw, list | tuple):  # pragma: no cover - report is engine-written
        return ()
    return [entry for entry in raw if isinstance(entry, Mapping)]


def compute(**kwargs: Any) -> str:
    return hashlib.sha256(chain.canonical_json(digest_body(**kwargs)).encode("utf-8")).hexdigest()


__all__ = ["SCHEMA", "compute", "digest_body", "normalise"]
