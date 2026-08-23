"""The one name for "a derived financial fact, whichever generation it is".

Deliberately its own module rather than a line in ``app/models/__init__.py``:
that aggregator is architecturally exempt from the case-plane boundary check
*because it is a pure re-export list*, and
``tests/architecture/test_case_plane_boundary.py
::test_the_model_aggregator_exemption_stays_logic_free`` fails the moment it
grows anything that is not an import or an assignment — a type alias included.
"""

from __future__ import annotations

from app.models.live import CurrentFinancialFact
from app.models.regulatory import BankFinancialFact

#: EITHER generation of a derived financial fact. ``BankFinancialFact`` is the
#: period-anchored official spine; ``CurrentFinancialFact`` is the live plane's
#: current generation. They carry identical value columns and differ only in what
#: anchors them (a reporting period vs a source business date), which is what lets
#: the live tier and the official run share ONE engine translation instead of
#: keeping two that can drift. Calculation modules annotate their fact readers with
#: this and take a ``Sequence`` so either list is accepted (a ``list`` parameter is
#: invariant and would still refuse the other one).
type FinancialFactRow = BankFinancialFact | CurrentFinancialFact
