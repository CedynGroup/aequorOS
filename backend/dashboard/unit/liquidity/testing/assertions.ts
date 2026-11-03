import { expect } from "vitest";
import type { parseCurrencyGaps } from "../../../features/liquidity/components/ladderData";

/** engine.py MONEY 4dp; a single amount quantum covers Number accumulation error.
 * Status, currency identity and persisted ordering continue to compare exactly.
 */
export const liquidityTolerance = {
  absolute: 0.0001,
  unit: "GHS equivalent",
} as const;
export function assertLiquidityAmount(
  actual: number,
  expected: number,
  reason: string,
) {
  expect(Number.isFinite(actual), `Liquidity: ${reason} is not finite`).toBe(
    true,
  );
  expect(
    Math.abs(actual - expected),
    `Liquidity: ${reason}, expected ${expected}, got ${actual}`,
  ).toBeLessThanOrEqual(liquidityTolerance.absolute);
}
export function assertLadderReconciles(
  parsed: ReturnType<typeof parseCurrencyGaps>[number],
  assetsTotal: number,
  liabilitiesTotal: number,
) {
  expect(
    parsed.assets,
    `Liquidity ${parsed.currency}: horizon count`,
  ).toHaveLength(5);
  expect(parsed.liabilities).toHaveLength(5);
  assertLiquidityAmount(
    parsed.assets.reduce((a, b) => a + b, 0),
    assetsTotal,
    "asset buckets reconcile",
  );
  assertLiquidityAmount(
    parsed.liabilities.reduce((a, b) => a + b, 0),
    liabilitiesTotal,
    "liability buckets reconcile",
  );
  assertLiquidityAmount(
    parsed.cumulative[4],
    assetsTotal - liabilitiesTotal,
    "cumulative gap reconciles",
  );
}
