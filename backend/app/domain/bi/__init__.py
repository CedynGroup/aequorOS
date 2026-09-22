"""The BI plane's pure layer: mart-row extraction and the metric catalogue.

Everything under ``app.domain.bi`` is dispatch-plane analytics over data the
regulatory plane already produced. It imports nothing from ``app.services`` or
``app.models`` (pinned by ``tests/architecture``), never recomputes an engine
figure, and never names a currency or a regulator — the reporting currency is
whatever ``jurisdictions.base_currency(bank)`` says, passed in by the caller.
"""
