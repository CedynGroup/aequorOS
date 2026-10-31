"""Data Engine's interface for other features: re-exports only, no logic."""

from app.data_engine.api.upload_seam import (
    MAX_UPLOAD_BYTES,
    IngestionStorage,
)
from app.data_engine.models.canonical import (
    CanonicalCounterparty,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalGlAccount,
    CanonicalLoanEvent,
    CanonicalMarketIndex,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    is_current_generation,
)
from app.data_engine.models.database_connection import DatabaseDirectConnection
from app.data_engine.models.ingestion import (
    IngestionBatch,
    LineageRecord,
    MappingConfigRecord,
    TranslationFailure,
)
from app.data_engine.models.temenos import TemenosConnection
from app.data_engine.schemas.data_activation import (
    ACTIVATION_EVENT,
    ActivationGroupRead,
    ActivationRunRead,
    DataActivationCreate,
    DataActivationListRead,
    DataActivationRead,
    DataActivationSummaryRead,
)
from app.data_engine.service import reconciliation, withdrawal_impact
from app.data_engine.service.concentration_labels import counterparty_group_labels
from app.data_engine.service.document_uploads import (
    ALLOWED_MEDIA,
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    sniff_media_type,
)

# isort: split
# The import below reaches the widest slice of the app, so every lighter
# re-export above must be bound before it initializes: a module-level
# ``public`` import from anywhere on its chain (reporting's filing
# reconciliation does exactly that) then resolves against a complete namespace
# instead of a partially initialized module.
from app.data_engine.service.ingestion import (  # noqa: E402 - deliberate order
    bank_slug,
    upload_source,
)

__all__ = [
    "ACTIVATION_EVENT",
    "ALLOWED_MEDIA",
    "ActivationGroupRead",
    "ActivationRunRead",
    "CanonicalCounterparty",
    "CanonicalCounterpartyRating",
    "CanonicalFxRate",
    "CanonicalGlAccount",
    "CanonicalLoanEvent",
    "CanonicalMarketIndex",
    "CanonicalPosition",
    "CanonicalPositionSnapshot",
    "CanonicalProduct",
    "CanonicalReferenceRow",
    "CanonicalYieldCurve",
    "CanonicalYieldCurvePoint",
    "DatabaseDirectConnection",
    "DataActivationCreate",
    "DataActivationListRead",
    "DataActivationRead",
    "DataActivationSummaryRead",
    "DOCX_MEDIA_TYPE",
    "IngestionBatch",
    "IngestionStorage",
    "LineageRecord",
    "MappingConfigRecord",
    "MAX_UPLOAD_BYTES",
    "PDF_MEDIA_TYPE",
    "TemenosConnection",
    "TranslationFailure",
    "XLSX_MEDIA_TYPE",
    "bank_slug",
    "counterparty_group_labels",
    "is_current_generation",
    "reconciliation",
    "sniff_media_type",
    "upload_source",
    "withdrawal_impact",
]
