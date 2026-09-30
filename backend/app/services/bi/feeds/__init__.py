"""The Power BI Stage B feed: curated datasets a report server pulls (docs/bi.md §Phase 4).

Four modules, one responsibility each, and nothing here writes a row:

* :mod:`~app.services.bi.feeds.datasets` — **what may be pulled.** A closed
  registry of curated datasets, each declaring its columns as catalogue member
  ids, its grain, the mart build scopes it reads and its disclosure class. There
  is no client-chosen table, column list or filter, and nothing resembling SQL
  reaches this package from a caller.
* :mod:`~app.services.bi.feeds.cursor` — **what is new.** The cursor is anchored
  on the mart BUILD rather than on the business date, because a bank's book is
  restated; the module docstring is the argument for that and for the barrier
  that keeps a concurrent build from being stepped over.
* :mod:`~app.services.bi.feeds.authorization` — **who may.** The machine
  decision: a bank-scoped integration key, the licence class, every
  ``(module, sensitivity)`` pair the dataset touches, and the binding's data
  scope as an unremovable branch filter.
* :mod:`~app.services.bi.feeds.runner` — **the rows and the provenance.** Paged
  reads through the one guarded executor, and the response metadata.
* :mod:`~app.services.bi.feeds.render` — **the bytes.** NDJSON and CSV, streamed
  a line at a time, agreeing on every value.

What is NOT here, deliberately: the route and its refusals
(``app/features/read_bi_feeds.py``), the ``bi_query_log`` row and the audit event
(the feature owns both — ``app/services/bi`` writes ``bi_*`` tables and nothing
else), and the credential itself (``app/services/integration_keys.py`` issues the
``bi_reader`` key through the existing integration-key flow).

The bank-facing contract is ``docs/API_INTEGRATION.md`` §8; the deployment and
data-residency guidance a bank's BI team is handed is
``backend/docs/powerbi_stage_b.md``.
"""

from __future__ import annotations

from app.services.bi.feeds.authorization import (
    SURFACE_FEED,
    FeedAuthorization,
    authorize_feed,
)
from app.services.bi.feeds.cursor import (
    MAX_SLICES_PER_PULL,
    FeedCursor,
    FeedSlice,
    InvalidCursor,
    SliceSelection,
    decode,
    select_slices,
)
from app.services.bi.feeds.datasets import (
    DATASETS,
    FeedDataset,
    UnknownDataset,
    dataset,
    dataset_ids,
)
from app.services.bi.feeds.render import (
    FEED_FORMATS,
    MEDIA_TYPES,
    FeedFormat,
    iter_bytes,
)
from app.services.bi.feeds.runner import (
    FEED_HEADERS,
    PAGE_ROWS,
    FeedProvenance,
    FeedShapeChanged,
    build_provenance,
    columns_for,
    filename_for,
    iter_rows,
)

__all__ = [
    "DATASETS",
    "FEED_FORMATS",
    "FEED_HEADERS",
    "MAX_SLICES_PER_PULL",
    "MEDIA_TYPES",
    "PAGE_ROWS",
    "SURFACE_FEED",
    "FeedAuthorization",
    "FeedCursor",
    "FeedDataset",
    "FeedFormat",
    "FeedProvenance",
    "FeedShapeChanged",
    "FeedSlice",
    "InvalidCursor",
    "SliceSelection",
    "UnknownDataset",
    "authorize_feed",
    "build_provenance",
    "columns_for",
    "dataset",
    "dataset_ids",
    "decode",
    "filename_for",
    "iter_bytes",
    "iter_rows",
    "select_slices",
]
