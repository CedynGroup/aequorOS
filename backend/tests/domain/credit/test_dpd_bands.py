"""The analytical DPD bands are ONE definition (P0-8).

The seven bands below are written out by hand from the three copies they
replace — the classification service's ``_DPD_BANDS``, ``migration.ROLL_BUCKETS``
and ``regulatory_credit._ROLL_BAND_EDGES`` — never echoed from the module. Every
former owner is pinned to the domain definition so the copies cannot come back.
"""

from __future__ import annotations

import pytest

from app.domain.credit import migration
from app.domain.credit.dpd_bands import DPD_BAND_CODES, DPD_BANDS, DpdBand, dpd_band
from app.services import loan_classification, regulatory_credit

EXPECTED_BANDS: tuple[tuple[str, str, int, int | None], ...] = (
    ("current", "Current", 0, 0),
    ("1_29", "1–29 days", 1, 29),
    ("30_59", "30–59 days", 30, 59),
    ("60_89", "60–89 days", 60, 89),
    ("90_179", "90–179 days", 90, 179),
    ("180_359", "180–359 days", 180, 359),
    ("360_plus", "360+ days", 360, None),
)


def test_the_bands_are_exactly_the_seven_analytical_bands() -> None:
    assert tuple(tuple(band) for band in DPD_BANDS) == EXPECTED_BANDS
    assert tuple(code for code, _, _, _ in EXPECTED_BANDS) == DPD_BAND_CODES
    assert all(isinstance(band, DpdBand) for band in DPD_BANDS)


def test_the_bands_are_contiguous_and_end_open() -> None:
    """Each band starts the day after the previous one ends; only the last is open."""
    for previous, current in zip(DPD_BANDS, DPD_BANDS[1:], strict=False):
        assert previous.maximum is not None
        assert current.minimum == previous.maximum + 1
    assert DPD_BANDS[-1].maximum is None


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (None, None),
        (-1, None),
        (0, "current"),
        (1, "1_29"),
        (29, "1_29"),
        (30, "30_59"),
        (59, "30_59"),
        (60, "60_89"),
        (89, "60_89"),
        (90, "90_179"),
        (179, "90_179"),
        (180, "180_359"),
        (359, "180_359"),
        (360, "360_plus"),
        (10_000, "360_plus"),
    ],
)
def test_dpd_band_edges(days: int | None, expected: str | None) -> None:
    assert dpd_band(days) == expected


def test_the_three_former_copies_read_the_domain_definition() -> None:
    assert loan_classification._DPD_BANDS is DPD_BANDS
    assert migration.ROLL_BUCKETS is DPD_BAND_CODES
    assert regulatory_credit._dpd_bucket is dpd_band
