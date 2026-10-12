import fc from "fast-check";

/** Sources: schemas/ingestion.py CanonicalPositionRead, PositionSnapshotRead,
 * IngestionBatchRead; models/canonical.py six-place balances and ten-place rates.
 * One synthetic bank, one month-end batch, unique position/snapshot IDs and one
 * shared lineage node. Whole-cent amounts are intentionally coarser than 6dp.
 */
export const canonicalGraphs = fc
  .tuple(
    fc.uuid(),
    fc.uuid(),
    fc.constantFrom("2026-03-31", "2026-06-30", "2026-12-31"),
    fc
      .integer({ min: 1, max: 12 })
      .chain((count) =>
        fc
          .tuple(
            fc.uniqueArray(fc.uuid(), {
              minLength: count * 2,
              maxLength: count * 2,
            }),
            fc.array(
              fc.tuple(
                fc.oneof(
                  fc.constant(0),
                  fc.integer({ min: 1_000_000, max: 1_000_000_000 }),
                ),
                fc.integer({ min: 500, max: 3500 }),
              ),
              { minLength: count, maxLength: count },
            ),
          )
          .map(([ids, amounts]) =>
            amounts.map(
              ([cents, rate], index) =>
                [ids[index * 2], ids[index * 2 + 1], cents, rate] as const,
            ),
          ),
      ),
  )
  .map(([batchId, lineageId, date, rows]) => {
    const bank = "BK-000000000001";
    const snapshots = rows.map(([positionId, id, cents, rate]) => ({
      id,
      position_id: positionId,
      as_of_date: date,
      validation_status: "accepted",
      balance: (cents / 100).toFixed(6),
      interest_rate: (rate / 10000).toFixed(10),
      ifrs9_stage: 1,
      behavioral_maturity_months: null,
      enrichment_provenance: {},
      lineage_id: lineageId,
      superseded_snapshot_id: null,
    }));
    const positions = rows.map(([id], index) => ({
      id,
      source_system: "EXCEL_CSV",
      source_reference: `SYNTHETIC/LOAN/${index}`,
      position_type: "LOAN",
      currency: "GHS",
      validation_status: "accepted",
      as_of_date: date,
      snapshot_id: snapshots[index].id,
      balance: snapshots[index].balance,
      interest_rate: snapshots[index].interest_rate,
      rate_type: "FIXED",
      contractual_maturity: "2027-12-31",
      lineage_id: lineageId,
    }));
    const batch = {
      id: batchId,
      bank_id: bank,
      source_system: "EXCEL_CSV",
      adapter_version: "synthetic-1",
      extraction_mode: "full",
      status: "accepted",
      as_of_date: date,
      created_at: `${date}T00:00:00Z`,
      started_at: `${date}T00:00:00Z`,
      completed_at: `${date}T00:00:01Z`,
      content_hash: null,
      mapping_config_id: null,
      raw_artifact_path: null,
      report_artifact_path: null,
      error_code: null,
      error_message: null,
      records_extracted: rows.length + 2,
      records_translated: rows.length + 2,
      records_accepted: rows.length + 2,
      records_warning: 0,
      records_error: 0,
      records_blocked: 0,
      validation_report: {
        summary: {
          reference_rows: { capital_structure: 1, behavioral_assumptions: 1 },
        },
      },
    };
    return {
      batch,
      list: {
        bank_id: bank,
        as_of_date: date,
        positions,
        total: rows.length,
        limit: rows.length,
        offset: 0,
      },
      snapshots,
    };
  });
