"""The capture client — proven fetch mechanics for every Tier-1 source.

Everything here was reverse-engineered on the real sites during the
2026-08-09 harvest (fixtures README) and is unit-testable WITHOUT network:
the ``httpx.Client`` is injected (tests pass ``httpx.MockTransport``) and
pacing sleeps through an injectable sleeper.

Site contracts encoded here:

**BoG (www.bog.gov.gh)** — broken TLS chain, so the client for this host
(and ONLY this host) is built with ``verify=False``; a browser User-Agent is
mandatory (the bot filter rejects bare clients) and data requests must carry
a ``Referer`` naming the page the table lives on. wpDataTables protocol:
GET the host page, scrape the per-table nonce
(``name="wdtNonceFrontendServerSide_<id>" value="..."`` — nonces rotate, so
re-extract per session), then POST ``admin-ajax.php?action=get_wdtable``
paging with ``start``/``length``. Row counts come from ``recordsFiltered``;
``recordsTotal`` is the shared underlying table and LIES (all interbank
views report 38145). The global ``search[value]`` is broken site-wide
(nonzero count, zero rows) — this module never sends it; per-column
``columns[i][search][value]`` is the only filtering mechanism.

**GFIM (gfim.com.gh)** — normal TLS. Report lists render client-side via the
FileBird Document Library plugin: scrape ``var fbdl = {json_url,
rest_nonce}`` plus each year-tab widget's HTML-entity-encoded ``data-json``
request, then POST ``<json_url>/get-attachments`` with the request object
and ``X-WP-Nonce``; files are plain uploads GETs.

**GSS statsbank** — standard PxWeb: POST a JSON query to the table URL.

Pacing: minimum 2 seconds between requests, enforced on every request the
session makes.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from datetime import date

BOG_HOSTS = ("www.bog.gov.gh", "bog.gov.gh")
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MIN_REQUEST_INTERVAL_SECONDS = 2.0
WDT_PAGE_LENGTH = 1000

BOG_ADMIN_AJAX = "https://www.bog.gov.gh/wp-admin/admin-ajax.php"

_NONCE_RE_TEMPLATE = r'name="wdtNonceFrontendServerSide_{table_id}"\s+value="([0-9a-f]+)"'
_WPDATATABLE_ID_RE = re.compile(r'data-wpdatatable_id="(\d+)"')
_FBDL_CONFIG_RE = re.compile(r"var\s+fbdl\s*=\s*(\{.*?\});", re.DOTALL)
_FBDL_WIDGET_RE = re.compile(r'class="njt-fbdl"\s+data-json="([^"]+)"')
# The page's inline CSS also mentions .wpb_tabs_nav — anchor on the real <ul>.
_TAB_LABEL_RE = re.compile(r'<ul class="wpb_tabs_nav[^"]*">.*?</ul>', re.DOTALL)
_TAB_YEAR_RE = re.compile(r"<span>(\d{4})</span>")
_UPLOADS_PDF_RE = re.compile(r'href="([^"]*wp-content/uploads[^"]*\.pdf)"')


class FetchError(RuntimeError):
    """A source's published mechanics did not hold on this fetch."""


@dataclass(frozen=True)
class RawFetch:
    """One raw response, exactly as fetched — the future capture payload."""

    url: str
    content: bytes
    sha256: str
    meta: dict[str, Any] = field(default_factory=dict)


def _raw(url: str, content: bytes, meta: dict[str, Any] | None = None) -> RawFetch:
    return RawFetch(
        url=url,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        meta=meta or {},
    )


def client_kwargs(host: str) -> dict[str, Any]:
    """httpx.Client kwargs for a source host.

    ``verify=False`` is applied ONLY to bog.gov.gh: the site serves a broken
    TLS chain (fixtures README §1) and every harvested byte required it.
    Never widen this to other hosts.
    """
    if host in BOG_HOSTS:
        return {
            "verify": False,
            "headers": {"User-Agent": BROWSER_USER_AGENT},
            "follow_redirects": True,
            "trust_env": False,
        }
    return {
        "headers": {"User-Agent": BROWSER_USER_AGENT},
        "follow_redirects": True,
        "trust_env": False,
    }


class Pacer:
    """Minimum-interval throttle with injectable clock/sleeper (testable)."""

    def __init__(
        self,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = min_interval
        self._sleeper = sleeper
        self._clock = clock
        self._last_request: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            remaining = self.min_interval - (now - self._last_request)
            if remaining > 0:
                self._sleeper(remaining)
                now = self._clock()
        self._last_request = now


class DeskSession:
    """Injected-transport HTTP session: pacing + Referer discipline."""

    def __init__(self, client: httpx.Client, *, pacer: Pacer | None = None) -> None:
        self._client = client
        self._pacer = pacer or Pacer()

    def get(self, url: str, *, referer: str | None = None) -> httpx.Response:
        self._pacer.wait()
        headers = {"Referer": referer} if referer else None
        response = self._client.get(url, headers=headers)
        response.raise_for_status()
        return response

    def post(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json_body: Any | None = None,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self._pacer.wait()
        merged = dict(headers or {})
        if referer:
            merged["Referer"] = referer
        response = self._client.post(url, data=data, json=json_body, headers=merged or None)
        response.raise_for_status()
        return response


# ---------------------------------------------------------------------------
# BoG wpDataTables
# ---------------------------------------------------------------------------


def extract_wdt_nonce(page_html: str, table_id: int) -> str:
    """Per-table nonce from the host page. Nonces rotate — never cache one
    across sessions."""
    match = re.search(_NONCE_RE_TEMPLATE.format(table_id=table_id), page_html)
    if match is None:
        raise FetchError(f"no wdtNonceFrontendServerSide_{table_id} nonce on host page")
    return match.group(1)


def extract_wpdatatable_ids(page_html: str) -> list[int]:
    """DOM-order wpdatatable ids. DOM order does NOT equal table_id order
    (the interbank page maps table_1..4 -> 69, 70, 62, 63) — always read
    ``data-wpdatatable_id``, never assume."""
    return [int(m) for m in _WPDATATABLE_ID_RE.findall(page_html)]


def _wdt_form(
    *,
    nonce: str,
    start: int,
    length: int,
    draw: int,
    column_search: dict[int, str] | None,
) -> dict[str, str]:
    # NOTE deliberately absent: the global ``search[value]`` parameter. It is
    # broken on this deployment (returns a count but zero rows) — filtering
    # must use per-column search exclusively.
    form = {
        "draw": str(draw),
        "wdtNonce": nonce,
        "start": str(start),
        "length": str(length),
        "order[0][column]": "0",
        "order[0][dir]": "desc",
    }
    for column, value in (column_search or {}).items():
        form[f"columns[{column}][data]"] = str(column)
        form[f"columns[{column}][searchable]"] = "true"
        form[f"columns[{column}][search][value]"] = value
        form[f"columns[{column}][search][regex]"] = "false"
    return form


def fetch_wdt_table(  # noqa: PLR0913 - the wpDataTables protocol has this many knobs
    session: DeskSession,
    *,
    table_id: int,
    page_url: str,
    column_search: dict[int, str] | None = None,
    page_length: int = WDT_PAGE_LENGTH,
    max_pages: int = 200,
) -> list[RawFetch]:
    """Full wpDataTables pull: host page (nonce) + every data page.

    Paging is driven by ``recordsFiltered`` — the real row count of the
    view; ``recordsTotal`` is the shared underlying table and lies.
    """
    page_response = session.get(page_url)
    nonce = extract_wdt_nonce(page_response.text, table_id)
    ajax_url = f"{BOG_ADMIN_AJAX}?action=get_wdtable&table_id={table_id}"

    fetches: list[RawFetch] = []
    start = 0
    draw = 1
    while draw <= max_pages:
        response = session.post(
            ajax_url,
            data=_wdt_form(
                nonce=nonce,
                start=start,
                length=page_length,
                draw=draw,
                column_search=column_search,
            ),
            referer=page_url,
        )
        payload = response.json()
        rows = payload.get("data") or []
        records_filtered = int(payload.get("recordsFiltered") or 0)
        fetches.append(
            _raw(
                ajax_url,
                response.content,
                {
                    "table_id": table_id,
                    "start": start,
                    "records_filtered": records_filtered,
                    "rows": len(rows),
                },
            )
        )
        start += len(rows)
        draw += 1
        if not rows or start >= records_filtered:
            break
    return fetches


# ---------------------------------------------------------------------------
# BoG auction results & publications
# ---------------------------------------------------------------------------


def extract_tender_slugs(index_html: str, *, base_url: str) -> list[str]:
    """Tender-page URLs from an auction-results index, newest first,
    de-duplicated in document order."""
    pattern = re.compile(r'href="([^"]*results-of-(?:gog-)?tender-[^"]+)"')
    seen: set[str] = set()
    urls: list[str] = []
    for match in pattern.finditer(index_html):
        url = match.group(1)
        if not url.startswith("http"):
            url = base_url.rstrip("/") + "/" + url.lstrip("/")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_uploads_pdf_url(page_html: str) -> str:
    match = _UPLOADS_PDF_RE.search(page_html)
    if match is None:
        raise FetchError("no wp-content/uploads PDF link on page")
    return match.group(1)


def fetch_auction_results(
    session: DeskSession, *, index_url: str, limit: int = 1
) -> list[RawFetch]:
    """Index -> newest tender page(s) -> result PDF(s). The PDF is the data
    artifact (tender pages carry no inline tables); the page HTML rides
    along for lineage."""
    index_response = session.get(index_url)
    slugs = extract_tender_slugs(index_response.text, base_url=index_url)
    fetches: list[RawFetch] = []
    for url in slugs[:limit]:
        page_response = session.get(url, referer=index_url)
        fetches.append(_raw(url, page_response.content, {"kind": "tender_page"}))
        pdf_url = extract_uploads_pdf_url(page_response.text)
        pdf_response = session.get(pdf_url, referer=url)
        fetches.append(_raw(pdf_url, pdf_response.content, {"kind": "result_pdf"}))
    return fetches


def fetch_publication_pdf(
    session: DeskSession, *, page_url: str
) -> list[RawFetch]:
    """A BoG publication page (APR notice, SEFD edition) -> its PDF."""
    page_response = session.get(page_url)
    pdf_url = extract_uploads_pdf_url(page_response.text)
    pdf_response = session.get(pdf_url, referer=page_url)
    return [
        _raw(page_url, page_response.content, {"kind": "publication_page"}),
        _raw(pdf_url, pdf_response.content, {"kind": "publication_pdf"}),
    ]


def apr_notice_url(period: date) -> str:
    month = period.strftime("%B").lower()
    return (
        "https://www.bog.gov.gh/notice/"
        f"annual-percentage-rates-apr-of-banks-as-at-{month}-{period.year}/"
    )


def sefd_page_url(period: date) -> str:
    month = period.strftime("%B").lower()
    return (
        "https://www.bog.gov.gh/econ_fin_data/"
        f"summary-of-economic-and-financial-data-{month}-{period.year}/"
    )


# ---------------------------------------------------------------------------
# GFIM (FileBird Document Library)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileBirdWidget:
    year_label: str | None
    request: dict[str, Any]


@dataclass(frozen=True)
class FileBirdConfig:
    json_url: str
    rest_nonce: str
    widgets: tuple[FileBirdWidget, ...]


def extract_filebird_config(page_html: str) -> FileBirdConfig:
    """``var fbdl`` (API base + REST nonce) plus every year-tab widget's
    decoded ``data-json`` request, paired with the tab labels in document
    order (first widget = newest year)."""
    config_match = _FBDL_CONFIG_RE.search(page_html)
    if config_match is None:
        raise FetchError("no `var fbdl` FileBird config on page")
    config = json.loads(config_match.group(1))

    years: list[str] = []
    nav_match = _TAB_LABEL_RE.search(page_html)
    if nav_match:
        years = _TAB_YEAR_RE.findall(nav_match.group(0))

    widgets: list[FileBirdWidget] = []
    for index, encoded in enumerate(_FBDL_WIDGET_RE.findall(page_html)):
        payload = json.loads(html_lib.unescape(encoded))
        widgets.append(
            FileBirdWidget(
                year_label=years[index] if index < len(years) else None,
                request=payload.get("request", {}),
            )
        )
    if not widgets:
        raise FetchError("no njt-fbdl widgets on page")
    return FileBirdConfig(
        json_url=str(config["json_url"]),
        rest_nonce=str(config["rest_nonce"]),
        widgets=tuple(widgets),
    )


def fetch_filebird_files(
    session: DeskSession,
    *,
    page_url: str,
    year: str | None = None,
    limit: int = 1,
    name_filter: Callable[[str], bool] | None = None,
) -> list[RawFetch]:
    """Listing page -> get-attachments -> newest file(s) in the year folder."""
    page_response = session.get(page_url)
    config = extract_filebird_config(page_response.text)
    widget = config.widgets[0]
    if year is not None:
        matches = [w for w in config.widgets if w.year_label == year]
        if not matches:
            raise FetchError(f"no FileBird year tab labelled {year!r}")
        widget = matches[0]

    listing_response = session.post(
        f"{config.json_url}/get-attachments",
        json_body=widget.request,
        referer=page_url,
        headers={"X-WP-Nonce": config.rest_nonce},
    )
    files = listing_response.json().get("files", [])
    fetches: list[RawFetch] = [
        _raw(
            f"{config.json_url}/get-attachments",
            listing_response.content,
            {"kind": "filebird_listing", "year": widget.year_label},
        )
    ]
    for file_info in files:
        if limit <= 0:
            break
        url = file_info.get("url") or file_info.get("link")
        if not url:
            continue
        if name_filter is not None and not name_filter(str(file_info.get("title", ""))):
            continue
        file_response = session.get(url, referer=page_url)
        fetches.append(
            _raw(
                url,
                file_response.content,
                {"kind": "report_file", "title": file_info.get("title")},
            )
        )
        limit -= 1
    return fetches


# ---------------------------------------------------------------------------
# GSS statsbank (PxWeb)
# ---------------------------------------------------------------------------

GSS_INTEREST_PX_URL = (
    "https://statsbank.statsghana.gov.gh/api/v1/en/"
    "Macroeconomic%20Indicators/Monetary%20and%20Financial%20Sector/interest.px"
)


def fetch_pxweb_table(
    session: DeskSession,
    *,
    url: str = GSS_INTEREST_PX_URL,
    rate_values: Iterable[str] | None = None,
) -> list[RawFetch]:
    """POST the PxWeb query (all Rate values unless narrowed)."""
    query: list[dict[str, Any]] = []
    values = list(rate_values or [])
    if values:
        query.append({"code": "Rate", "selection": {"filter": "item", "values": values}})
    response = session.post(url, json_body={"query": query, "response": {"format": "json"}})
    return [_raw(url, response.content, {"kind": "pxweb_data"})]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_WDT_SOURCES: dict[str, tuple[int, str]] = {
    "bog_tbill_rates": (2, "https://www.bog.gov.gh/treasury-and-the-markets/treasury-bill-rates/"),
    "bog_bill_rates": (
        3,
        "https://www.bog.gov.gh/treasury-and-the-markets/bank-of-ghana-bill-rates/",
    ),
    "bog_interbank_daily": (
        69,
        "https://www.bog.gov.gh/treasury-and-the-markets/interbank-interest-rates/",
    ),
    "bog_interbank_weekly": (
        70,
        "https://www.bog.gov.gh/treasury-and-the-markets/interbank-interest-rates/",
    ),
    "bog_mpr": (62, "https://www.bog.gov.gh/treasury-and-the-markets/interbank-interest-rates/"),
    "bog_fx_daily": (
        31,
        "https://www.bog.gov.gh/treasury-and-the-markets/daily-interbank-fx-rates/",
    ),
    "bog_fx_reference": (
        32,
        "https://www.bog.gov.gh/treasury-and-the-markets/daily-interbank-fx-rates/",
    ),
    "bog_fx_historical": (
        40,
        "https://www.bog.gov.gh/treasury-and-the-markets/historical-interbank-fx-rates/",
    ),
    "bog_econ_interest_monthly": (21, "https://www.bog.gov.gh/economic-data/interest-rates/"),
}

GOG_AUCTION_INDEX_URL = "https://www.bog.gov.gh/gog_auction_results/"
BOG_AUCTION_INDEX_URL = "https://www.bog.gov.gh/bog_auction_results/"
GFIM_DAILY_REPORTS_URL = "https://gfim.com.gh/daily-trading-reports/"
GFIM_MONTHLY_REPORTS_URL = "https://gfim.com.gh/monthly-reports/"


def fetch_source(  # noqa: PLR0911, PLR0913 - one dispatch arm/knob per protocol
    source_key: str,
    session: DeskSession,
    *,
    period: date | None = None,
    fx_pair: str | None = None,
    year: str | None = None,
    limit: int = 1,
) -> list[RawFetch]:
    """Run the proven capture mechanics for one source.

    ``period`` selects the edition for monthly publications (APR, SEFD);
    ``fx_pair`` narrows table 40 with the working per-column search;
    ``year``/``limit`` steer the GFIM FileBird folder and file count.
    """
    if source_key in _WDT_SOURCES:
        table_id, page_url = _WDT_SOURCES[source_key]
        column_search: dict[int, str] | None = None
        if source_key == "bog_fx_historical" and fx_pair is not None:
            column_search = {2: fx_pair}  # Currency Pair column
        return fetch_wdt_table(
            session, table_id=table_id, page_url=page_url, column_search=column_search
        )
    if source_key == "bog_gog_auction_pdf":
        return fetch_auction_results(session, index_url=GOG_AUCTION_INDEX_URL, limit=limit)
    if source_key == "bog_bog_auction_pdf":
        return fetch_auction_results(session, index_url=BOG_AUCTION_INDEX_URL, limit=limit)
    if source_key == "bog_apr_pdf":
        if period is None:
            raise FetchError("bog_apr_pdf needs period= (the notice month)")
        return fetch_publication_pdf(session, page_url=apr_notice_url(period))
    if source_key == "bog_sefd_pdf":
        if period is None:
            raise FetchError("bog_sefd_pdf needs period= (the edition month)")
        return fetch_publication_pdf(session, page_url=sefd_page_url(period))
    if source_key == "gfim_daily_xlsx":
        return fetch_filebird_files(
            session, page_url=GFIM_DAILY_REPORTS_URL, year=year, limit=limit
        )
    if source_key == "gfim_monthly_status":
        return fetch_filebird_files(
            session, page_url=GFIM_MONTHLY_REPORTS_URL, year=year, limit=limit
        )
    if source_key == "gss_interest_px":
        return fetch_pxweb_table(session)
    raise FetchError(f"no fetch mechanics registered for source_key={source_key!r}")
