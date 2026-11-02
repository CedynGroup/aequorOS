# ICAAP feature boundary

ICAAP's application code and tests live in `app/icaap/` and `tests/icaap/`.
Other features import the `IcaapCycle` model through `app.icaap.public` and pure
calculation contracts through `app.icaap.domain`. No additional service exports
are needed by the current permitted import graph.

Attachment and supervisory-letter storage uses `bank_slug` through
`app.data_engine.public`. These imports stay lazy to preserve the existing import
order. Feature import rules and their ratchet are owned by
[the feature-layout contract](../../../CODEBASE_CONVENTIONS.md#5-feature-layout).

Existing inbound layer inversions from the kernel and reporting remain recorded
in `tests/architecture/feature_boundary_baseline.json`; changing those dependencies
requires a separate architecture decision.
