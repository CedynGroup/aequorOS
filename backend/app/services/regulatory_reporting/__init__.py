"""Regulatory Reporting & Submission Hub services (docs/regulatory_reporting.md).

- ``registry`` — the return-family registry (§4): codes, deadline rules,
  generators, template fidelity grades.
- ``generation`` — immutable versioned package generation from existing
  computed state (§5).
- ``validation`` — completeness / consistency / prior-period movement rules.
- ``workflow`` — the §2 lifecycle state machine with maker-checker + audit.
- ``calendar`` — upcoming obligations with due dates and RAG grades.
- ``channels`` — the submission-channel protocol (concrete channels ship in
  the export/submission wave).
"""

from app.services.regulatory_reporting.calendar import list_obligations
from app.services.regulatory_reporting.channel_config import (
    get_channel_config,
    put_channel_config,
)
from app.services.regulatory_reporting.generation import GeneratedReturn, generate_package
from app.services.regulatory_reporting.packages import (
    get_package,
    list_packages,
    list_return_templates,
)
from app.services.regulatory_reporting.registry import (
    REGISTRY,
    ReturnDefinition,
    get_definition,
)
from app.services.regulatory_reporting.validation import validate_package
from app.services.regulatory_reporting.workflow import (
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
    "list_obligations",
    "list_packages",
    "list_return_templates",
    "list_submission_events",
    "put_channel_config",
    "record_regulator_decision",
    "request_approval",
    "submit_package",
    "validate_package",
]
