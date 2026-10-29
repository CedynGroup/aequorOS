# BI feature boundary

BI application code and tests live in `app/bi/` and `tests/bi/`.
Other features use `app.bi.public` for `BUILDER_VERSION` and
`app.bi.domain` for pure calculation contracts. The public version is the same
value stamped onto BI job payloads by the enqueue seam.

## Remaining violations

BI has no private cross-feature import violations in the boundary baseline and
no remaining layered modules or ownership rules. Existing inward layer
inversions remain recorded in the baseline:

- Data Engine's canonical withdrawal and ingestion services enqueue BI jobs.
- The live feature's refresh triggers and pipeline enqueue BI jobs.

These dependencies retain their existing imports and behavior. Moving a service
behind a public export does not resolve an upward layer dependency; changing
that dependency direction requires a separate architecture decision.
