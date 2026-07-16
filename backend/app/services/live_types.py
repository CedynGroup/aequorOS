"""Shared types for the two-tier live view.

Neutral module (imports no services) so each regulatory module can expose a
``compute_live`` returning the normalized shape the pipeline upserts, without a
circular import through the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Rank for rolling per-metric traffic-light statuses up to a module status.
_STATUS_RANK = {"red": 3, "amber": 2, "green": 1, "na": 0}

# A failed regulatory validation maps to a live-finding severity. Error-severity
# breaches escalate to critical when the module itself is red, else high.
_WARNING_SEVERITY = "medium"
_INFO_SEVERITY = "low"


@dataclass(frozen=True)
class LiveFindingSpec:
    """One derived limit breach, the raw material for a live alert."""

    rule_id: str
    severity: str  # low | medium | high | critical
    message: str
    metric: str | None = None


@dataclass(frozen=True)
class LiveModuleResult:
    """A module's cheap baseline live view for one (bank, period)."""

    metrics: dict[str, str]
    status: str  # green | amber | red | na
    input_hash: str | None
    findings: tuple[LiveFindingSpec, ...] = field(default_factory=tuple)


def worst_status(*statuses: str) -> str:
    present = [status for status in statuses if status]
    if not present:
        return "na"
    return max(present, key=lambda status: _STATUS_RANK.get(status, 0))


def findings_from_validations(
    validations: tuple[tuple[str, bool, str, str], ...], module_status: str
) -> tuple[LiveFindingSpec, ...]:
    """Turn a module's ``(rule_code, passed, severity, message)`` rows into the
    open-breach findings that feed the alerts surface. Passing rows and
    info-only rows never raise an alert-worthy severity."""
    specs: list[LiveFindingSpec] = []
    for rule_code, passed, severity, message in validations:
        if passed:
            continue
        if severity == "error":
            finding_severity = "critical" if module_status == "red" else "high"
        elif severity == "warning":
            finding_severity = _WARNING_SEVERITY
        else:
            finding_severity = _INFO_SEVERITY
        specs.append(LiveFindingSpec(rule_id=rule_code, severity=finding_severity, message=message))
    return tuple(specs)
