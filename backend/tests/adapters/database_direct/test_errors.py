"""Error classification + the bank-facing leak canary."""

from __future__ import annotations

import pytest

from app.adapters.database_direct.errors import (
    MESSAGE_TEMPLATES,
    DatabaseDirectError,
    DbDirectErrorCode,
    classify_dbapi_error,
    render_bank_facing,
)

# A sentinel that must NEVER appear on a bank-facing surface: it stands in for
# the raw DBAPI/ODBC/JDBC diagnostic text an error carries internally.
INTERNAL_MARKER = "X-DBDIRECT-INTERNAL-DO-NOT-SURFACE"


class _FakeDbapiError(Exception):
    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        if sqlstate is not None:
            self.sqlstate = sqlstate


class TestSqlstateClassification:
    @pytest.mark.parametrize(
        ("sqlstate", "expected"),
        [
            ("28000", DbDirectErrorCode.CREDENTIAL_INVALID),
            ("42000", DbDirectErrorCode.INSUFFICIENT_PRIVILEGE),
            ("42S02", DbDirectErrorCode.TABLE_NOT_FOUND),
            ("42P01", DbDirectErrorCode.TABLE_NOT_FOUND),
            ("08001", DbDirectErrorCode.CORE_UNAVAILABLE),
            ("HYT00", DbDirectErrorCode.QUERY_TIMEOUT),
            ("57P03", DbDirectErrorCode.CORE_UNAVAILABLE),
        ],
    )
    def test_classifies_by_sqlstate(self, sqlstate: str, expected: DbDirectErrorCode) -> None:
        err = classify_dbapi_error(_FakeDbapiError("boom", sqlstate), database="Sample Bank core")
        assert err.code is expected


class TestTextClassification:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("Login failed for user 'AEQ'", DbDirectErrorCode.CREDENTIAL_INVALID),
            ("permission denied for relation positions", DbDirectErrorCode.INSUFFICIENT_PRIVILEGE),
            ("ORA-00942: table or view does not exist", DbDirectErrorCode.TABLE_NOT_FOUND),
            ("query execution timed out", DbDirectErrorCode.QUERY_TIMEOUT),
            ("SSL handshake failed", DbDirectErrorCode.TLS_REQUIRED),
            ("could not connect to server: refused", DbDirectErrorCode.NETWORK_ERROR),
            ("something entirely unexpected", DbDirectErrorCode.CORE_UNAVAILABLE),
        ],
    )
    def test_classifies_by_text_when_no_sqlstate(
        self, message: str, expected: DbDirectErrorCode
    ) -> None:
        err = classify_dbapi_error(_FakeDbapiError(message), database="Sample Bank core")
        assert err.code is expected


class TestRendering:
    def test_defaults_fill_all_placeholders(self) -> None:
        # Render every template with no params; none may retain a raw brace.
        for code in DbDirectErrorCode:
            rendered = render_bank_facing(code)
            assert "{" not in rendered.message and "}" not in rendered.message
            assert rendered.actions
            assert rendered.severity in ("informational", "warning", "urgent")

    def test_every_code_has_a_template(self) -> None:
        assert set(MESSAGE_TEMPLATES) == set(DbDirectErrorCode)

    def test_business_label_used_not_physical_names(self) -> None:
        rendered = render_bank_facing(
            DbDirectErrorCode.TABLE_NOT_FOUND, database="Sample Bank core", table="loans"
        )
        assert "Sample Bank core" in rendered.message
        assert "loans" in rendered.message


class TestLeakCanary:
    def test_internal_detail_never_reaches_bank_facing_surface(self) -> None:
        err = classify_dbapi_error(
            _FakeDbapiError(f"ORA-01017 {INTERNAL_MARKER} dsn=corebank:1521 pw=deadbeef", "28000"),
            database="Sample Bank core",
        )
        assert err.code is DbDirectErrorCode.CREDENTIAL_INVALID
        assert INTERNAL_MARKER not in str(err)
        assert INTERNAL_MARKER not in err.bank_facing.message
        assert INTERNAL_MARKER in err.internal_detail

    def test_str_renders_only_the_bank_facing_message(self) -> None:
        err = DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CORE_UNAVAILABLE, database="Sample Bank core"),
            internal_detail=f"raw socket error {INTERNAL_MARKER}",
        )
        assert str(err) == err.bank_facing.message
        assert INTERNAL_MARKER not in str(err)

    def test_code_property(self) -> None:
        err = DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.TLS_REQUIRED, database="X"),
            internal_detail="d",
        )
        assert err.code is DbDirectErrorCode.TLS_REQUIRED
