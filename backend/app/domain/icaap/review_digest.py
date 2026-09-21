"""What a reviewer's decision attests to, as one value-based digest (pure domain).

An approval is an approval OF something. If the text, the checklist answers or
the bound figures can change after the CRO has read them and before the Board
signs, the platform cannot say what was approved — and "the CRO approved the
FY2025 ICAAP" becomes a claim nobody can check. So every forward decision
carries the digest of exactly what was in front of the decider, and a later
decision on a different digest is refused rather than recorded.

The digest is **value-based**, for the same reason the regulatory ``input_hash``
is (AGENTS.md): a block refresh that reproduces the same figures writes a new
binding row with a new id, and a digest that included row ids would report a
change when nothing changed. Only content is hashed.

Two deliberate exclusions:

* **Attachments.** Uploads stay open while a cycle is in review — a reviewer
  asking for the Board minutes is the normal way of getting them, and making
  that invalidate every decision taken so far would push banks into attaching
  everything up front. The freeze gate checks attachments separately.
* **Uncommitted working text.** It is not reviewable, so it cannot be part of
  what was reviewed; submission refuses while any exists.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.icaap.frameworks.registry import canonical_json


@dataclass(frozen=True)
class SectionDigestInput:
    section_key: str
    committed_version_no: int | None
    doc_sha256: str | None


@dataclass(frozen=True)
class RequirementDigestInput:
    section_key: str
    item_id: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class BlockDigestInput:
    block_key: str
    binding_seq: int | None
    payload_sha256: str | None
    pinned_binding_seq: int | None
    pin_reason: str | None


def _sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_body(
    *,
    framework: Mapping[str, Any],
    sections: Iterable[SectionDigestInput],
    requirements: Iterable[RequirementDigestInput],
    blocks: Iterable[BlockDigestInput],
    p2_state_digest: str | None,
) -> dict[str, Any]:
    """The canonical body the digest is taken over — sorted, ids excluded."""
    return {
        "schema": "icaap-review-digest-v1",
        "framework": {
            "code": framework.get("code"),
            "version": framework.get("version"),
            "sha256": framework.get("sha256"),
        },
        "sections": sorted(
            [entry.section_key, entry.committed_version_no, entry.doc_sha256] for entry in sections
        ),
        "requirements": sorted(
            [entry.section_key, entry.item_id, entry.status, _sha256(entry.reason)]
            for entry in requirements
        ),
        "blocks": sorted(
            [
                entry.block_key,
                entry.binding_seq,
                entry.payload_sha256,
                entry.pinned_binding_seq,
                _sha256(entry.pin_reason),
            ]
            for entry in blocks
        ),
        "p2": p2_state_digest,
    }


def compute(
    *,
    framework: Mapping[str, Any],
    sections: Sequence[SectionDigestInput],
    requirements: Sequence[RequirementDigestInput],
    blocks: Sequence[BlockDigestInput],
    p2_state_digest: str | None,
) -> str:
    body = digest_body(
        framework=framework,
        sections=sections,
        requirements=requirements,
        blocks=blocks,
        p2_state_digest=p2_state_digest,
    )
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


__all__ = [
    "BlockDigestInput",
    "RequirementDigestInput",
    "SectionDigestInput",
    "compute",
    "digest_body",
]
