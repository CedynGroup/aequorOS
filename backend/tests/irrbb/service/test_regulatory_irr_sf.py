"""The IRRBB Standardised Framework service: lifecycle, refusals, provenance.

These tests are about the SERVICE, not the arithmetic. The eight golden vectors
of P5-DESIGN §3 already pin every number the engine produces
(``tests/irrbb/domain/``), so nothing here asserts a magnitude the engine chose.
What is asserted instead:

* a run is minted, sealed and reproducible — the same book hashes the same way;
* every refusal is DATA on the run row with a typed code, never an HTTP 500;
* the option refusal carries exactly one name (D-061);
* which governed rows the run resolved is recorded, and moving one in the
  console moves the answer with no code change (D-024, SF-8);
* the legacy IRRBB engine is untouched by an SF run (P5-DESIGN §1.7).

This module's other-suite cases live in ``test_regulatory_irr_sf_integration.py``.
"""

from __future__ import annotations

from app.irrbb.domain import standardised as sf
from app.irrbb.domain import standardised_cash_flows as sfcf
from app.irrbb.service import regulatory_irr_sf


def test_every_assumption_marker_carries_bank_facing_copy() -> None:
    """A raw marker must never print, so the map must be COMPLETE, not mostly.

    ``ASSUMPTION_LABELS.get(marker, marker)`` falls back to the raw key at
    three surfaces — the validation row, the read model and the ICAAP
    assumptions table — so a marker added without copy degrades quietly into
    jargon on a document a supervisor reads. Collected by reflection rather
    than listed, so the guard cannot go stale behind a new marker.
    """
    engine = {name: value for name, value in vars(sf).items() if name.startswith("TALLY_")}
    markers = (
        set(sfcf.TALLY_MARKERS)
        | set(engine.values())
        | {regulatory_irr_sf.TALLY_TD_WHOLESALE_DEMANDABLE}
    )

    missing = markers - set(regulatory_irr_sf.ASSUMPTION_LABELS)

    assert missing == set(), f"no bank-facing copy for {sorted(missing)}"
    assert all(label != marker for marker, label in regulatory_irr_sf.ASSUMPTION_LABELS.items())
