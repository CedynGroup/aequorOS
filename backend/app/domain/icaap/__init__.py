"""Pure ICAAP domain: framework registry, editor grammar, block catalogue, readiness.

Nothing here imports ``app.services``, ``app.models``, ``app.api`` or
``app.features`` — ``tests/architecture/test_dependency_boundaries.py`` pins that,
and it is what lets a second product segment reuse the same framework text.

No jurisdiction identity lives in this package's Python: country, regulator,
currency and every regulatory number come from the framework JSON under
``frameworks/<jurisdiction>/`` (data) and from the governed parameter control
plane (founder directive D-024).
"""

from __future__ import annotations
