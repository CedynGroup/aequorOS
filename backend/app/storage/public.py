"""Cross-feature API for immutable sealed document resolution."""

from app.storage.sealed_documents import (
    DocumentBinding,
    DocumentFamily,
    resolve_sealed_document,
)

__all__ = ["DocumentBinding", "DocumentFamily", "resolve_sealed_document"]
