"""Capture-client mechanics, fully offline: nonce extraction against the
REAL captured host pages, and the wire protocols against mocked transports
(httpx.MockTransport — the injected-client seam)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app.services.market_desk.sources import fetch
from tests.services.market_desk_sources.conftest import read_fixture


def _session(handler, *, pacer: fetch.Pacer | None = None) -> fetch.DeskSession:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return fetch.DeskSession(client, pacer=pacer or fetch.Pacer(0.0))


class TestClientPolicy:
    def test_verify_disabled_only_for_bog_hosts(self) -> None:
        # README §1: bog.gov.gh serves a broken TLS chain; every request
        # also needs a browser User-Agent (bot filter).
        for host in fetch.BOG_HOSTS:
            kwargs = fetch.client_kwargs(host)
            assert kwargs["verify"] is False
            assert "Mozilla/5.0" in kwargs["headers"]["User-Agent"]
        gfim_kwargs = fetch.client_kwargs("gfim.com.gh")
        assert "verify" not in gfim_kwargs  # normal TLS everywhere else
        gss_kwargs = fetch.client_kwargs("statsbank.statsghana.gov.gh")
        assert "verify" not in gss_kwargs


class TestNonceExtraction:
    @pytest.mark.parametrize(
        ("fixture", "table_id", "nonce"),
        [
            ("bog_page_treasury-bill-rates.html", 2, "4fb46df3b9"),
            ("bog_page_interbank-interest-rates.html", 69, "da9a4b92c3"),
            ("bog_page_interbank-interest-rates.html", 70, "a5cc614ab4"),
            ("bog_page_interbank-interest-rates.html", 62, "12b61a0afa"),
            ("bog_page_interbank-interest-rates.html", 63, "531fe353e0"),
            ("bog_page_historical-interbank-fx-rates.html", 40, "c2041d81bf"),
            ("bog_page_economic-data-interest-rates.html", 21, "47e848841d"),
        ],
    )
    def test_per_table_nonce_from_real_host_pages(
        self, fixture: str, table_id: int, nonce: str
    ) -> None:
        html = read_fixture(fixture).decode(errors="replace")
        assert fetch.extract_wdt_nonce(html, table_id) == nonce

    def test_missing_nonce_raises(self) -> None:
        with pytest.raises(fetch.FetchError, match="nonce"):
            fetch.extract_wdt_nonce("<html></html>", 2)

    def test_dom_order_does_not_equal_table_id_order(self) -> None:
        # README: on the interbank page table_1..table_4 map to 69,70,62,63 —
        # data-wpdatatable_id is the truth, never positional assumptions.
        html = read_fixture("bog_page_interbank-interest-rates.html").decode(errors="replace")
        assert fetch.extract_wpdatatable_ids(html) == [69, 70, 62, 63]


class TestWdtPaging:
    def _handler_factory(self, rows: list[list[str]], page_length: int, table_id: int = 69):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=f'name="wdtNonceFrontendServerSide_{table_id}" value="da9a4b92c3"',
                )
            form = parse_qs(request.content.decode())
            start = int(form["start"][0])
            page = rows[start : start + page_length]
            return httpx.Response(
                200,
                json={
                    "draw": int(form["draw"][0]),
                    # recordsTotal LIES by design (shared underlying table).
                    "recordsTotal": 38145,
                    "recordsFiltered": len(rows),
                    "data": page,
                },
            )

        return handler, requests

    def test_pages_by_records_filtered_never_records_total(self) -> None:
        rows = [[str(i), "07 Aug 2026", "10.23"] for i in range(5)]
        handler, requests = self._handler_factory(rows, page_length=2)
        fetches = fetch.fetch_wdt_table(
            _session(handler),
            table_id=69,
            page_url="https://www.bog.gov.gh/treasury-and-the-markets/interbank-interest-rates/",
            page_length=2,
        )
        # 5 rows at length=2 -> 3 data pages; a recordsTotal-driven loop
        # would have tried to page through 38145 phantom rows.
        assert len(fetches) == 3
        assert sum(f.meta["rows"] for f in fetches) == 5
        posts = [r for r in requests if r.method == "POST"]
        assert len(posts) == 3

    def test_post_carries_nonce_referer_and_never_global_search(self) -> None:
        rows = [["1", "07 Aug 2026", "10.23"]]
        handler, requests = self._handler_factory(rows, page_length=1000)
        page_url = "https://www.bog.gov.gh/treasury-and-the-markets/interbank-interest-rates/"
        fetch.fetch_wdt_table(_session(handler), table_id=69, page_url=page_url)
        post = next(r for r in requests if r.method == "POST")
        form = parse_qs(post.content.decode())
        assert form["wdtNonce"] == ["da9a4b92c3"]
        assert post.headers["Referer"] == page_url
        assert "action=get_wdtable" in str(post.url)
        assert "table_id=69" in str(post.url)
        # The global search[value] is broken site-wide — it must NEVER be sent.
        assert not any(key.startswith("search") for key in form)

    def test_per_column_search_is_the_filtering_mechanism(self) -> None:
        rows = [["07 Aug 2026", "US Dollar", "USDGHS", "11.7556", "11.7674", "11.7615"]]
        handler, requests = self._handler_factory(rows, page_length=1000, table_id=40)
        fetch.fetch_wdt_table(
            _session(handler),
            table_id=40,
            page_url="https://www.bog.gov.gh/treasury-and-the-markets/historical-interbank-fx-rates/",
            column_search={2: "USDGHS"},
        )
        post = next(r for r in requests if r.method == "POST")
        form = parse_qs(post.content.decode())
        assert form["columns[2][search][value]"] == ["USDGHS"]
        assert form["columns[2][search][regex]"] == ["false"]
        assert form["columns[2][data]"] == ["2"]


class TestWdtWatermark:
    """A walk is bounded by what the desk already holds.

    Table 40 (historical interbank FX) is the whole published archive —
    144,647 rows on 2026-08-19, 145 pages of 1,000 — and it grows with
    HISTORY, not with news. An unbounded nightly walk therefore costs more
    every night forever while learning one new day. The bound is the newest
    date already captured; the pacing is untouched (the defect is request
    COUNT, not request rate).
    """

    NEWEST = date(2026, 8, 19)

    def _fx_rows(self, days: int) -> list[list[str]]:
        """Table 40's shape, newest first — the order the request asks for
        (``order[0][column]=0``, ``dir=desc``, and column 0 IS the date)."""
        rows: list[list[str]] = []
        for offset in range(days):
            label = (self.NEWEST - timedelta(days=offset)).strftime("%d %b %Y")
            rows.append([label, "US Dollar", "USDGHS", "11.7556", "11.7674", "11.7615"])
            rows.append([label, "Euro", "EURGHS", "13.5945", "13.6080", "13.6013"])
        return rows

    def _handler(self, rows: list[list[str]], *, page_length: int, table_id: int):
        posts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=f'name="wdtNonceFrontendServerSide_{table_id}" value="c2041d81bf"',
                )
            form = parse_qs(request.content.decode())
            start = int(form["start"][0])
            posts.append(start)
            return httpx.Response(
                200,
                json={
                    "draw": int(form["draw"][0]),
                    "recordsTotal": 144647,
                    "recordsFiltered": len(rows),
                    "data": rows[start : start + page_length],
                },
            )

        return handler, posts

    def _walk(  # noqa: PLR0913 - mirrors fetch_wdt_table's own knobs
        self,
        rows: list[list[str]],
        *,
        page_length: int,
        since: date | None,
        date_column: int | None = 0,
        table_id: int = 40,
    ) -> tuple[list[fetch.RawFetch], list[int]]:
        handler, posts = self._handler(rows, page_length=page_length, table_id=table_id)
        fetches = fetch.fetch_wdt_table(
            _session(handler),
            table_id=table_id,
            page_url="https://www.bog.gov.gh/treasury-and-the-markets/"
            "historical-interbank-fx-rates/",
            page_length=page_length,
            since=since,
            date_column=date_column,
        )
        return fetches, posts

    def test_no_watermark_walks_the_whole_archive(self) -> None:
        """A cold start has nothing to bound the walk with, so it pages in
        full — deliberately. A watermark invented to avoid a first backfill
        would silently truncate the history it claims to hold."""
        rows = self._fx_rows(days=40)  # 80 rows
        _, posts = self._walk(rows, page_length=10, since=None)
        assert len(posts) == 8
        assert posts == [0, 10, 20, 30, 40, 50, 60, 70]

    def test_watermark_stops_at_the_first_page_that_reaches_back(self) -> None:
        """Newest-first paging means the first page carrying a row at or
        before the watermark is the last page that can hold anything new."""
        rows = self._fx_rows(days=40)
        # 10 rows/page = 5 published days/page. Page 1 covers 19..15 Aug (all
        # newer than the watermark); page 2 covers 14..10 Aug and contains it.
        fetches, posts = self._walk(rows, page_length=10, since=date(2026, 8, 14))
        assert posts == [0, 10]
        assert len(fetches) == 2

    def test_the_page_that_crosses_the_watermark_is_kept_whole(self) -> None:
        """The bound narrows the FETCH, never the bytes: the crossing page is
        stored and parsed exactly as the site served it, so the ingest path
        sees identical input to an unbounded walk."""
        rows = self._fx_rows(days=40)
        bounded, _ = self._walk(rows, page_length=10, since=date(2026, 8, 14))
        unbounded, _ = self._walk(rows, page_length=10, since=None)
        assert [f.sha256 for f in bounded] == [f.sha256 for f in unbounded[:2]]
        assert bounded[1].meta["rows"] == 10

    def test_todays_watermark_costs_exactly_one_page(self) -> None:
        """The nightly case: yesterday is already held, so one page answers
        the whole question — 1 POST where the archive walk takes 145."""
        rows = self._fx_rows(days=400)  # 800 rows
        _, posts = self._walk(rows, page_length=1000, since=date(2026, 8, 18))
        assert posts == [0]

    def test_watermark_is_ignored_when_the_table_has_no_date_column(self) -> None:
        """Tables 62/69/70 order by an ID column and tables 21/32 have no date
        at all, so a page-level date bound is not sound there — they keep the
        unbounded walk rather than guess. None exceeds two pages of 1,000."""
        rows = self._fx_rows(days=40)
        _, posts = self._walk(rows, page_length=10, since=date(2026, 8, 14), date_column=None)
        assert len(posts) == 8

    def test_unreadable_date_cells_never_shorten_a_walk(self) -> None:
        """BoG really does ship empty date cells (table 62 has two). An
        unparseable cell must not be read as 'old enough to stop'."""
        rows = [["", "US Dollar", "USDGHS", "11.75", "11.77", "11.76"] for _ in range(20)]
        rows += self._fx_rows(days=5)
        _, posts = self._walk(rows, page_length=10, since=date(2026, 8, 14))
        assert len(posts) == 3  # 30 rows / 10, no early stop from the blank pages

    def test_dispatch_bounds_table_40_and_leaves_table_69_unbounded(self) -> None:
        """``fetch_source`` carries the watermark to the registered view; the
        registry decides whether that view can honour it.

        Same rows, same watermark, at the production page length of 1,000:
        table 40's date IS its ordered column, so one page answers the night;
        table 69 orders by an ID column, so it pages in full.
        """
        rows = self._fx_rows(days=1500)  # 3,000 rows = 3 pages of 1,000
        watermark = self.NEWEST - timedelta(days=400)
        results: dict[str, list[int]] = {}
        for source_key, table_id in (("bog_fx_historical", 40), ("bog_interbank_daily", 69)):
            handler, posts = self._handler(
                rows, page_length=fetch.WDT_PAGE_LENGTH, table_id=table_id
            )
            fetch.fetch_source(source_key, _session(handler), since=watermark)
            results[source_key] = posts
        assert results["bog_fx_historical"] == [0]
        assert results["bog_interbank_daily"] == [0, 1000, 2000]

    def test_registry_marks_only_date_ordered_views_as_watermarkable(self) -> None:
        """``date_column`` is a claim about SOUNDNESS, not a convenience: it
        is set only where column 0 — the column every request orders by —
        is the published date."""
        for bounded in ("bog_fx_historical", "bog_fx_daily", "bog_tbill_rates",
                        "bog_bill_rates"):
            assert fetch._WDT_SOURCES[bounded].date_column == 0, bounded
        for unbounded in ("bog_interbank_daily", "bog_interbank_weekly", "bog_mpr",
                          "bog_fx_reference", "bog_econ_interest_monthly"):
            assert fetch._WDT_SOURCES[unbounded].date_column is None, unbounded


class TestPacing:
    def test_minimum_two_seconds_between_requests(self) -> None:
        sleeps: list[float] = []
        clock_value = 0.0

        def clock() -> float:
            return clock_value

        def sleeper(seconds: float) -> None:
            nonlocal clock_value
            sleeps.append(seconds)
            clock_value += seconds

        pacer = fetch.Pacer(2.0, sleeper=sleeper, clock=clock)
        pacer.wait()  # first request: no sleep
        clock_value += 0.5  # request took half a second
        pacer.wait()  # 1.5s still owed
        assert sleeps == [1.5]

    def test_no_sleep_when_naturally_slower_than_interval(self) -> None:
        sleeps: list[float] = []
        clock_value = 0.0
        pacer = fetch.Pacer(2.0, sleeper=sleeps.append, clock=lambda: clock_value)
        pacer.wait()
        clock_value += 5.0
        pacer.wait()
        assert sleeps == []

    def test_session_paces_every_request(self) -> None:
        waits: list[bool] = []

        class SpyPacer(fetch.Pacer):
            def wait(self) -> None:
                waits.append(True)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        session = _session(handler, pacer=SpyPacer(0.0))
        session.get("https://gfim.com.gh/a")
        session.post("https://gfim.com.gh/b", json_body={})
        assert len(waits) == 2


class TestFileBird:
    def test_config_from_real_daily_reports_page(self) -> None:
        html = read_fixture("gfim_daily_trading_reports_page.html").decode(errors="replace")
        config = fetch.extract_filebird_config(html)
        assert config.json_url == "https://gfim.com.gh/wp-json/filebird/v1"
        assert config.rest_nonce == "dd8a1182f6"
        # First widget = newest year; labels pair in document order.
        assert [w.year_label for w in config.widgets] == ["2026", "2025", "2024", "2023"]
        first = config.widgets[0].request
        assert first["selectedFolder"] == ["uk+N1Df7mDmi3uS44m8kXQ=="]
        assert first["orderBy"] == "post_date"

    def test_listing_and_download_flow(self) -> None:
        page_html = read_fixture("gfim_daily_trading_reports_page.html").decode(
            errors="replace"
        )
        listing = json.loads(
            read_fixture("gfim_filebird_get_attachments_daily2026_response.json")
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            url = str(request.url)
            if url.endswith("/daily-trading-reports/"):
                return httpx.Response(200, text=page_html)
            if url.endswith("/get-attachments"):
                assert request.headers["X-WP-Nonce"] == "dd8a1182f6"
                body = json.loads(request.content)
                assert body["selectedFolder"] == ["uk+N1Df7mDmi3uS44m8kXQ=="]
                return httpx.Response(200, json=listing)
            return httpx.Response(200, content=b"xlsx-bytes")

        fetches = fetch.fetch_filebird_files(
            _session(handler),
            page_url="https://gfim.com.gh/daily-trading-reports/",
            limit=1,
        )
        assert fetches[0].meta["kind"] == "filebird_listing"
        assert fetches[1].meta["kind"] == "report_file"
        assert fetches[1].meta["title"] == "TRADING REPORT FOR GFIM-07082026"
        assert fetches[1].content == b"xlsx-bytes"
        assert len([r for r in requests if r.method == "GET"]) == 2  # page + one file

    def test_unknown_year_tab_raises(self) -> None:
        page_html = read_fixture("gfim_daily_trading_reports_page.html").decode(
            errors="replace"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=page_html)

        with pytest.raises(fetch.FetchError, match="year tab"):
            fetch.fetch_filebird_files(
                _session(handler),
                page_url="https://gfim.com.gh/daily-trading-reports/",
                year="1999",
            )


class TestMonthlyStatusFetch:
    def test_selects_newest_status_report_by_title(self) -> None:
        # The monthly folder is pinned by title: the newest STATUS REPORT is
        # taken, never whatever file happens to sort first.
        page_html = read_fixture("gfim_monthly_reports_page.html").decode(errors="replace")
        listing = json.loads(
            read_fixture("gfim_filebird_get_attachments_monthly2026_response.json")
        )

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/monthly-reports/"):
                return httpx.Response(200, text=page_html)
            if url.endswith("/get-attachments"):
                return httpx.Response(200, json=listing)
            return httpx.Response(200, content=b"%PDF-1.4 status-report-bytes")

        fetches = fetch.fetch_source("gfim_monthly_status", _session(handler))
        reports = [f for f in fetches if f.meta.get("kind") == "report_file"]
        assert len(reports) == 1
        assert "Status Report" in str(reports[0].meta["title"])

    def test_no_status_report_is_soft_not_yet_published(self) -> None:
        # Folder resolves but carries no status report yet (early in the
        # period): a soft SourceNotYetPublished, not a hard fetch failure.
        page_html = read_fixture("gfim_monthly_reports_page.html").decode(errors="replace")
        listing = {"files": [{"title": "Yield Curve Deck", "url": "https://gfim.com.gh/x.pdf"}]}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/monthly-reports/"):
                return httpx.Response(200, text=page_html)
            if url.endswith("/get-attachments"):
                return httpx.Response(200, json=listing)
            return httpx.Response(200, content=b"pdf")

        with pytest.raises(fetch.SourceNotYetPublished):
            fetch.fetch_source("gfim_monthly_status", _session(handler))

    def test_not_yet_published_is_a_fetch_error_subclass(self) -> None:
        # Existing ``except FetchError`` sites keep catching it; the capture
        # job special-cases it ahead of the generic handler.
        assert issubclass(fetch.SourceNotYetPublished, fetch.FetchError)


class TestAuctionFlow:
    def test_index_to_tender_page_to_pdf(self) -> None:
        index_html = read_fixture("bog_auction_results_index.html").decode(errors="replace")
        tender_html = read_fixture("bog_tender_873_2026-08-05.html").decode(errors="replace")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.rstrip("/").endswith("bog_auction_results"):
                return httpx.Response(200, text=index_html)
            if "results-of-tender" in url:
                return httpx.Response(200, text=tender_html)
            assert url.endswith(".pdf")
            return httpx.Response(200, content=b"pdf-bytes")

        fetches = fetch.fetch_auction_results(
            _session(handler), index_url=fetch.BOG_AUCTION_INDEX_URL, limit=1
        )
        assert [f.meta["kind"] for f in fetches] == ["tender_page", "result_pdf"]
        assert fetches[1].content == b"pdf-bytes"

    def test_tender_slugs_from_real_indexes(self) -> None:
        bog_index = read_fixture("bog_auction_results_index.html").decode(errors="replace")
        slugs = fetch.extract_tender_slugs(bog_index, base_url=fetch.BOG_AUCTION_INDEX_URL)
        assert any("results-of-tender-873-held-on-5-august-2026" in s for s in slugs)
        gog_index = read_fixture("gog_auction_results_index.html").decode(errors="replace")
        gog_slugs = fetch.extract_tender_slugs(
            gog_index, base_url=fetch.GOG_AUCTION_INDEX_URL
        )
        assert any("results-of-gog-tender-2019" in s for s in gog_slugs)


class TestPxWeb:
    def test_post_body_shape(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"columns": [], "data": []})

        fetch.fetch_pxweb_table(
            _session(handler), rate_values=["Ghana reference rate"]
        )
        body = json.loads(requests[0].content)
        assert body["response"] == {"format": "json"}
        assert body["query"] == [
            {
                "code": "Rate",
                "selection": {"filter": "item", "values": ["Ghana reference rate"]},
            }
        ]


class TestDispatch:
    def test_publication_sources_build_month_slugged_urls(self) -> None:
        assert fetch.apr_notice_url(date(2026, 5, 1)) == (
            "https://www.bog.gov.gh/notice/"
            "annual-percentage-rates-apr-of-banks-as-at-may-2026/"
        )
        assert fetch.sefd_page_url(date(2026, 7, 1)) == (
            "https://www.bog.gov.gh/econ_fin_data/"
            "summary-of-economic-and-financial-data-july-2026/"
        )

    def test_unknown_source_key_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        with pytest.raises(fetch.FetchError, match="no fetch mechanics"):
            fetch.fetch_source("nope", _session(handler))

    def test_publication_sources_require_period(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        with pytest.raises(fetch.FetchError, match="period"):
            fetch.fetch_source("bog_apr_pdf", _session(handler))
