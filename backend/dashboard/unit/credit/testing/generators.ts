import fc from "fast-check";

/** Sources: credit/pd.py observation floor 30; stored estimates are percentages.
 * Synthetic matched loan-months, a small default count, and two-decimal display
 * measurements. Annual PD is an independent saved estimate, never recomputed.
 */
export const creditEstimates = fc
  .integer({ min: 1, max: 300 })
  .chain((hazard) =>
    fc.tuple(
      fc.constant(hazard),
      fc.integer({ min: hazard * 6, max: hazard * 12 }),
      fc.constant(10_000),
      fc.constant(hazard),
    ),
  );

/** Explicit decoder/display stress cases: independent stored 0–100% figures.
 * These deliberately inconsistent analytical estimates are not valid books.
 */
export const adversarialCreditEstimates = fc.tuple(
  fc.integer({ min: 0, max: 10_000 }),
  fc.integer({ min: 0, max: 10_000 }),
  fc.integer({ min: 100, max: 100_000 }),
  fc.integer({ min: 0, max: 100 }),
);

/** Notice 2025/23 state vocabulary: credit/migration.py. Synthetic facility flows
 * use whole GHS amounts (within the engine's 4dp precision) and matching counts.
 */
export const creditMigrationCells = fc
  .tuple(
    fc.integer({ min: 0, max: 2 }),
    fc.integer({ min: 0, max: 2 }),
    fc.integer({ min: 0, max: 100 }),
    fc.integer({ min: 10_000, max: 1_000_000 }),
  )
  .map(
    ([from, to, loans, perLoan]) => [from, to, loans * perLoan, loans] as const,
  );

/** Seven analytical DPD buckets: domain/credit/dpd_bands.py. A single supplied
 * cell retains its stored exposure-weighted rate; other cells can be absent.
 */
export const creditRollRateCells = fc.tuple(
  fc.integer({ min: 0, max: 6 }),
  fc.integer({ min: 0, max: 6 }),
  fc.integer({ min: 0, max: 1000 }),
  fc.integer({ min: 1, max: 1000 }),
);
