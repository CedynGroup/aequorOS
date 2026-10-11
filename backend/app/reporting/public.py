"""Reporting's interface for other features: re-exports only, no logic."""

from app.models.regulatory_reporting import RegulatoryPackage
from app.services import filing_reconciliation
from app.services.filing_workflow import engine
from app.services.filing_workflow.errors import conflict, not_found, unprocessable
from app.services.regulatory_reporting import attachments, generation
from app.services.regulatory_reporting.common import get_snapshot_for_reporting_date
from app.services.regulatory_reporting.exports import pdf_parts
from app.services.regulatory_reporting.exports.text import xml_safe
from app.services.regulatory_reporting.exports.xlsx import normalize_zip
from app.services.regulatory_reporting.family_hooks import FamilyHooks
from app.services.regulatory_reporting.registry import REGISTRY
from app.services.regulatory_reporting.workflow import granted_unconsumed_resubmission

__all__ = [
    "FamilyHooks",
    "REGISTRY",
    "RegulatoryPackage",
    "attachments",
    "conflict",
    "engine",
    "filing_reconciliation",
    "generation",
    "get_snapshot_for_reporting_date",
    "granted_unconsumed_resubmission",
    "icaap_docx",
    "icaap_pdf",
    "normalize_zip",
    "not_found",
    "pdf_parts",
    "unprocessable",
    "xml_safe",
]


# The ICAAP exporters consume the shared rendering primitives re-exported above.
from app.services.regulatory_reporting.exports import icaap_docx, icaap_pdf
