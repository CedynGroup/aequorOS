"""Synthetic dates, identities and query payloads for the BI route suites."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

AS_OF = dt.date(2026, 8, 31)
BUILT_AT = dt.datetime(2026, 9, 1, 2, tzinfo=dt.UTC)
BANK_ID = "BK-BIROUTE1"
SIBLING_BANK_ID = "BK-BIOTHER1"
#: A SECOND institution of the same tenant: in scope for the 404 rule, out of
#: scope for an institution-scoped binding that names only the first (S3).
SAME_TENANT_BANK_ID = "BK-BIROUTE2"
CP_ONE = UUID("11111111-1111-4111-8111-111111111111")
CP_TWO = UUID("22222222-2222-4222-8222-222222222222")
FINGERPRINT = "f" * 64
#: Figures that exist ONLY on the second institution of the same tenant, so a
#: response that contains one of them has leaked across institutions.
OTHER_BANK_ROWS = 41234
OTHER_BANK_FINGERPRINT = "a1b2c3d4" * 8
BASE = f"/api/v1/banks/{BANK_ID}/bi"
SIBLING_BASE = f"/api/v1/banks/{SIBLING_BANK_ID}/bi"
BALANCE_BY_BRANCH_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["branch.code"],
    "time": {"as_of": AS_OF.isoformat()},
}
OBLIGOR_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["counterparty.name"],
    "time": {"as_of": AS_OF.isoformat()},
}
RECORD_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["position.source_reference"],
    "time": {"as_of": AS_OF.isoformat()},
}
