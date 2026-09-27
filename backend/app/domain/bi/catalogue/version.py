"""The catalogue version, bumped by code review whenever a member changes.

``CATALOGUE_VERSION`` enters the mart build fingerprint
(``mart_builder.fingerprint_for``) and every ``bi_query_log`` row, so a
catalogue change invalidates cached builds and is attributable in the audit
trail. Bump it when a member's id, table/column binding, aggregation, FX rule,
sensitivity or module changes; adding a label is not a version change.
"""

from __future__ import annotations

CATALOGUE_VERSION: str = "2.0.0"
"""2.0.0 (D-105…D-112): every target variant id gained a register-version segment
(``loans.balance_rc.budget.target``), so EVERY id minted at 1.1.0 was renamed —
the one change this file's own rule calls breaking — and the single ``ratio``
value type was split into ``fraction`` / ``index`` / ``duration_years`` so a
surface can render each correctly. Nothing parses this string as semver; it is an
opaque fingerprint and ETag input, and the major is the only honest signal for a
mass rename."""
