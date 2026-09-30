"""The catalogue version, bumped by code review whenever a member changes.

``CATALOGUE_VERSION`` enters the mart build fingerprint
(``mart_builder.fingerprint_for``) and every ``bi_query_log`` row, so a
catalogue change invalidates cached builds and is attributable in the audit
trail. Bump it when a member's id, table/column binding, aggregation, FX rule,
sensitivity or module changes; adding a label is not a version change.
"""

from __future__ import annotations

CATALOGUE_VERSION: str = "2.1.0"
"""2.1.0 (Phase 5): five new members — ``position.officer_code`` / ``position.channel``
/ ``position.account_status`` over four new mart columns (migration
``202609280076``), ``loans.arrears_amount_rc`` / ``loans.arrears_share_pct`` over
one of them, and ``gl.branch_ytd_rc`` / ``gl.branch_movement_rc`` over the new
branch ledger mart (``202609280075``). Nothing was renamed, so a minor bump.

**The bump is the mechanism, not its cost.** It enters the build fingerprint, so
the four new fact columns are NULL on every slice already built and without a
fingerprint change the builder would consider those slices current and never
refill them — every officer, channel, status and arrears figure would read empty
for all history while the mart looked perfectly healthy.

2.0.0 (D-105…D-112): every target variant id gained a register-version segment
(``loans.balance_rc.budget.target``), so EVERY id minted at 1.1.0 was renamed —
the one change this file's own rule calls breaking — and the single ``ratio``
value type was split into ``fraction`` / ``index`` / ``duration_years`` so a
surface can render each correctly. Nothing parses this string as semver; it is an
opaque fingerprint and ETag input, and the major is the only honest signal for a
mass rename."""
