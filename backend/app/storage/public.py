"""Cross-feature API for immutable sealed document publication and resolution."""

from app.storage.sealed_documents import (
    DocumentBinding,
    DocumentFamily,
    RecordedDocument,
    document_retention_reference,
    find_sealed_document,
    resolve_document,
    resolve_sealed_document,
)
from app.storage.sealed_publication import (
    PublicationRequest,
    PublishedDocument,
    bind_existing_document,
    bind_sealed_document,
    publish_sealed_document,
)

__all__ = [
    "DocumentBinding",
    "DocumentFamily",
    "PublicationRequest",
    "PublishedDocument",
    "RecordedDocument",
    "bind_existing_document",
    "bind_sealed_document",
    "find_sealed_document",
    "document_retention_reference",
    "publish_sealed_document",
    "resolve_document",
    "resolve_sealed_document",
]
