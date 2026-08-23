"""Shared vocabulary for the equivalence suite.

Deliberately thin: the tolerances and the identity of each declared pair are
the interesting content, and they belong where a reviewer will read them.
"""

from __future__ import annotations

from decimal import Decimal

#: Two engines that consume the same inputs through the same code must agree
#: EXACTLY. Every ratio in this repository is Decimal end to end and quantized
#: once, at the same place, so there is no float drift to absorb.
EXACT = Decimal("0")

#: A run metric is persisted as a string and re-parsed by the package
#: generator. Round-tripping a ``Decimal`` through ``str`` is lossless, so this
#: is exact too — it is named separately because the reason differs.
RUN_METRIC_ROUNDTRIP = Decimal("0")

#: BoG template forms present amounts in the return's own unit and the
#: workbook's own rounding. A form cell is compared to the engine figure at the
#: precision the TEMPLATE declares, never at the engine's.
#: (Populated per-form where a form/engine overlap is actually asserted.)
FORM_UNIT_ROUNDING = Decimal("1")
