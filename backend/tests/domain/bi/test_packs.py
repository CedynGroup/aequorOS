"""The certified dashboard packs (spec §Phase 2, Content packs).

A pack is DATA that a board reads. It may name catalogue member ids, a closed
window vocabulary and a closed panel key — never a table, a column, a route, a
parameter, a date or a number. Half of that is structural (``BiPackSpec`` is
closed and every source is a ``Literal``); the other half is here:

* the glob is not vacuous — a test that iterates a directory and finds nothing
  is green and proves nothing, so the pack ids are asserted, not observed;
* every member a pack names resolves through ``catalogue().member()``, which
  is what stops a pack surviving a measure rename;
* every widget query passes the compiler's SHAPE rules, with no database;
* every widget either reads (a query or a panel) or names what it is waiting
  for. Nothing renders a zero it cannot stand behind;
* no widget quietly widens its authorization surface: ``authorize_query``
  denies the WHOLE query if any (module, sensitivity) pair fails, so a widget
  spanning two modules is all-or-nothing for a partially entitled reader and
  is listed here with its reason;
* jurisdiction neutrality, on the FILE and on the display text. The repository
  guard at ``tests/services/test_jurisdiction_neutrality.py`` scans pack JSON
  for currency codes, but its regulator alternation lives in a ``.py``-only
  guard — so a pack naming a national regulator passes it today. That
  alternation is asserted here as well.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.domain.bi.catalogue import Catalogue, catalogue
from app.domain.bi.packs import PACK_DIR, PackError, _load, pack, pack_ids, packs
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from app.domain.ingestion.contracts import CanonicalRecords
from app.schemas.bi import (
    BI_PACK_GRID_COLUMNS,
    BI_PACK_MAX_WIDGETS,
    BiPackQuery,
    BiPackSpec,
    BiPackWidget,
    BiPendingCapability,
    BiQuery,
)
from app.services.bi.compiler import _resolve

#: The seven packs the spec's coverage table names, and the audience each is
#: written for. Asserted rather than read off the directory.
EXPECTED_PACKS: dict[str, str] = {
    "alco": "alco",
    "board": "board",
    "branch_network": "branch_network",
    "compliance": "compliance",
    "credit": "credit",
    "cro": "cro",
    "finance": "finance",
}

#: How many widgets each pack ships, and how they are made up:
#: (query widgets, embedded panels, named gaps). The spec's coverage table has
#: 47 cells; three of them (stage migration beside the stage mix, vintage
#: curves beside the vintage balances, attestation beside the filing calendar)
#: are served by a SECOND widget embedding a platform panel, so 50 widgets
#: carried 47 cells. Restating the shape here means a widget cannot be dropped,
#: or silently demoted to a gap, without this failing.
#: 2026-09-29: the Compliance pack's "Reconciliation exceptions" panel left with
#: the rest of BI reconciliation (BI grades nothing against the returns the
#: platform files), so 49 widgets now carry 46 cells; ``compliance`` is (1, 2, 1).
#: Phase 5 moved three counts: ``alco`` and ``branch_network`` each gained one
#: query widget over the new optional position fields, ``credit`` gained one AND
#: promoted ``officer_league_table`` from a gap to a figure, and ``finance``
#: promoted ``profit_and_loss_by_branch`` — both promotions because the mart
#: column each was waiting on now exists.
EXPECTED_SHAPE: dict[str, tuple[int, int, int]] = {
    "alco": (9, 0, 2),
    "board": (5, 1, 3),
    "branch_network": (3, 0, 2),
    "compliance": (1, 2, 1),
    "credit": (7, 2, 1),
    "cro": (7, 1, 1),
    "finance": (2, 0, 2),
}

#: Widgets whose query spans more than one authorization module, and why.
#: ``authorize_query`` evaluates every (module, sensitivity) pair across a
#: query's measures, dimensions AND filters and denies the whole query if one
#: fails, so each of these is all-or-nothing for a reader who holds only part
#: of the sentence. That is a decision, so it is written down.
MIXED_MODULE_WIDGETS: dict[str, str] = {
    # Every time dimension belongs to Risk & Limits, so ANY trend of a
    # non-risk measure spans two modules. There is no time dimension under
    # another module to choose instead.
    "alco.cost_of_funds": "a liquidity measure over a time dimension",
    "alco.margin_decomposition": "funds transfer pricing measures over a time dimension",
    "board.net_interest_margin": "a funds transfer pricing measure over a time dimension",
    "cro.provision_coverage": "a credit measure over a time dimension",
    "credit.disbursements": "a credit flow over a time dimension",
    "credit.recoveries_and_write_offs": "credit flows over a time dimension",
    # The conformed dimensions (branch, product) belong to Risk & Limits
    # because every fact shares them; slicing a credit figure by one is the
    # whole point of the widget.
    "cro.npl_by_product": "a credit measure sliced by the shared product dimension",
    "cro.npl_by_branch": "a credit measure sliced by the shared branch dimension",
    "credit.portfolio_by_product": "credit measures sliced by the shared product dimension",
    "branch_network.branch_league_table": "credit measures sliced by the shared branch dimension",
    "branch_network.book_by_region": "a credit measure sliced by the shared branch region",
    # The balance-sheet ladders read the whole book (Risk & Limits) and bucket
    # it by an attribute the liquidity and interest-rate modules own.
    "alco.maturity_ladder": "the whole book bucketed by contractual maturity",
    "alco.repricing_ladder": "the whole book bucketed by repricing date",
    # Phase 5. The three optional position attributes are conformed attributes of
    # an ACCOUNT and belong to Risk & Limits, so slicing or selecting a credit or
    # liquidity figure by one spans two modules — which is the whole point of each
    # widget, exactly as the branch and product dimensions above.
    "alco.dormant_deposits": "a liquidity measure selected on the shared account status",
    "branch_network.arrears_by_branch": ("a credit measure sliced by the shared branch dimension"),
    "credit.book_by_channel": "a credit measure sliced by the shared channel dimension",
    "credit.officer_league_table": "credit measures sliced by the shared officer dimension",
}

#: Widgets that name a ``restricted`` member. Most readers hold no sentence
#: for these and see the restricted placeholder instead of the figure, so a
#: pack acquiring one is a deliberate act.
#: ``credit.officer_league_table`` joined in Phase 5 and is the deliberate act
#: this registry exists to record: ``position.officer_code`` names one member of
#: staff, so the league table is officer-level performance information and a
#: reader needs an explicit restricted-sensitivity sentence for it. Without one the
#: widget renders the restricted placeholder and the rest of the credit dashboard
#: is unaffected.
RESTRICTED_WIDGETS: frozenset[str] = frozenset(
    {"cro.large_exposures", "credit.officer_league_table"}
)

#: Standard names that legitimately carry a digit. Everything else numeric in a
#: title or caption would be a figure the pack asserted rather than read.
_STANDARD_NAMES = re.compile(r"Tier [12]|CET1|IFRS 9|Stage [123]|Basel I{1,3}")

#: Currency codes, currency names and currency symbols. The reporting unit is
#: the bank's own and is resolved from the institution, never written down.
_CURRENCY_WORDS = re.compile(
    r"\b(GHS|NGN|KES|ZAR|EUR|GBP|cedis?|naira|shillings?|rand|dollars?|euros?|"
    r"pounds?|sterling)\b",
    re.IGNORECASE,
)
_CURRENCY_SYMBOLS = re.compile(r"[¢₵$€£¥]")

#: A national regulator, supervisor or return family named in a pack. The
#: return family already lives in the filing workspace and the regime already
#: lives in the measure id, so display text never needs one.
_REGULATOR_WORDS = re.compile(
    r"\b(BoG|Bank of Ghana|Ghana|Ghanaian|Nigeria\w*|Kenya\w*|BSD ?\d+|"
    r"Section ?29|CRD ?IV)\b",
    re.IGNORECASE,
)

#: Dataset keys ``needs_data`` may name: a reference dataset kind, or one of
#: the canonical entity collections the Data Engine translates a push into.
_CANONICAL_DATASETS: frozenset[str] = frozenset(CanonicalRecords.model_fields) - {
    "reference_rows",
    "failures",
}
KNOWN_DATASETS: frozenset[str] = frozenset(REFERENCE_DATASET_KINDS) | _CANONICAL_DATASETS

#: A date with no significance whatever, used only to give the compiler a
#: window to check the shape of. No pack carries a date.
SHAPE_CHECK_AS_OF = date(2026, 6, 30)


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


def _widget_keys() -> list[tuple[str, str]]:
    return [(spec.id, widget.id) for spec in packs() for widget in spec.widgets]


def _widget(pack_id: str, widget_id: str) -> BiPackWidget:
    return pack(pack_id).widget(widget_id)


def _display_text(spec: BiPackSpec) -> list[tuple[str, str]]:
    text = [(f"{spec.id}.title", spec.title), (f"{spec.id}.description", spec.description)]
    for widget in spec.widgets:
        text.append((f"{spec.id}.{widget.id}.title", widget.title))
        text.append((f"{spec.id}.{widget.id}.caption", widget.caption))
    return text


def _named_members(query: BiPackQuery) -> list[str]:
    named = [*query.measures, *query.dimensions]
    named.extend(predicate.member for predicate in query.filters)
    named.extend(sort.member for sort in query.sort)
    if query.top_n is not None:
        named.append(query.top_n.dimension)
    if query.pivot is not None:
        named.append(query.pivot.dimension)
    return named


# --- the glob is not vacuous ------------------------------------------------------------


def test_the_pack_directory_holds_exactly_the_certified_packs() -> None:
    """Asserted, never observed: a directory walk that finds nothing passes."""
    on_disk = sorted(path.stem for path in PACK_DIR.glob("*.json"))
    assert on_disk == sorted(EXPECTED_PACKS), (
        "the pack files on disk are not the certified set; adding or removing a pack "
        "is a decision that belongs in EXPECTED_PACKS"
    )
    assert sorted(pack_ids()) == sorted(EXPECTED_PACKS)
    assert len(packs()) == len(EXPECTED_PACKS)


def test_every_pack_file_parses_and_is_named_for_its_own_id() -> None:
    for path in sorted(PACK_DIR.glob("*.json")):
        spec = _load(path)
        assert spec.id == path.stem
        assert spec == pack(spec.id)


def test_each_audience_is_written_for_exactly_once() -> None:
    audiences = [spec.audience for spec in packs()]
    assert sorted(audiences) == sorted(EXPECTED_PACKS.values())
    assert len(set(audiences)) == len(audiences)
    for spec in packs():
        assert spec.audience == EXPECTED_PACKS[spec.id]


def test_each_pack_ships_the_widgets_it_is_expected_to() -> None:
    shape = {
        spec.id: (
            sum(1 for w in spec.widgets if w.query is not None),
            sum(1 for w in spec.widgets if w.panel is not None),
            sum(1 for w in spec.widgets if w.is_gap),
        )
        for spec in packs()
    }
    assert shape == EXPECTED_SHAPE


# --- every member resolves --------------------------------------------------------------


@pytest.mark.parametrize(("pack_id", "widget_id"), _widget_keys())
def test_every_member_a_widget_names_is_in_the_catalogue(
    pack_id: str, widget_id: str, cat: Catalogue
) -> None:
    widget = _widget(pack_id, widget_id)
    if widget.query is None:
        return
    for member_id in _named_members(widget.query):
        cat.member(member_id)
    series = widget.display.series_dimension
    if series is not None:
        cat.dimension(series)


@pytest.mark.parametrize(("pack_id", "widget_id"), _widget_keys())
def test_every_widget_query_passes_the_compiler_shape_rules(
    pack_id: str, widget_id: str, cat: Catalogue
) -> None:
    """One fact table, one time behaviour, allowed dimensions, sorts in range.

    ``_resolve`` is the whole of the compiler's shape stage and needs no
    session; ``compile_query`` would only add a dialect and the pivot probe.
    """
    widget = _widget(pack_id, widget_id)
    if widget.query is None:
        return
    resolved = _resolve(cat, widget.query.for_period(SHAPE_CHECK_AS_OF), ())
    assert resolved.measures
    assert resolved.fact


# --- what a widget is waiting on --------------------------------------------------------


@pytest.mark.parametrize(("pack_id", "widget_id"), _widget_keys())
def test_every_widget_either_reads_or_names_what_it_waits_for(pack_id: str, widget_id: str) -> None:
    widget = _widget(pack_id, widget_id)
    sources = [widget.query is not None, widget.panel is not None]
    assert sources.count(True) <= 1
    if widget.is_gap:
        assert widget.needs_data is not None or widget.pending_capability is not None


@pytest.mark.parametrize(("pack_id", "widget_id"), _widget_keys())
def test_needs_data_names_a_dataset_the_data_engine_accepts(pack_id: str, widget_id: str) -> None:
    widget = _widget(pack_id, widget_id)
    if widget.needs_data is None:
        return
    assert widget.needs_data in KNOWN_DATASETS, (
        f"{pack_id}.{widget_id} waits on {widget.needs_data!r}, which is neither a "
        "reference dataset kind nor a canonical entity collection, so no Data Engine "
        "template could be offered for it"
    )


def test_at_least_one_widget_of_each_gap_kind_ships() -> None:
    """The gap surface is real, not a field nobody uses."""
    widgets = [widget for spec in packs() for widget in spec.widgets]
    assert any(w.is_gap and w.needs_data is not None for w in widgets)
    assert any(w.is_gap and w.pending_capability is not None for w in widgets)
    assert any(w.query is not None and w.needs_data is not None for w in widgets), (
        "no widget that DOES read names a dataset it degrades without; portfolio at "
        "risk is the worked example (a missing days-past-due column must read as no "
        "value, never as a clean book)"
    )
    # ``mart_field`` has NO occupant since Phase 5, and that is this change
    # landing rather than the vocabulary rotting: both widgets that carried it
    # were waiting on a mart column, and both columns now exist
    # (``bi_fact_position_daily.officer_id`` and the whole
    # ``bi_fact_gl_branch_monthly``), so both read a query instead. Leaving the
    # copy in place would have told a bank the platform cannot do something it can.
    assert {w.pending_capability for w in widgets if w.pending_capability} == {
        "catalogue_member",
        "governed_limit",
    }
    # The value stays in the vocabulary because the next such gap needs it, and it
    # is asserted present so it cannot be dropped without a decision — so this test
    # pins BOTH the occupancy and the vocabulary, where before it pinned one.
    assert set(get_args(BiPendingCapability)) == {
        "catalogue_member",
        "governed_limit",
        "mart_field",
    }


# --- authorization surface --------------------------------------------------------------


@pytest.mark.parametrize(("pack_id", "widget_id"), _widget_keys())
def test_no_widget_mixes_authorization_modules_by_accident(
    pack_id: str, widget_id: str, cat: Catalogue
) -> None:
    widget = _widget(pack_id, widget_id)
    if widget.query is None:
        return
    resolved = _resolve(cat, widget.query.for_period(SHAPE_CHECK_AS_OF), ())
    modules = {cat.member(member_id).module for member_id in resolved.member_ids}
    key = f"{pack_id}.{widget_id}"
    if len(modules) > 1:
        assert key in MIXED_MODULE_WIDGETS, (
            f"{key} spans {sorted(modules)}. authorize_query denies the WHOLE query when "
            "any pair fails, so a partially entitled reader sees nothing at all. If that "
            "is intended, say so in MIXED_MODULE_WIDGETS."
        )


def test_the_mixed_module_list_holds_only_widgets_that_still_mix(cat: Catalogue) -> None:
    """An allow-list that outlives its entries is how a guard goes blind."""
    still_mixing: set[str] = set()
    for spec in packs():
        for widget in spec.widgets:
            if widget.query is None:
                continue
            resolved = _resolve(cat, widget.query.for_period(SHAPE_CHECK_AS_OF), ())
            modules = {cat.member(member_id).module for member_id in resolved.member_ids}
            if len(modules) > 1:
                still_mixing.add(f"{spec.id}.{widget.id}")
    assert set(MIXED_MODULE_WIDGETS) == still_mixing


def test_restricted_members_appear_only_where_intended(cat: Catalogue) -> None:
    restricted: set[str] = set()
    for spec in packs():
        for widget in spec.widgets:
            if widget.query is None:
                continue
            resolved = _resolve(cat, widget.query.for_period(SHAPE_CHECK_AS_OF), ())
            if any(
                cat.member(member_id).sensitivity == "restricted"
                for member_id in resolved.member_ids
            ):
                restricted.add(f"{spec.id}.{widget.id}")
    assert restricted == RESTRICTED_WIDGETS


# --- neutrality and production copy -----------------------------------------------------


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_PACKS))
def test_no_pack_file_names_a_currency_or_a_regulator(pack_id: str) -> None:
    """Whole FILE, not just the display strings: every line of JSON is quoted.

    The repository-wide guard already scans these files for currency codes.
    The regulator alternation it uses lives in a ``.py``-only guard, so it is
    restated here — a pack naming a national supervisor would pass there.
    """
    source = (PACK_DIR / f"{pack_id}.json").read_text(encoding="utf-8")
    assert not _CURRENCY_WORDS.search(source), f"{pack_id}.json names a currency"
    assert not _CURRENCY_SYMBOLS.search(source), f"{pack_id}.json carries a currency symbol"
    assert not _REGULATOR_WORDS.search(source), f"{pack_id}.json names a regulator"


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_PACKS))
def test_display_text_asserts_no_figure_of_its_own(pack_id: str) -> None:
    """A pack narrates; it never states a number.

    A threshold, a target, an axis maximum or a percentage written into a
    caption is a figure nobody governs, sitting beside figures that are. Only
    the digits inside standard names survive.
    """
    for where, text in _display_text(pack(pack_id)):
        stripped = _STANDARD_NAMES.sub("", text)
        assert not re.search(r"\d", stripped), f"{where} states a number: {text!r}"
        assert "%" not in text, f"{where} states a percentage: {text!r}"


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_PACKS))
def test_display_text_is_production_copy(pack_id: str) -> None:
    """No raw enums, no wire keys, no placeholders in anything a bank reads."""
    spec = pack(pack_id)
    for where, text in _display_text(spec):
        assert text.strip() == text, f"{where} is padded"
        if where.endswith(".caption"):
            continue
        assert text, f"{where} is empty"
    for where, text in _display_text(spec):
        assert "_" not in text, f"{where} reads like a wire key: {text!r}"
        assert not re.search(r"\b(TODO|TBD|FIXME|lorem|placeholder)\b", text, re.IGNORECASE), (
            f"{where} carries placeholder copy: {text!r}"
        )
        assert not re.search(r"\b[A-Z][A-Z0-9]{2,}\b", _STANDARD_NAMES.sub("", text)), (
            f"{where} shows a raw code: {text!r}"
        )
    for widget in spec.widgets:
        assert widget.caption, f"{spec.id}.{widget.id} has no caption"


# --- layout -----------------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", sorted(EXPECTED_PACKS))
def test_the_layout_covers_the_canvas_without_overlapping(pack_id: str) -> None:
    spec = pack(pack_id)
    assert len(spec.widgets) <= BI_PACK_MAX_WIDGETS
    occupied: set[tuple[int, int]] = set()
    for item in spec.layout:
        assert item.x + item.w <= BI_PACK_GRID_COLUMNS
        cells = {
            (column, row)
            for column in range(item.x, item.x + item.w)
            for row in range(item.y, item.y + item.h)
        }
        overlap = cells & occupied
        assert not overlap, f"{pack_id}.{item.i} overlaps another widget at {sorted(overlap)}"
        occupied |= cells


# --- the window vocabulary --------------------------------------------------------------


def test_the_pack_query_restates_every_field_of_a_bi_query() -> None:
    """Drift guard: a new ``BiQuery`` field must land on both or on neither."""
    pack_fields = set(BiPackQuery.model_fields) - {"window", "compare"}
    query_fields = set(BiQuery.model_fields) - {"time"}
    assert pack_fields == query_fields, (
        "BiPackQuery and BiQuery have drifted; a field on one and not the other means a "
        "pack cannot express something the API can, or vice versa"
    )


def test_no_pack_carries_a_date() -> None:
    """The reporting date is the reader's, never the file's."""
    for pack_id in EXPECTED_PACKS:
        source = (PACK_DIR / f"{pack_id}.json").read_text(encoding="utf-8")
        assert not re.search(r"\d{4}-\d{2}-\d{2}", source), f"{pack_id}.json pins a date"


@pytest.mark.parametrize(
    ("window", "compare", "expected_start", "expected_compare"),
    [
        ("as_of", "none", None, None),
        ("as_of", "prior_month", None, date(2026, 5, 31)),
        ("as_of", "prior_quarter", None, date(2026, 3, 31)),
        ("as_of", "prior_year", None, date(2025, 6, 30)),
        ("month_to_date", "prior_month", date(2026, 6, 1), date(2026, 5, 31)),
        ("quarter_to_date", "prior_quarter", date(2026, 4, 1), date(2026, 3, 31)),
        ("year_to_date", "prior_year", date(2026, 1, 1), date(2025, 6, 30)),
        ("trailing_3_months", "prior_quarter", date(2026, 4, 1), date(2026, 3, 31)),
        ("trailing_12_months", "prior_year", date(2025, 7, 1), date(2025, 6, 30)),
        ("trailing_24_months", "none", date(2024, 7, 1), None),
    ],
)
def test_a_relative_window_binds_to_the_readers_reporting_date(
    window: str,
    compare: str,
    expected_start: date | None,
    expected_compare: date | None,
) -> None:
    query = BiPackQuery.model_validate(
        {"measures": ["positions.balance_rc"], "window": window, "compare": compare}
    )
    bound = query.for_period(date(2026, 6, 30))
    assert bound.time.compare_to == expected_compare
    if expected_start is None:
        assert bound.time.as_of == date(2026, 6, 30)
        assert bound.time.range is None
    else:
        assert bound.time.as_of is None
        assert bound.time.range is not None
        assert bound.time.range.start == expected_start
        assert bound.time.range.end == date(2026, 6, 30)


def test_a_month_end_compares_against_a_month_end() -> None:
    """End-of-month convention, so a comparison lands on a date the book has.

    A reporting date is almost always a period end. Shifting 30 June back one
    month by its day number gives 30 May, which a month-end book has no
    snapshot at, and the prior column would read as no value for no reason.
    """
    query = BiPackQuery.model_validate(
        {"measures": ["positions.balance_rc"], "window": "as_of", "compare": "prior_month"}
    )
    assert query.for_period(date(2026, 6, 30)).time.compare_to == date(2026, 5, 31)
    assert query.for_period(date(2026, 3, 31)).time.compare_to == date(2026, 2, 28)
    assert query.for_period(date(2024, 3, 31)).time.compare_to == date(2024, 2, 29)
    # A mid-month date keeps its own day number, clamped to the shorter month.
    assert query.for_period(date(2026, 6, 15)).time.compare_to == date(2026, 5, 15)
    assert query.for_period(date(2026, 3, 30)).time.compare_to == date(2026, 2, 28)


def test_a_comparison_may_not_overlap_the_window_it_compares() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        BiPackQuery.model_validate(
            {
                "measures": ["positions.balance_rc"],
                "window": "trailing_12_months",
                "compare": "prior_quarter",
            }
        )


# --- the loader refuses what it should --------------------------------------------------


def test_a_malformed_pack_is_refused_at_load(tmp_path: Path) -> None:
    broken = tmp_path / "board.json"
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(PackError, match="not valid JSON"):
        _load(broken)


def test_a_pack_whose_id_disagrees_with_its_file_name_is_refused(tmp_path: Path) -> None:
    source: dict[str, Any] = json.loads((PACK_DIR / "board.json").read_text(encoding="utf-8"))
    misnamed = tmp_path / "alco.json"
    misnamed.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(PackError, match="named for its id"):
        _load(misnamed)


def test_a_widget_may_not_name_both_a_query_and_a_panel() -> None:
    with pytest.raises(ValidationError, match="may name one"):
        BiPackWidget.model_validate(
            {
                "id": "both",
                "kind": "kpi",
                "title": "Both doors",
                "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
                "panel": "return_calendar",
            }
        )


def test_a_widget_with_no_source_must_say_what_it_waits_for() -> None:
    with pytest.raises(ValidationError, match="waiting for"):
        BiPackWidget.model_validate({"id": "silent", "kind": "kpi", "title": "Silent"})


def test_a_series_must_be_drawn_over_a_dimension_the_widget_groups_by() -> None:
    with pytest.raises(ValidationError, match="does not group by"):
        BiPackWidget.model_validate(
            {
                "id": "series",
                "kind": "bar",
                "title": "Series",
                "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
                "display": {"series_dimension": "position.type"},
            }
        )


def test_a_widget_may_not_show_a_comparison_it_never_asked_for() -> None:
    with pytest.raises(ValidationError, match="no prior period"):
        BiPackWidget.model_validate(
            {
                "id": "compare",
                "kind": "kpi",
                "title": "Comparison",
                "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
                "display": {"show_comparison": True},
            }
        )


def test_a_layout_item_may_not_run_off_the_grid() -> None:
    with pytest.raises(ValidationError, match="runs past column"):
        BiPackSpec.model_validate(
            {
                "id": "overflow",
                "title": "Overflow",
                "description": "A pack whose widget runs off the canvas.",
                "audience": "board",
                "version": "1.0.0",
                "widgets": [
                    {
                        "id": "wide",
                        "kind": "kpi",
                        "title": "Wide",
                        "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
                    }
                ],
                "layout": [{"i": "wide", "x": 8, "y": 0, "w": 8, "h": 2}],
            }
        )


def test_every_widget_must_be_positioned_exactly_once() -> None:
    with pytest.raises(ValidationError, match="position every widget"):
        BiPackSpec.model_validate(
            {
                "id": "unpositioned",
                "title": "Unpositioned",
                "description": "A pack with a widget nobody placed.",
                "audience": "board",
                "version": "1.0.0",
                "widgets": [
                    {
                        "id": "first",
                        "kind": "kpi",
                        "title": "First",
                        "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
                    },
                    {
                        "id": "second",
                        "kind": "kpi",
                        "title": "Second",
                        "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
                    },
                ],
                "layout": [{"i": "first", "x": 0, "y": 0, "w": 3, "h": 2}],
            }
        )


def test_a_pack_cannot_carry_an_unknown_key() -> None:
    """``extra="forbid"`` is what stops a pack smuggling a table or a filter."""
    with pytest.raises(ValidationError):
        BiPackWidget.model_validate(
            {
                "id": "smuggler",
                "kind": "kpi",
                "title": "Smuggler",
                "table": "bi_fact_position_daily",
                "query": {"measures": ["positions.balance_rc"], "window": "as_of"},
            }
        )


# --- Phase 5: a figure that degrades to a named gap, never to a zero ---------------------


#: The widgets Phase 5 added or promoted, and the dataset each degrades to.
#: ``needs_data`` is carried BESIDE a query on purpose: the answer is still asked
#: for, because a bank that has supplied the field must see the figure, and the key
#: is what the empty state names for a bank that has not.
PHASE_5_WIDGETS: dict[str, str] = {
    "branch_network.arrears_by_branch": "positions",
    "credit.book_by_channel": "positions",
    "credit.officer_league_table": "positions",
    "alco.dormant_deposits": "positions",
    "finance.profit_and_loss_by_branch": "gl_segment_balances",
}


@pytest.mark.parametrize(("key", "dataset"), sorted(PHASE_5_WIDGETS.items()))
def test_a_phase_5_widget_reads_a_figure_and_still_names_the_dataset_it_needs(
    key: str, dataset: str
) -> None:
    pack_id, widget_id = key.split(".", 1)
    widget = _widget(pack_id, widget_id)
    assert widget.query is not None, f"{key} must ASK for the figure, not ship as a gap"
    assert widget.needs_data == dataset, (
        f"{key} must name the dataset its empty state asks for, so a bank with none "
        "of the field sees a named gap rather than a chart of zeros"
    )
    assert widget.pending_capability is None, (
        f"{key} is no longer waiting on platform work; saying it is would be a false "
        "statement about what the platform can do"
    )
    assert widget.is_gap is False
    # The caption carries the specificity the dataset key cannot: which FIELD of the
    # dataset is missing. A key must stay a dataset the Data Engine accepts (there is
    # no ``loan_arrears`` template to offer), so the sentence is where it is said.
    assert widget.caption


def test_the_phase_5_widgets_name_the_members_this_wave_added() -> None:
    """The promotions are only honest if they read the NEW members; a widget that
    kept its old query would have had its copy corrected and nothing else."""
    named = {
        f"{spec.id}.{widget.id}": set(_named_members(widget.query))
        for spec in packs()
        for widget in spec.widgets
        if widget.query is not None and f"{spec.id}.{widget.id}" in PHASE_5_WIDGETS
    }
    assert set(named) == set(PHASE_5_WIDGETS)
    assert "loans.arrears_amount_rc" in named["branch_network.arrears_by_branch"]
    assert "position.channel" in named["credit.book_by_channel"]
    assert "position.officer_code" in named["credit.officer_league_table"]
    assert "position.account_status" in named["alco.dormant_deposits"]
    assert "gl.branch_ytd_rc" in named["finance.profit_and_loss_by_branch"]
