"""Which exact AI configurations have been reviewed for a deployed environment.

``approved_configurations.json`` ships EMPTY. Until an entry exists, every
request in a deployed environment is refused with ``configuration_not_approved``
— which is how "the AI path is not production-ready yet" stops being a note in a
handover document and becomes something the code enforces.

An entry is a reviewed commit. It pins the feature, the prompt version, the
model id, the effort level and the environment, and records the evidence that
justified it (the eval report digest, who approved it, when). Changing the
prompt text bumps ``PROMPT_VERSION``; changing the model or the effort changes
the tuple — either way the running configuration silently falls OUT of approval
and requests are refused again. That is the point: approval is of a specific
configuration, not of a feature.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_PATH = Path(__file__).parent / "approved_configurations.json"
_SCHEMA = "ai-approved-configurations-v1"


class ApprovalsError(ValueError):
    """The shipped approvals file is malformed — a packaging fault."""


@dataclass(frozen=True)
class ApprovedConfiguration:
    feature: str
    prompt_version: str
    model: str
    effort: str
    app_env: str
    #: Evidence, carried for the audit trail and never used in matching.
    eval_report_sha256: str | None
    approved_by: str | None
    approved_on: str | None
    reference: str | None

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.feature, self.prompt_version, self.model, self.effort, self.app_env)


def _entry(raw: Any, index: int) -> ApprovedConfiguration:
    if not isinstance(raw, dict):
        message = f"approved_configurations[{index}] must be an object"
        raise ApprovalsError(message)
    required = ("feature", "prompt_version", "model", "effort", "app_env")
    for field in required:
        if not isinstance(raw.get(field), str) or not raw[field]:
            message = f"approved_configurations[{index}].{field} must be a non-empty string"
            raise ApprovalsError(message)
    return ApprovedConfiguration(
        feature=raw["feature"],
        prompt_version=raw["prompt_version"],
        model=raw["model"],
        effort=raw["effort"],
        app_env=raw["app_env"],
        eval_report_sha256=raw.get("eval_report_sha256"),
        approved_by=raw.get("approved_by"),
        approved_on=raw.get("approved_on"),
        reference=raw.get("reference"),
    )


@lru_cache(maxsize=1)
def load_approvals() -> tuple[ApprovedConfiguration, ...]:
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA:
        message = f"approved_configurations.json must declare schema {_SCHEMA!r}"
        raise ApprovalsError(message)
    entries = raw.get("configurations")
    if not isinstance(entries, list):
        message = "approved_configurations.configurations must be a list"
        raise ApprovalsError(message)
    return tuple(_entry(item, index) for index, item in enumerate(entries))


def find(  # noqa: PLR0913 - the approval key is five explicit dimensions
    *,
    feature: str,
    prompt_version: str,
    model: str,
    effort: str,
    app_env: str,
    approvals: Sequence[ApprovedConfiguration] | None = None,
) -> ApprovedConfiguration | None:
    """The approval for this exact configuration, or None.

    Exact match on all five dimensions. No wildcard, no "any effort", no
    inheritance from another environment: an approval that could stretch is an
    approval nobody actually gave.
    """
    wanted = (feature, prompt_version, model, effort, app_env)
    for entry in approvals if approvals is not None else load_approvals():
        if entry.key == wanted:
            return entry
    return None


__all__ = ["ApprovalsError", "ApprovedConfiguration", "find", "load_approvals"]
