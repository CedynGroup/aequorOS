from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

FIELD_FORMAT = "aqs-bank-field-v1"


class KeyUnavailableError(RuntimeError):
    """The bank key cannot be used; callers must refuse without a fallback."""

    def __init__(self, message: str, *, revoked: bool = False) -> None:
        super().__init__(message)
        self.revoked = revoked

    @property
    def public_message(self) -> str:
        return (
            "Your bank has turned off aequorOS access to protected data. "
            "Contact your bank administrator."
            if self.revoked
            else "Can't reach your bank's key service. Retrying."
        )


class ProtectedSupportAccessError(KeyUnavailableError):
    """Staff presentation reads must not release protected bank data."""

    def __init__(self) -> None:
        super().__init__("Protected data is hidden during staff support access.")

    @property
    def public_message(self) -> str:
        return str(self)


class KeyIntegrityError(KeyUnavailableError):
    """Encrypted material failed authentication or belongs to another context."""


class KeyStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class KeyReference:
    provider: str
    key_id: str
    region: str
    owner_account: str


@dataclass(frozen=True)
class KeyDescription:
    reference: KeyReference
    status: KeyStatus


@dataclass(frozen=True)
class DataKey:
    plaintext: bytes = field(repr=False)
    wrapped: bytes


class KeyProvider(Protocol):
    """No master-key material or provider credentials cross this boundary.

    Context must be authenticated by every provider. Rotation replaces only
    the wrapped data key; the encrypted payload stays byte-for-byte identical.
    No provider may cache plaintext keys across operations.
    """

    def describe(self, key: KeyReference) -> KeyDescription: ...

    def generate_data_key(self, key: KeyReference, context: dict[str, str]) -> DataKey: ...

    def wrap(self, key: KeyReference, plaintext: bytes, context: dict[str, str]) -> bytes: ...

    def unwrap(self, key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes: ...

    def rotate(
        self,
        source: KeyReference,
        destination: KeyReference,
        wrapped: bytes,
        context: dict[str, str],
    ) -> bytes: ...
