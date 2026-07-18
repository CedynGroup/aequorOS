"""The safe parameterized query builder: quoting, binding, and injection guard."""

from __future__ import annotations

import pytest

from app.adapters.database_direct.config import JoinExtraction, TableExtraction
from app.adapters.database_direct.query_builder import (
    IdentifierError,
    ParamStyle,
    build_select,
    paramstyle_for,
    quote_qualified,
)


class TestQuoting:
    def test_sqlserver_uses_brackets(self) -> None:
        assert quote_qualified("DBO.POSITIONS", "sqlserver") == "[DBO].[POSITIONS]"

    def test_oracle_and_bridges_use_ansi_double_quotes(self) -> None:
        assert quote_qualified("COREBANK.POSITIONS", "oracle") == '"COREBANK"."POSITIONS"'
        assert quote_qualified("s.t", "jdbc") == '"s"."t"'
        assert quote_qualified("s.t", "odbc") == '"s"."t"'

    @pytest.mark.parametrize(
        "bad",
        [
            "POSITIONS; DROP TABLE X",
            'POS"ITIONS',
            "POS ITIONS",
            "POS--ITIONS",
            "1POSITIONS",
            "",
        ],
    )
    def test_unsafe_identifiers_are_rejected(self, bad: str) -> None:
        with pytest.raises(IdentifierError):
            quote_qualified(bad, "sqlserver")


class TestParamStyle:
    def test_backend_paramstyles(self) -> None:
        assert paramstyle_for("oracle") is ParamStyle.NAMED
        assert paramstyle_for("sqlserver") is ParamStyle.QMARK
        assert paramstyle_for("jdbc") is ParamStyle.QMARK
        assert paramstyle_for("odbc") is ParamStyle.QMARK


class TestBuildSelect:
    def test_plain_full_select(self) -> None:
        ext = TableExtraction(
            table="DBO.GL_ACCOUNTS",
            record_kind="gl_account",
            columns=("ACCT_CODE", "BAL"),
        )
        query = build_select(ext, "sqlserver")
        assert query.sql == "SELECT [ACCT_CODE], [BAL] FROM [DBO].[GL_ACCOUNTS]"
        assert query.parameters == []

    def test_values_are_bound_never_interpolated(self) -> None:
        ext = TableExtraction(
            table="DBO.POSITIONS",
            record_kind="position",
            incremental_column="UPDATED_AT",
            filters={"CCY": "GHS"},
        )
        query = build_select(ext, "sqlserver", incremental_since="2026-06-30T00:00:00")
        assert "'GHS'" not in query.sql  # no literal value ever reaches the SQL text
        assert query.sql == (
            "SELECT * FROM [DBO].[POSITIONS] "
            "WHERE [CCY] = ? AND [UPDATED_AT] > ? ORDER BY [UPDATED_AT]"
        )
        assert query.parameters == ["GHS", "2026-06-30T00:00:00"]

    def test_named_paramstyle_for_oracle(self) -> None:
        ext = TableExtraction(
            table="COREBANK.POSITIONS",
            record_kind="position",
            incremental_column="UPDATED_AT",
            filters={"CCY": "GHS"},
        )
        query = build_select(ext, "oracle", incremental_since="X")
        assert query.paramstyle is ParamStyle.NAMED
        assert query.parameters == {"p0": "GHS", "p1": "X"}
        assert ":p0" in query.sql and ":p1" in query.sql

    def test_incremental_predicate_omitted_without_cursor_or_since(self) -> None:
        ext = TableExtraction(table="DBO.T", record_kind="gl_account")
        assert "WHERE" not in build_select(ext, "sqlserver").sql
        # a cursor column but no 'since' still omits the predicate (full pull).
        ext2 = TableExtraction(table="DBO.T", record_kind="gl_account", incremental_column="TS")
        assert "WHERE" not in build_select(ext2, "sqlserver").sql

    def test_row_limit_dialects(self) -> None:
        ext = TableExtraction(table="DBO.T", record_kind="gl_account")
        assert build_select(ext, "sqlserver", row_limit=10).sql.startswith("SELECT TOP 10 *")
        assert build_select(ext, "oracle", row_limit=10).sql.endswith("FETCH FIRST 10 ROWS ONLY")

    def test_only_select_is_ever_emitted(self) -> None:
        # Exhaustive over the config surface: no code path yields a write verb.
        # Note the column name UPDATED_AT legitimately contains "UPDATE"; the
        # guarantee is a single read statement, checked at word/statement level.
        ext = TableExtraction(
            table="DBO.POSITIONS",
            record_kind="position",
            incremental_column="UPDATED_AT",
            filters={"CCY": "GHS", "BRANCH": "001"},
        )
        for backend in ("oracle", "sqlserver", "jdbc", "odbc"):
            sql = build_select(ext, backend, incremental_since="X", row_limit=5).sql
            assert sql.lstrip().upper().startswith("SELECT")
            assert ";" not in sql  # a single statement; no stacked write
            words = set(sql.upper().replace("[", " ").replace("]", " ").split())
            assert words.isdisjoint(
                {"INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "TRUNCATE", "ALTER"}
            )


class TestJoins:
    """Header/detail enrichment: a base position joined to a pricing table."""

    def _loan_with_pricing(self) -> TableExtraction:
        # FLEXCUBE-shaped: the loan account carries balance, the LD contract
        # carries pricing, joined by ACCOUNT_ID.
        return TableExtraction(
            table="CORE.CLTB_ACCOUNT_MASTER",
            record_kind="position",
            joins=(
                JoinExtraction(
                    table="CORE.LDTB_CONTRACT_MASTER",
                    on={"ACCOUNT_ID": "ACCOUNT_ID"},
                    columns=("INTEREST_RATE", "RATE_TYPE", "RATE_SPREAD"),
                ),
            ),
        )

    def test_left_join_projects_detail_columns_by_bare_name(self) -> None:
        query = build_select(self._loan_with_pricing(), "oracle")
        assert query.sql == (
            'SELECT t0.*, '
            'j1."INTEREST_RATE" AS "INTEREST_RATE", '
            'j1."RATE_TYPE" AS "RATE_TYPE", '
            'j1."RATE_SPREAD" AS "RATE_SPREAD" '
            'FROM "CORE"."CLTB_ACCOUNT_MASTER" t0 '
            'LEFT JOIN "CORE"."LDTB_CONTRACT_MASTER" j1 '
            'ON t0."ACCOUNT_ID" = j1."ACCOUNT_ID"'
        )
        assert query.parameters == {}

    def test_sqlserver_bracket_quoting_and_alias(self) -> None:
        sql = build_select(self._loan_with_pricing(), "sqlserver").sql
        assert "[CORE].[CLTB_ACCOUNT_MASTER] t0" in sql
        assert (
            "LEFT JOIN [CORE].[LDTB_CONTRACT_MASTER] j1 "
            "ON t0.[ACCOUNT_ID] = j1.[ACCOUNT_ID]"
        ) in sql
        assert "j1.[INTEREST_RATE] AS [INTEREST_RATE]" in sql

    def test_inner_join_and_composite_key(self) -> None:
        ext = TableExtraction(
            table="CORE.ACCT",
            record_kind="position",
            joins=(
                JoinExtraction(
                    table="CORE.PRICE",
                    on={"INST_ID": "INST_ID", "ACCOUNT_ID": "ACCT_ID"},
                    columns=("RATE",),
                    kind="inner",
                ),
            ),
        )
        sql = build_select(ext, "oracle").sql
        assert "INNER JOIN " in sql
        assert 't0."INST_ID" = j1."INST_ID" AND t0."ACCOUNT_ID" = j1."ACCT_ID"' in sql

    def test_filters_and_incremental_bind_to_base_alias(self) -> None:
        ext = TableExtraction(
            table="CORE.ACCT",
            record_kind="position",
            incremental_column="UPDATED_AT",
            filters={"CCY": "GHS"},
            joins=(
                JoinExtraction(
                    table="CORE.PRICE", on={"ACCOUNT_ID": "ACCOUNT_ID"}, columns=("RATE",)
                ),
            ),
        )
        query = build_select(ext, "sqlserver", incremental_since="2026-06-30")
        assert "WHERE t0.[CCY] = ? AND t0.[UPDATED_AT] > ?" in query.sql
        assert "ORDER BY t0.[UPDATED_AT]" in query.sql
        assert query.parameters == ["GHS", "2026-06-30"]

    def test_join_column_collision_is_rejected(self) -> None:
        ext = TableExtraction(
            table="CORE.ACCT",
            record_kind="position",
            columns=("ACCOUNT_ID", "RATE"),
            joins=(
                JoinExtraction(
                    table="CORE.PRICE", on={"ACCOUNT_ID": "ACCOUNT_ID"}, columns=("RATE",)
                ),
            ),
        )
        with pytest.raises(IdentifierError):
            build_select(ext, "oracle")

    def test_joined_query_is_still_read_only_across_backends(self) -> None:
        ext = self._loan_with_pricing()
        for backend in ("oracle", "sqlserver", "jdbc", "odbc"):
            sql = build_select(ext, backend, row_limit=5).sql
            assert sql.lstrip().upper().startswith("SELECT")
            assert ";" not in sql
            words = set(sql.upper().replace("[", " ").replace("]", " ").split())
            assert words.isdisjoint(
                {"INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "TRUNCATE", "ALTER"}
            )

    def test_unsafe_join_identifier_is_rejected(self) -> None:
        ext = TableExtraction(
            table="CORE.ACCT",
            record_kind="position",
            joins=(
                JoinExtraction(
                    table="CORE.PRICE",
                    on={"ACCOUNT_ID": "ACCOUNT_ID"},
                    columns=("RATE; DROP TABLE X",),
                ),
            ),
        )
        with pytest.raises(IdentifierError):
            build_select(ext, "oracle")
