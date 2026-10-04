"""The media type a regulatory artifact is served with is its file's, not its kind's.

A multi-section return's ``csv`` artifact is a ``.zip`` of CSVs
(``app/services/regulatory_reporting/exports/csv.py``). The download routes and
the downtime email used to label it by kind alone, so a client was told an
archive was ``text/csv``. The dashboard journey
``e2e/full-lifecycle.spec.ts`` journey 6 asserts the same through the browser.
"""

from __future__ import annotations

import pytest

from app.features.manage_regulatory_reporting import _artifact_media_type

SPREADSHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.parametrize(
    ("kind", "object_path", "expected"),
    [
        ("csv", "bog_returns/2026-06-30/pkg/BSD2.zip", "application/zip"),
        ("csv", "bog_returns/2026-06-30/pkg/BSD7A.csv", "text/csv"),
        ("xlsx", "bog_returns/2026-06-30/pkg/BSD2.xlsx", SPREADSHEET),
        ("xlsx_working", "bog_returns/2026-06-30/pkg/BSD2.working.xlsx", SPREADSHEET),
        ("pdf", "bog_returns/2026-06-30/pkg/BSD2.pdf", "application/pdf"),
        ("unknown", "bog_returns/2026-06-30/pkg/BSD2.bin", "application/octet-stream"),
    ],
)
def test_artifact_media_type_follows_the_stored_container(
    kind: str, object_path: str, expected: str
) -> None:
    assert _artifact_media_type(kind, object_path) == expected
