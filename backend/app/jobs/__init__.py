"""Worker job handlers that are not owned by a service module.

The live-engine handlers live beside the services they drive
(``app/live/service/pipeline.py``, ``etl_dedup_jobs.py``, …). The ``bi`` lane's
handlers live here instead because they are thin dispatchers over a builder
that is imported lazily (``bi_common.load_builder``), so the worker registers
them before the builder ships.
"""
