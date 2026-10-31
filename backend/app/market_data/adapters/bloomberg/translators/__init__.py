"""Bloomberg translators (market_data_adapter.md §6.4).

Translators convert the extractors' Bloomberg-shaped intermediate structures
into the vendor-agnostic record dataclasses the shared pull runner persists
(``CurveRecord``/``FxRateRecord``/``RatingRecord`` inside a
``MarketDataBundle``). The pull runner stamps the mandatory metadata
(``source_system='BLOOMBERG'``, ``ingestion_batch_id``, ``lineage_id``, ...);
translators own the value semantics: PX_LAST yields arrive as percents and
become decimal fractions, FX prices pass through untouched, rating mnemonics
map to canonical agencies. ``source_reference`` is always the specific
Bloomberg security (or security/field) reference.
"""
