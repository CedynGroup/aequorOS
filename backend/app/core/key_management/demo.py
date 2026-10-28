"""File-controlled synthetic key provider for isolated local demonstrations."""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import is_undeployed_environment
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import (
    KeyDescription,
    KeyReference,
    KeyStatus,
    KeyUnavailableError,
)


class DemoKeyState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str = Field(pattern=r"^OR-[A-Z0-9]+$")
    bank_id: str = Field(pattern=r"^BK-[A-Z0-9]+$")
    material: SecretStr
    status: KeyStatus = KeyStatus.ACTIVE

    @property
    def reference(self) -> KeyReference:
        return KeyReference(
            "aws_kms",
            f"synthetic:{self.organization_id}:{self.bank_id}",
            "local",
            "000000000000",
        )


def require_local_demo() -> None:
    if not is_undeployed_environment():
        raise KeyUnavailableError(
            "Synthetic demo keys are available only in local/test environments."
        )


def read_state(path: Path) -> DemoKeyState:
    require_local_demo()
    try:
        return DemoKeyState.model_validate_json(path.read_text())
    except (OSError, ValueError) as exc:
        raise KeyUnavailableError("The synthetic key service is unreachable.") from exc


def _write_state(path: Path, state: DemoKeyState) -> None:
    # Keep key material out of model dumps, logs and command output.
    payload = {
        "organization_id": state.organization_id,
        "bank_id": state.bank_id,
        "status": state.status.value,
        "material": state.material.get_secret_value(),
    }
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(json.dumps(payload))
            temporary.flush()
            os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def initialize(path: Path, *, organization_id: str, bank_id: str) -> DemoKeyState:
    require_local_demo()
    state = DemoKeyState(
        organization_id=organization_id,
        bank_id=bank_id,
        material=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode("ascii")),
    )
    # Exclusive creation prevents accidentally destroying a demo's existing key.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        _write_state(path, state)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return state


def change_status(path: Path, status: KeyStatus) -> DemoKeyState:
    state = read_state(path)
    updated = state.model_copy(update={"status": status})
    _write_state(path, updated)
    return updated


class FileDemoKeyProvider(LocalKeyProvider):
    """Reuse the test provider's cipher and reread control state per operation."""

    def __init__(self, path: Path) -> None:
        require_local_demo()
        super().__init__()
        self._path = path

    def _state(self, key: KeyReference) -> DemoKeyState:
        state = read_state(self._path)
        if key != state.reference:
            raise KeyUnavailableError("The requested key is not this synthetic demo key.")
        return state

    def describe(self, key: KeyReference) -> KeyDescription:
        return KeyDescription(key, self._state(key).status)

    def _material(self, key: KeyReference) -> bytes:
        state = self._state(key)
        if state.status != KeyStatus.ACTIVE:
            raise KeyUnavailableError(
                "The synthetic key is unavailable.",
                revoked=state.status == KeyStatus.DISABLED,
            )
        try:
            material = base64.b64decode(state.material.get_secret_value(), validate=True)
            if len(material) != 32:
                raise ValueError("Invalid synthetic key length")
        except ValueError as exc:
            raise KeyUnavailableError("The synthetic key state is invalid.") from exc
        return material
