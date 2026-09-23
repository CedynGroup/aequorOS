"""The BI plane's services (ARCHITECTURE ``.ai/BI_ARCHITECTURE.md``).

``mart_builder`` writes the ``bi_*`` marts from the canonical book, the live
plane and the sealed runs; ``reconciliation`` grades what it wrote (R1–R9 →
trust); ``partitions`` is the thin Postgres partition-lifecycle wrapper;
``compiler`` turns a ``BiQuery`` into SQLAlchemy Core. Nothing in this package
writes a canonical, regulatory or live table, calls ``derive_facts``, or
records a governed-parameter read.
"""
