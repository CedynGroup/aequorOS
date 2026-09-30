"""The BI plane's services (ARCHITECTURE ``.ai/BI_ARCHITECTURE.md``).

``mart_builder`` writes the ``bi_*`` marts from the canonical book, the live
plane and the sealed runs; ``provenance`` says which build an answer was read
from and whether that build succeeded; ``partitions`` is the thin Postgres
partition-lifecycle wrapper; ``compiler`` turns a ``BiQuery`` into SQLAlchemy
Core. BI grades nothing against the returns the platform files — that was the
regulatory plane's question and it left BI on 2026-09-29. Nothing in this package
writes a canonical, regulatory or live table, calls ``derive_facts``, or
records a governed-parameter read.
"""
