# ICAAP feature boundary

ICAAP's application code and tests live in `app/icaap/` and `tests/icaap/`.
Other features import the `IcaapCycle` model through `app.icaap.public` and pure
calculation contracts through `app.icaap.domain`. No additional service exports
are needed by the current permitted import graph.

## Remaining violations

Two private dependency edges remain, with four lazy import sites:

| Consumer                               | Supplier                           | Import sites                              |
| -------------------------------------- | ---------------------------------- | ----------------------------------------- |
| `app.icaap.service.attachments`        | `app.services.ingestion.bank_slug` | `upload_attachment`, `prepare_download`   |
| `app.icaap.service.supervisory_addons` | `app.services.ingestion.bank_slug` | `create_addon`, `prepare_letter_download` |

The helper assigns and persists a bank's storage slug when necessary. It must
remain the single implementation of that behavior. The imports stay lazy to
preserve the existing import order.

These edges are deferred until the Data Engine feature migration lands. The
follow-up is to expose the existing helper through Data Engine's public
interface and switch these four imports, then remove the two corresponding
private-import entries from the boundary baseline. The current move does not
change Data Engine's interface or the feature layer order.

Existing inbound layer inversions from the kernel and reporting remain recorded
in `tests/architecture/feature_boundary_baseline.json`; changing those dependencies
requires a separate architecture decision.
