"""Value-based digests for the ICAAP registers.

A register block — the risk register, the appetite table, the Pillar 2 summary
— has no sealed run to pin. Its binding pins a digest of its own CONTENT
instead, so a refresh that produces the same figures writes nothing and one
that produces different figures makes every section quoting it stale, exactly
as a new engine run would.

The digest is value-based for the same reason the regulatory ``input_hash`` is
(AGENTS.md): row ids change on every rewrite, so a digest that included them
would report a change whenever nothing had changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.attestation.digests import digest_of

#: Prefix of a register block's ``source_key``. The resolver recomputes it on
#: every probe, which is what makes staleness a read-time answer.
SOURCE_PREFIX = "computed"


def register_digest(body: Mapping[str, Any] | list[Any]) -> str:
    """A stable sha256 over canonical JSON of the register's content."""
    return digest_of(body)


def source_key(register: str, digest: str) -> str:
    return f"{SOURCE_PREFIX}:{register}:{digest}"


def inputs_digest(body: Mapping[str, Any]) -> str:
    """The digest of a Pillar 2 computation's complete inputs.

    It covers the bound source keys, the manual inputs and the RESOLVED
    parameter values and row ids, so a console change to a governed figure
    changes it — which is how a computed item learns that it is stale.
    """
    return digest_of(body)


__all__ = ["SOURCE_PREFIX", "inputs_digest", "register_digest", "source_key"]
