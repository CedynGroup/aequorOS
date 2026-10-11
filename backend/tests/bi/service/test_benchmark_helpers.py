"""The BI benchmark's pure helpers (``scripts/bi_benchmark.py``).

Only the decisions a wrong answer would make the benchmark LIE about are pinned
here: which percentile is reported, whether a measurement passes its target,
whether a columnar-fallback threshold was crossed, whether the run would refuse
a database it must never touch, and the two shape translations the ingestion
path depends on. The measuring itself needs Postgres and a book, and is not
mockable into this suite.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scripts.bi_benchmark import (
    CLASS_AGGREGATE,
    CLASS_EXPLORE,
    CLASS_FACT,
    COLUMNAR_BUILD_SECONDS,
    COLUMNAR_EXPLORE_P95_MS,
    COLUMNAR_FACT_ROWS,
    TARGET_AGGREGATE_P95_MS,
    TARGET_FACT_P95_HIGH_MS,
    _reporting_balance_columns,
    chunked,
    columnar_thresholds_crossed,
    dotenv_urls,
    keeps_reference,
    partitions_touched,
    percentile,
    remap_dates,
    require_disposable_target,
    target_text,
    verdict,
)


class TestPercentile:
    """Nearest-rank, so every reported number was actually observed."""

    def test_p95_is_a_measurement_not_an_interpolation(self) -> None:
        sample = [10.0, 11.0, 12.0, 13.0, 400.0]
        assert percentile(sample, 0.95) in sample
        assert percentile(sample, 0.95) == 400.0

    def test_p50_of_an_even_sample_is_the_upper_middle_measurement(self) -> None:
        assert percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 2.0

    def test_order_of_the_input_does_not_matter(self) -> None:
        assert percentile([400.0, 10.0, 12.0], 0.95) == percentile([10.0, 12.0, 400.0], 0.95)

    def test_a_single_measurement_is_its_own_p95(self) -> None:
        assert percentile([7.5], 0.95) == 7.5

    def test_an_empty_sample_is_refused_rather_than_reported_as_zero(self) -> None:
        with pytest.raises(ValueError, match="empty sample"):
            percentile([], 0.95)

    @pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
    def test_a_fraction_outside_the_unit_interval_is_refused(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="fraction"):
            percentile([1.0], fraction)


class TestVerdict:
    """The spec's aggregate target is a CEILING; its fact target is a BAND."""

    def test_an_aggregate_query_under_the_ceiling_passes(self) -> None:
        assert verdict(TARGET_AGGREGATE_P95_MS - 1, query_class=CLASS_AGGREGATE) == "pass"

    def test_an_aggregate_query_at_the_ceiling_fails(self) -> None:
        assert verdict(TARGET_AGGREGATE_P95_MS, query_class=CLASS_AGGREGATE) == "FAIL"

    def test_a_fact_query_faster_than_the_band_is_not_a_failure(self) -> None:
        # The band says 1–4 s; 120 ms is better than asked for, not a miss.
        assert verdict(120, query_class=CLASS_FACT) == "pass"

    def test_a_fact_query_beyond_the_band_fails(self) -> None:
        assert verdict(TARGET_FACT_P95_HIGH_MS + 1, query_class=CLASS_FACT) == "FAIL"

    def test_an_explore_query_is_judged_against_the_columnar_threshold(self) -> None:
        assert verdict(COLUMNAR_EXPLORE_P95_MS, query_class=CLASS_EXPLORE) == "pass"
        assert verdict(COLUMNAR_EXPLORE_P95_MS + 1, query_class=CLASS_EXPLORE) == "FAIL"

    def test_an_unknown_class_claims_no_target(self) -> None:
        assert verdict(9_999, query_class="something_else") == "no target"

    @pytest.mark.parametrize(
        "query_class", [CLASS_AGGREGATE, CLASS_FACT, CLASS_EXPLORE, "something_else"]
    )
    def test_every_class_prints_a_target_beside_its_measurement(self, query_class: str) -> None:
        assert target_text(query_class)


class TestColumnarThresholds:
    """The fallback is evidence-gated: no crossing, no sentence."""

    def test_nothing_crossed_returns_an_empty_list(self) -> None:
        assert (
            columnar_thresholds_crossed(
                explore_p95_ms=COLUMNAR_EXPLORE_P95_MS - 1,
                fact_rows=COLUMNAR_FACT_ROWS - 1,
                longest_daily_build_s=COLUMNAR_BUILD_SECONDS - 1,
            )
            == []
        )

    def test_a_threshold_exactly_at_its_bound_is_not_crossed(self) -> None:
        assert (
            columnar_thresholds_crossed(
                explore_p95_ms=COLUMNAR_EXPLORE_P95_MS,
                fact_rows=COLUMNAR_FACT_ROWS,
                longest_daily_build_s=COLUMNAR_BUILD_SECONDS,
            )
            == []
        )

    def test_each_crossing_is_named_once_with_its_number(self) -> None:
        crossed = columnar_thresholds_crossed(
            explore_p95_ms=COLUMNAR_EXPLORE_P95_MS + 1,
            fact_rows=COLUMNAR_FACT_ROWS + 1,
            longest_daily_build_s=COLUMNAR_BUILD_SECONDS + 1,
        )
        assert len(crossed) == 3
        assert any("Explore p95" in sentence for sentence in crossed)
        assert any("fact rows" in sentence for sentence in crossed)
        assert any("daily build" in sentence for sentence in crossed)

    def test_an_unmeasured_explore_class_crosses_nothing(self) -> None:
        assert (
            columnar_thresholds_crossed(explore_p95_ms=None, fact_rows=0, longest_daily_build_s=0.0)
            == []
        )


class TestChunked:
    def test_pages_are_capped_and_lose_no_row(self) -> None:
        rows = list(range(10))
        pages = list(chunked(rows, 4))
        assert [len(page) for page in pages] == [4, 4, 2]
        assert [row for page in pages for row in page] == rows

    def test_an_empty_sequence_yields_no_page(self) -> None:
        assert list(chunked([], 4)) == []

    def test_a_page_size_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one row"):
            list(chunked([1], 0))


class TestRemapDates:
    """The book is translated in time, never reordered or re-spaced."""

    def test_the_last_source_date_lands_on_the_requested_last_date(self) -> None:
        source = [date(2016, 7, 31), date(2016, 8, 31), date(2016, 9, 30)]
        mapping = remap_dates(source, date(2026, 8, 31))
        assert mapping[date(2016, 9, 30)] == date(2026, 8, 31)

    def test_every_mapped_date_is_a_month_end_and_the_order_is_preserved(self) -> None:
        source = [date(2016, 7, 31), date(2016, 8, 31), date(2016, 9, 30)]
        mapping = remap_dates(source, date(2026, 8, 31))
        mapped = [mapping[day] for day in source]
        assert mapped == [date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31)]
        assert mapped == sorted(mapped)

    def test_an_unsorted_source_is_sorted_before_mapping(self) -> None:
        source = [date(2016, 9, 30), date(2016, 7, 31), date(2016, 8, 31)]
        mapping = remap_dates(source, date(2026, 8, 31))
        assert mapping[date(2016, 7, 31)] == date(2026, 6, 30)

    def test_one_date_maps_to_the_last_date(self) -> None:
        assert remap_dates([date(2016, 7, 31)], date(2026, 8, 31)) == {
            date(2016, 7, 31): date(2026, 8, 31)
        }


class TestReportingBalanceColumns:
    """The unit-suffixed column names come from the simulator, never from here."""

    def test_the_reporting_columns_are_read_off_the_attribute_list(self) -> None:
        attribute_columns = ("balance_xyz", "notional_xyz", "branch_id")
        columns = {"balance_ccy", "balance_xyz", "notional_ccy", "notional_xyz", "branch_id"}
        assert _reporting_balance_columns(columns, attribute_columns) == (
            "balance_xyz",
            "notional_xyz",
        )

    def test_a_panel_without_them_yields_none_rather_than_a_guess(self) -> None:
        assert _reporting_balance_columns({"balance_ccy"}, ("branch_id",)) == (None, None)


class TestDatabaseRefusal:
    """A benchmark that can reach the primary is a benchmark that will."""

    def test_an_empty_url_is_refused(self) -> None:
        with pytest.raises(SystemExit, match="--database-url is required"):
            require_disposable_target("")

    def test_a_url_named_by_the_environment_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LIVE_DATA_DATABASE_URL", "postgresql+psycopg://x@primary/aequoros")
        with pytest.raises(SystemExit, match="LIVE_DATA_DATABASE_URL"):
            require_disposable_target("postgresql+psycopg://x@primary/aequoros")

    def test_a_trailing_slash_does_not_defeat_the_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REAL_DATA_DATABASE_URL", "postgresql+psycopg://x@primary/aequoros")
        with pytest.raises(SystemExit, match="REAL_DATA_DATABASE_URL"):
            require_disposable_target("  postgresql+psycopg://x@primary/aequoros/ ")

    def test_a_disposable_url_is_admitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for variable in (
            "DATABASE_URL",
            "WORKER_DATABASE_URL",
            "REAL_DATA_DATABASE_URL",
            "LIVE_DATA_DATABASE_URL",
            "BI_DATABASE_URL",
            "_BI_BENCHMARK_INHERITED_DATABASE_URL",
        ):
            monkeypatch.setenv(variable, "")
        require_disposable_target("postgresql+psycopg://bi_owner:bi@127.0.0.1:5498/bi_bench")


class TestDotenvUrls:
    """``DATABASE_URL`` is blanked at import, so the refusal reads the file."""

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert dotenv_urls(tmp_path / "absent.env") == {}

    def test_quoted_exported_and_commented_lines_are_handled(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "# a comment",
                    'export DATABASE_URL="postgresql+psycopg://u:p@host:5433/aequoros_db"',
                    "WORKER_DATABASE_URL='postgresql+psycopg://w@host:5433/aequoros_db'",
                    "UNRELATED=1",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        found = dotenv_urls(env_file)
        assert found["DATABASE_URL"] == "postgresql+psycopg://u:p@host:5433/aequoros_db"
        assert found["WORKER_DATABASE_URL"] == "postgresql+psycopg://w@host:5433/aequoros_db"
        assert "UNRELATED" not in found


class TestRosterSample:
    """The sample must be stable, or a position's history breaks across dates."""

    def test_a_full_sample_keeps_everything(self) -> None:
        assert all(keeps_reference(f"LN-{n}", 1.0) for n in range(200))

    def test_the_same_reference_is_kept_or_dropped_at_every_date(self) -> None:
        references = [f"LN-{n}" for n in range(500)]
        first = [keeps_reference(r, 0.25) for r in references]
        second = [keeps_reference(r, 0.25) for r in references]
        assert first == second

    def test_the_kept_share_is_close_to_the_requested_fraction(self) -> None:
        references = [f"AA.ARRANGEMENT/{n}" for n in range(20_000)]
        kept = sum(keeps_reference(r, 0.25) for r in references)
        assert 0.23 < kept / len(references) < 0.27

    def test_a_smaller_fraction_keeps_a_subset_of_a_larger_one(self) -> None:
        references = [f"LN-{n}" for n in range(2_000)]
        small = {r for r in references if keeps_reference(r, 0.1)}
        large = {r for r in references if keeps_reference(r, 0.5)}
        assert small <= large


class TestPartitionsTouched:
    """A query that asks for one date and scans every month is the finding."""

    def test_monthly_and_yearly_children_are_both_counted_once(self) -> None:
        plan = "\n".join(
            [
                "->  Seq Scan on bi_fact_position_daily_y2026m08 f_1",
                "->  Parallel Seq Scan on bi_fact_position_daily_y2026m07 f_2",
                "->  Index Scan using ix_x on bi_fact_position_eom_y2026 e_1",
                "->  Seq Scan on bi_fact_position_daily_y2026m08 f_1",
            ]
        )
        assert partitions_touched(plan) == 3

    def test_the_default_partition_and_the_parent_are_not_counted(self) -> None:
        plan = "\n".join(
            [
                "->  Seq Scan on bi_fact_position_daily bi_fact_position_daily",
                "->  Index Scan using pk on bi_fact_position_daily_default d",
            ]
        )
        assert partitions_touched(plan) == 0

    def test_a_plan_with_no_partition_scan_counts_zero(self) -> None:
        assert partitions_touched("Result  (cost=0..0 rows=1 width=8)") == 0
