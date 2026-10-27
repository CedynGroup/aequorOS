"""Explicit non-database CI ownership, shared by planning and execution guards."""

from __future__ import annotations

PURE_MODULES = frozenset(
    [
        "tests/scripts/test_feature_moves.py",
        "tests/scripts/test_reporting_reorganization.py",
        "tests/scripts/test_fix_api_client_package.py",
        "tests/domain/irr/test_sf_properties.py",
        "tests/domain/authority/test_registry_completeness.py",
        "tests/domain/ingestion/test_reference_row_enforcement.py",
        "tests/domain/curves/test_nss.py",
        "tests/domain/test_capital_engine.py",
        "tests/domain/test_ftp_engine.py",
        "tests/domain/test_credit_pd.py",
        "tests/domain/test_ingestion_validation.py",
    ]
)
