"""Manual Upload fallback adapter (market_data_adapter.md §8).

Not every bank has Bloomberg or Refinitiv. The Manual Upload adapter produces
the same canonical output as the vendor adapters, sourced from operator
uploaded files matching AequorOS's provided templates: the operator downloads
a template per scope category, fills it from their own sources (BoG website
for GHS curves, ECB for EUR curves, ...), and uploads it. The upload is
treated as a pull with ``source_system = 'MANUAL_UPLOAD'``: full lineage and
audit records, zero vendor quota (§8.3). Always available in every
institution, never disabled (§8.4).
"""

from app.adapters.market_data.manual_upload.adapter import (
    ADAPTER_VERSION,
    VENDOR,
    ManualUploadAdapter,
)
from app.adapters.market_data.manual_upload.parser import (
    ManualUploadParseError,
    ParsedUpload,
    RowProblem,
    ScopeRows,
    parse_upload,
)
from app.adapters.market_data.manual_upload.templates import (
    TEMPLATE_HEADERS,
    TEMPLATE_KINDS,
    TemplateKind,
    build_template,
)

__all__ = [
    "ADAPTER_VERSION",
    "TEMPLATE_HEADERS",
    "TEMPLATE_KINDS",
    "VENDOR",
    "ManualUploadAdapter",
    "ManualUploadParseError",
    "ParsedUpload",
    "RowProblem",
    "ScopeRows",
    "TemplateKind",
    "build_template",
    "parse_upload",
]
