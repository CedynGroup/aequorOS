"""Regulatory Reporting & Submission Hub services (docs/regulatory_reporting.md).

- ``registry`` — the return-family registry (§4): codes, deadline rules,
  generators, template fidelity grades.
- ``generation`` — immutable versioned package generation from existing
  computed state (§5).
- ``validation`` — completeness / consistency / prior-period movement rules.
- ``workflow`` — the §2 lifecycle state machine with maker-checker + audit.
- ``anchors`` — the regulator's own reporting dates for a return (the ONE
  authority; never derived from ingestion).
- ``calendar`` — upcoming obligations with due dates and RAG grades, plus the
  per-return anchor list the Returns workspace selects a reporting date from.
- ``channels`` — the submission-channel protocol (concrete channels ship in
  the export/submission wave).
"""

from app.reporting.service.calendar import list_obligations, list_return_anchors
from app.reporting.service.channel_config import (
    get_channel_config,
    put_channel_config,
)
from app.reporting.service.generation import GeneratedReturn, generate_package
from app.reporting.service.packages import (
    get_package,
    list_packages,
    list_return_templates,
)
from app.reporting.service.registry import (
    REGISTRY,
    ReturnDefinition,
    get_definition,
)
from app.reporting.service.reporting_settings import (
    get_reporting_settings,
    put_reporting_settings,
)
from app.reporting.service.validation import validate_package
from app.reporting.service.workflow import (
    ALLOWED_TRANSITIONS,
    decide_approval,
    list_submission_events,
    record_regulator_decision,
    request_approval,
    submit_package,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "REGISTRY",
    "GeneratedReturn",
    "ReturnDefinition",
    "decide_approval",
    "generate_package",
    "get_channel_config",
    "get_definition",
    "get_package",
    "get_reporting_settings",
    "list_obligations",
    "list_packages",
    "list_return_anchors",
    "list_return_templates",
    "list_submission_events",
    "put_channel_config",
    "put_reporting_settings",
    "record_regulator_decision",
    "request_approval",
    "submit_package",
    "validate_package",
]
