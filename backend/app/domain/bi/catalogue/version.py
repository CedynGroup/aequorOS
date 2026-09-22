"""The catalogue version, bumped by code review whenever a member changes.

``CATALOGUE_VERSION`` enters the mart build fingerprint
(``mart_builder.fingerprint_for``) and every ``bi_query_log`` row, so a
catalogue change invalidates cached builds and is attributable in the audit
trail. Bump it when a member's id, table/column binding, aggregation, FX rule,
sensitivity or module changes; adding a label is not a version change.
"""

from __future__ import annotations

CATALOGUE_VERSION: str = "1.0.0"
