"""The certified dashboards, resolved for one reader and one reporting date.

``tests/bi/domain/test_packs.py`` proves the FILES: seven packs, every member
resolving, no date anywhere, no currency or regulator in a label. This file
proves what the ROUTE does with them, and each test is a property the product
depends on rather than a shape the code happens to produce:

* **the date is the caller's.** Every widget query in a response is byte-identical
  to ``BiPackQuery.for_period(as_of)``, and no date appears anywhere in the
  response that is not derivable from the ``as_of`` that was asked for. Nothing
  may bypass ``for_period``, or a certified dashboard becomes a pinned date.
* **a refused widget discloses nothing.** The refusal carries its id and its place
  on the canvas. Not the title, not the caption, not the measure, not the
  dimension, not the filter, not a figure — which is checked by asserting the
  response body does not CONTAIN any of them, not merely that a field is absent.
* **an all-refused dashboard does not read as an empty one.** It says so, in
  production copy, and the counts agree with the widgets.
* **``needs_data`` and ``pending_capability`` pass through unchanged.** Telling a
  bank it "needs data: positions" when it pushes positions every night is a false
  statement about its own book, so the platform-work case stays its own field.
* **an SDI is not served a bank's dashboard** (D-070).

This module's other-suite cases live in ``test_bi_packs_integration.py``.
"""

from __future__ import annotations

from app.bi.domain.catalogue import MeasureDef, catalogue
from app.bi.domain.packs import packs


def test_the_pack_set_still_carries_both_kinds_of_gap() -> None:
    """The pass-through test above is vacuous if no pack names either."""
    datasets = {w.needs_data for spec in packs() for w in spec.widgets if w.needs_data}
    capabilities = {
        w.pending_capability for spec in packs() for w in spec.widgets if w.pending_capability
    }
    assert datasets and capabilities


def test_the_class_gate_is_the_registrys_own_resolution() -> None:
    """Not a new notion of "is this an SDI": the same resolution the mart used.

    Every pack that names an engine measure names one whose authority resolves
    under CRD, which is why the gate closes for s.29 today and opens by itself
    when a pack naming an SDI's own authorities ships.
    """
    from app.bi.service.insights import engine_measure_applies  # noqa: PLC0415

    cat = catalogue()
    named = [
        cat.member(member_id)
        for spec in packs()
        for widget in spec.widgets
        if widget.query is not None
        for member_id in widget.query.measures
    ]
    engine = [m for m in named if isinstance(m, MeasureDef) and m.engine_rule is not None]
    assert engine, "no pack names an engine measure; the gate would be vacuous"
    assert all(
        engine_measure_applies(m, institution_class="bank", capital_regime="crd") for m in engine
    )
    assert not any(
        engine_measure_applies(m, institution_class="sdi", capital_regime="s29") for m in engine
    )
