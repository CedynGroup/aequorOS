"""The certified dashboard packs, parsed at import (spec §Phase 2, Content packs).

One JSON file per pack, named for the pack's own id. Every file is read and
validated into a :class:`BiPackSpec` the moment this module is imported, so a
malformed pack is an import failure at start-up rather than a 500 on a
dashboard route in front of a board.

The packs are DATA: a file can name catalogue member ids, a closed window
vocabulary and a closed panel key, and nothing else — no table, no column, no
route, no parameter and no date. That is a property of
:class:`app.schemas.bi.BiPackSpec` (every model closed, every source a
Literal), not of a convention, and ``tests/domain/bi/test_packs.py`` proves
each file also resolves through the catalogue and passes the compiler's shape
rules without a database.

This package is part of the pure BI domain: it imports the wire contract and
the standard library, and never a service, a model or SQL.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.bi import BiPackSpec

__all__ = ["PACK_DIR", "PackError", "pack", "pack_ids", "packs"]

PACK_DIR = Path(__file__).parent


class PackError(ValueError):
    """A pack file is missing, malformed or disagrees with its own file name."""


def _load(path: Path) -> BiPackSpec:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackError(f"{path.name} is not valid JSON: {exc}") from exc
    try:
        spec = BiPackSpec.model_validate(raw)
    except ValidationError as exc:
        raise PackError(f"{path.name} is not a valid dashboard pack: {exc}") from exc
    if spec.id != path.stem:
        raise PackError(f"{path.name} declares id {spec.id!r}; a pack is named for its id")
    return spec


def _load_all() -> Mapping[str, BiPackSpec]:
    files = sorted(PACK_DIR.glob("*.json"))
    if not files:
        raise PackError(
            f"{PACK_DIR.name} holds no pack files; a package that loads nothing would "
            "leave every dashboard surface silently empty"
        )
    loaded: dict[str, BiPackSpec] = {}
    audiences: dict[str, str] = {}
    for path in files:
        spec = _load(path)
        if spec.id in loaded:  # pragma: no cover - a glob cannot yield one stem twice
            raise PackError(f"two pack files declare id {spec.id!r}")
        owner = audiences.get(spec.audience)
        if owner is not None:
            raise PackError(
                f"{spec.id} and {owner} are both written for the {spec.audience} audience; "
                "one pack per audience, so a reader is never shown two certified answers"
            )
        audiences[spec.audience] = spec.id
        loaded[spec.id] = spec
    return loaded


#: Every certified pack, keyed by id, in file-name order.
PACKS: Mapping[str, BiPackSpec] = _load_all()


def packs() -> tuple[BiPackSpec, ...]:
    """Every certified pack, in file-name order."""
    return tuple(PACKS.values())


def pack_ids() -> tuple[str, ...]:
    return tuple(PACKS)


def pack(pack_id: str) -> BiPackSpec:
    """One pack by id; ``PackError`` when there is no such pack."""
    try:
        return PACKS[pack_id]
    except KeyError as exc:
        raise PackError(pack_id) from exc
