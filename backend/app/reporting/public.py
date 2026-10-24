"""Reporting's interface for other features: re-exports only, no logic."""

from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import attachments, filing_reconciliation, generation
from app.reporting.service.common import get_snapshot_for_reporting_date
from app.reporting.service.exports import pdf_parts
from app.reporting.service.exports.pdf_parts import (
    NAVY,
    WATERMARK_GREY,
    canvas_pagesize,
    escape_text,
    invariant_canvas,
    printable,
)
from app.reporting.service.exports.text import xml_safe
from app.reporting.service.exports.xlsx import normalize_zip
from app.reporting.service.family_hooks import FamilyHooks
from app.reporting.service.filing_workflow import engine
from app.reporting.service.filing_workflow.errors import conflict, not_found, unprocessable
from app.reporting.service.instrument_notifications import scan_instrument_statuses
from app.reporting.service.registry import REGISTRY
from app.reporting.service.report_comparison import favorability_for_disposition
from app.reporting.service.workflow import granted_unconsumed_resubmission

__all__ = [
    "FamilyHooks",
    "NAVY",
    "REGISTRY",
    "scan_instrument_statuses",
    "RegulatoryPackage",
    "WATERMARK_GREY",
    "attachments",
    "canvas_pagesize",
    "conflict",
    "engine",
    "escape_text",
    "favorability_for_disposition",
    "filing_reconciliation",
    "generation",
    "get_snapshot_for_reporting_date",
    "granted_unconsumed_resubmission",
    "icaap_docx",
    "icaap_pdf",
    "invariant_canvas",
    "normalize_zip",
    "not_found",
    "pdf_parts",
    "printable",
    "unprocessable",
    "xml_safe",
]


# The ICAAP exporters consume the shared rendering primitives re-exported above.
from app.reporting.service.exports import icaap_docx, icaap_pdf
