from __future__ import annotations

from app.adapters.excel_csv.sheet_analyzer import analyze_sheet, fill_merged_cells


class TestFillMergedCells:
    def test_propagates_top_left_value(self) -> None:
        grid = [["Title", None, None], [None, None, None]]
        filled = fill_merged_cells(grid, [(1, 1, 1, 3)])
        assert filled[0] == ["Title", "Title", "Title"]
        assert filled[1] == [None, None, None]

    def test_no_ranges_returns_grid_unchanged(self) -> None:
        grid = [["A", "B"]]
        assert fill_merged_cells(grid, []) is grid


class TestAnalyzeSheet:
    def test_skips_title_rows_above_the_header(self) -> None:
        grid = [
            ["Sample Bank Limited — Loan Book", None, None],
            [None, None, None],
            ["AccountRef", "Ccy", "Outstanding"],
            ["LN-0001", "GHS", 1500000.5],
        ]
        tables = analyze_sheet("Loans", grid)
        assert len(tables) == 1
        table = tables[0]
        assert table.columns == ("AccountRef", "Ccy", "Outstanding")
        assert table.header_row == 3
        assert table.rows == (
            (4, {"AccountRef": "LN-0001", "Ccy": "GHS", "Outstanding": 1500000.5}),
        )

    def test_splits_stacked_tables_and_numbers_them(self) -> None:
        grid = [
            ["ProductCode", "ProductName"],
            ["LN.CORP.5Y", "5y corporate loan"],
            [None, None],
            ["AccountRef", "Outstanding"],
            ["LN-0009", 90000],
        ]
        tables = analyze_sheet("Data", grid)
        assert [table.name for table in tables] == ["Data#1", "Data#2"]
        assert tables[1].rows[0][0] == 5  # true worksheet row number survives

    def test_placeholder_rows_are_dropped_without_splitting_the_table(self) -> None:
        grid = [
            ["AccountRef", "Outstanding"],
            ["LN-0001", 100],
            ["-", "N/A"],
            ["LN-0002", 200],
        ]
        (table,) = analyze_sheet("Loans", grid)
        assert [number for number, _ in table.rows] == [2, 4]

    def test_headerless_block_after_a_true_blank_is_not_invented_into_a_table(self) -> None:
        grid = [
            ["AccountRef", "Outstanding"],
            ["LN-0001", 100],
            [None, None],
            ["LN-0002", 200],
        ]
        (table,) = analyze_sheet("Loans", grid)
        assert [number for number, _ in table.rows] == [2]

    def test_duplicate_headers_are_deduped(self) -> None:
        grid = [
            ["Amount", "Amount", "Ccy"],
            [100, 200, "GHS"],
        ]
        (table,) = analyze_sheet("S", grid)
        assert table.columns == ("Amount", "Amount (2)", "Ccy")
        assert table.rows[0][1] == {"Amount": 100, "Amount (2)": 200, "Ccy": "GHS"}

    def test_sheet_without_a_header_yields_nothing(self) -> None:
        grid = [[1, 2, 3], [4, 5, 6]]
        assert analyze_sheet("Numbers", grid) == []
