"""Market-desk ingestion sources (spec §3 Tier-1 Ghana, §4 desk model).

One module per source family plus a shared core:

- ``core``       — SourceSpec/ParseResult/ObservationDraft, the validation
                   layer, and ``ingest_capture`` (the capture -> observations
                   write path with supersession).
- ``fetch``      — the capture client: proven BoG/GFIM/GSS mechanics with an
                   injected httpx client (fully unit-testable offline).
- ``bog_wdt``    — BoG wpDataTables JSON pages (t-bills, BoG bills,
                   interbank, MPR, FX, monthly matrix).
- ``bog_auctions`` — weekly GoG/BoG auction-result notices (PDF).
- ``bog_apr_pdf``  — monthly APR-of-banks competitor lending rates (PDF).
- ``bog_sefd_pdf`` — Summary of Economic and Financial Data (PDF, §3a/3b).
- ``gfim``       — GFIM daily trading XLSX + monthly status report extract.
- ``gss_pxweb``  — GSS statsbank PxWeb (GRR/IWAL/lending, stale but real).

Every source declares ``manual_fallback=True``: BoG publishes HTML/PDF only
and blocks automated access, so the desk can always fall back to
``observations.record_manual_observation`` for the series each source owns
(spec §3 ingestion-method summary).
"""

from __future__ import annotations

from app.services.market_desk.sources import (
    bog_apr_pdf,
    bog_auctions,
    bog_sefd_pdf,
    bog_wdt,
    fetch,
    gfim,
    gss_pxweb,
)
from app.services.market_desk.sources.core import (
    FetchMethod,
    ObservationDraft,
    ParseContext,
    ParseResult,
    SourceParser,
    SourceSpec,
    ingest_capture,
)

SOURCE_REGISTRY: dict[str, SourceSpec] = {
    spec.source_key: spec
    for spec in (
        SourceSpec(
            source_key="bog_tbill_rates",
            display_name="BoG treasury bill / GoG tender rates (table 2)",
            cadence="weekly",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table2_tbill_rates,
            parser_version="bog_wdt/1",
            series_codes=(
                "GHS.TBILL.{tenor}.DISCOUNT",
                "GHS.TBILL.{tenor}.INTEREST",
                "GHS.GOG.{security}.DISCOUNT",
                "GHS.GOG.{security}.INTEREST",
            ),
            unsupported=("rows before 2013-08-26 (view exposes 1361 of 1985 rows)",),
        ),
        SourceSpec(
            source_key="bog_bill_rates",
            display_name="BoG bill tender rates (table 3)",
            cadence="weekly",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table3_bog_bill_rates,
            parser_version="bog_wdt/1",
            series_codes=("GHS.BOGBILL.{tenor}.DISCOUNT", "GHS.BOGBILL.{tenor}.INTEREST"),
        ),
        SourceSpec(
            source_key="bog_interbank_daily",
            display_name="BoG daily interbank weighted-average rate (table 69)",
            cadence="daily",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table69_interbank_daily,
            parser_version="bog_wdt/1",
            series_codes=("GHS.INTERBANK.ON",),
        ),
        SourceSpec(
            source_key="bog_interbank_weekly",
            display_name="BoG weekly average interbank rate (table 70)",
            cadence="weekly",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table70_interbank_weekly,
            parser_version="bog_wdt/1",
            series_codes=("GHS.INTERBANK.WAVG",),
        ),
        SourceSpec(
            source_key="bog_mpr",
            display_name="BoG MPC policy rate (table 62)",
            cadence="per_event",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table62_mpr,
            parser_version="bog_wdt/1",
            series_codes=("GHS.MPR",),
            unsupported=(
                "table 63 (MPC rate minus 200bps): derived corridor-floor display, "
                "not independent data — deliberately not parsed",
            ),
        ),
        SourceSpec(
            source_key="bog_fx_daily",
            display_name="BoG daily interbank FX rates (table 31, latest day only)",
            cadence="daily",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_fx_pairs,
            parser_version="bog_wdt/1",
            series_codes=("GHS.FX.{pair}.BUY", "GHS.FX.{pair}.SELL", "GHS.FX.{pair}.MID"),
            unsupported=("history (table 31 serves only the latest day; use table 40)",),
        ),
        SourceSpec(
            source_key="bog_fx_historical",
            display_name="BoG historical interbank FX rates (table 40)",
            cadence="daily",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_fx_pairs,
            parser_version="bog_wdt/1",
            series_codes=("GHS.FX.{pair}.BUY", "GHS.FX.{pair}.SELL", "GHS.FX.{pair}.MID"),
        ),
        SourceSpec(
            source_key="bog_fx_reference",
            display_name="BoG FX market reference rate banner (table 32)",
            cadence="daily",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table32_fx_reference,
            parser_version="bog_wdt/1",
            series_codes=("GHS.FX.USDGHS.REF",),
            notes="Single text cell, no date column — as-of comes from the capture.",
        ),
        SourceSpec(
            source_key="bog_econ_interest_monthly",
            display_name="BoG monthly interest-rate matrix (table 21)",
            cadence="monthly",
            fetch_method=FetchMethod.WDT_AJAX,
            parser=bog_wdt.parse_table21_monthly_matrix,
            parser_version="bog_wdt/1",
            series_codes=("GHS.ECONDATA.{variable}",),
            unsupported=("data after 2023 (the table is no longer updated)",),
        ),
        SourceSpec(
            source_key="bog_gog_auction_pdf",
            display_name="GoG securities auction result notice (weekly PDF)",
            cadence="weekly",
            fetch_method=FetchMethod.PDF,
            parser=bog_auctions.parse_gog_auction_pdf,
            parser_version="bog_auctions/1",
            series_codes=("GHS.AUCTION.GOG.{tenor}.DISCOUNT", "GHS.AUCTION.GOG.{tenor}.INTEREST"),
        ),
        SourceSpec(
            source_key="bog_bog_auction_pdf",
            display_name="BoG bill auction result notice (weekly PDF)",
            cadence="weekly",
            fetch_method=FetchMethod.PDF,
            parser=bog_auctions.parse_bog_auction_pdf,
            parser_version="bog_auctions/1",
            series_codes=("GHS.AUCTION.BOG.{tenor}.DISCOUNT", "GHS.AUCTION.BOG.{tenor}.INTEREST"),
        ),
        SourceSpec(
            source_key="bog_apr_pdf",
            display_name="BoG Annual Percentage Rates of banks (monthly PDF)",
            cadence="monthly",
            fetch_method=FetchMethod.PDF,
            parser=bog_apr_pdf.parse_apr_pdf,
            parser_version="bog_apr_pdf/1",
            series_codes=("GHS.APR.{bank}.{segment}_{tenor}Y",),
        ),
        SourceSpec(
            source_key="bog_sefd_pdf",
            display_name="BoG Summary of Economic and Financial Data (bimonthly PDF)",
            cadence="monthly",
            fetch_method=FetchMethod.PDF,
            parser=bog_sefd_pdf.parse_sefd_pdf,
            parser_version="bog_sefd_pdf/1",
            series_codes=("GHS.SEFD.{row}", "GHS.SEFD.BOND.{tenor}Y"),
            unsupported=bog_sefd_pdf.UNSUPPORTED_SECTIONS,
        ),
        SourceSpec(
            source_key="gfim_daily_xlsx",
            display_name="GFIM daily trading report (XLSX)",
            cadence="daily",
            fetch_method=FetchMethod.XLSX,
            parser=gfim.parse_gfim_daily_xlsx,
            parser_version="gfim/1",
            series_codes=(
                "GHS.GFIM.{isin}.YIELD",
                "GHS.GFIM.{isin}.PRICE",
                "GHS.GFIM.{isin}.VOLUME",
                "GHS.GFIM.{isin}.TRADES",
                "GHS.GFIM.SBB.{isin}.*",
            ),
        ),
        SourceSpec(
            source_key="gfim_monthly_status",
            display_name="GFIM monthly status report (PDF text extract)",
            cadence="monthly",
            fetch_method=FetchMethod.PDF,
            parser=gfim.parse_gfim_status_report,
            parser_version="gfim/1",
            series_codes=(
                "GHS.GFIM.MONTHLY.VOLUME",
                "GHS.GFIM.MONTHLY.VALUE_GHS",
                "GHS.GFIM.MONTHLY.TRADES",
            ),
            unsupported=(
                "US$ trade statistics",
                "outstanding-securities tables",
                "primary-market activities",
                "member rankings",
                "yield-curve chart",
            ),
        ),
        SourceSpec(
            source_key="gss_interest_px",
            display_name="GSS statsbank interest rates (PxWeb)",
            cadence="monthly",
            fetch_method=FetchMethod.PXWEB_API,
            parser=gss_pxweb.parse_interest_px,
            parser_version="gss_pxweb/1",
            series_codes=tuple(sorted(set(gss_pxweb.SERIES_CODES.values()))),
            unsupported=("data after 2024M07 (table last updated 2024-09-16 — stale)",),
        ),
    )
}

__all__ = [
    "SOURCE_REGISTRY",
    "FetchMethod",
    "ObservationDraft",
    "ParseContext",
    "ParseResult",
    "SourceParser",
    "SourceSpec",
    "bog_apr_pdf",
    "bog_auctions",
    "bog_sefd_pdf",
    "bog_wdt",
    "fetch",
    "gfim",
    "gss_pxweb",
    "ingest_capture",
]
