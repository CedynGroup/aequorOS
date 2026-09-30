"""Version constants shared by the BI enqueue side and the mart builder.

``BUILDER_VERSION`` is stamped on every BI job payload by the enqueue seam
(``app.services.bi.enqueue``, the scheduler sweep and the operator backfill)
and compared by every ``bi`` lane handler against the builder it loaded
(P1-B5: a handler OLDER than the payload marks the job ``skipped:version``).

It lives here, in a module with no imports, because the two sides must not
share an import graph: the enqueue side runs inside ingestion, the pipelines
and the register triggers — the product's hot path — and must never pull the
mart builder (its models, the catalogue) into that
path. ``mart_builder.BUILDER_VERSION`` re-exports this value so the contract
name in ``.ai/bi_contracts.md`` still resolves; bump it HERE, once, whenever a
mart's shape or a fingerprint input changes.
"""

from __future__ import annotations

#: Bump on any change to a mart's shape or to a fingerprint input. Stamped on
#: every BI job payload; compared by every ``bi`` lane handler.
BUILDER_VERSION: int = 2

__all__ = ["BUILDER_VERSION"]
