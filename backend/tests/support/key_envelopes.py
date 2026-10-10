from __future__ import annotations

from uuid import UUID, uuid4

from app.core.key_management.registry import (
    EnvelopeStore,
    ObjectKey,
    context_digest,
    wrapping_context,
)
from app.core.key_management.types import KeyIntegrityError, KeyProvider, KeyReference


class MemoryEnvelopeStore(EnvelopeStore):
    """Test storage with real provider wrapping, including revocation checks."""

    def __init__(self, slug: str, key: KeyReference, provider: KeyProvider) -> None:
        self.slug = slug
        self.key = key
        self.provider = provider
        self.envelopes: dict[UUID, tuple[str, bytes]] = {}

    def prepare(self, slug: str, context: dict[str, str]) -> ObjectKey:
        if slug != self.slug:
            raise KeyIntegrityError("Unknown test bank.")
        digest = context_digest(context)
        data = self.provider.generate_data_key(self.key, wrapping_context(slug, digest))
        identifier = uuid4()
        self.envelopes[identifier] = digest, data.wrapped
        return ObjectKey(identifier, self.key.key_id, data.plaintext)

    def open(self, slug: str, envelope_id: UUID, context: dict[str, str]) -> ObjectKey:
        digest, wrapped = self.envelopes[envelope_id]
        if slug != self.slug or digest != context_digest(context):
            raise KeyIntegrityError("Test object belongs to another bank or location.")
        plaintext = self.provider.unwrap(self.key, wrapped, wrapping_context(slug, digest))
        return ObjectKey(envelope_id, self.key.key_id, plaintext)
