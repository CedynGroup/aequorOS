import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { RegulatoryRunReadFromJSON } from "@aequoros/risk-service-api";
import {
  parseCurrencyGaps,
  parseStressedLadder,
} from "../features/liquidity/components/ladderData";
import {
  currencyLadders,
  adversarialStoredStressFigures,
  liquiditySeries,
  liquidityAmountQuanta,
} from "./liquidity/testing/generators";
import { assertLadderReconciles } from "./liquidity/testing/assertions";
import {
  capitalMeasurements,
  capitalFloorHundredths,
} from "./capital/testing/generators";
import { assertCapitalDisplay } from "./capital/testing/assertions";

describe("financial generator validity", () => {
  it("produces finite complete adversarial stored decoder figures", () => {
    fc.assert(
      fc.property(adversarialStoredStressFigures, (stored) => {
        const run = RegulatoryRunReadFromJSON({
          metrics: { stressed_ladder: [stored] },
          inputs: {},
          line_items: [],
          metric_results: [],
          validations: [],
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
        });
        const parsed = parseStressedLadder(run);
        expect(parsed).toHaveLength(1);
        expect(parsed[0].stressedNet).toEqual(stored.stressed_net.map(Number));
        expect(parsed[0].stableCore).toBe(Number(stored.stable_core));
      }),
      { numRuns: 100 },
    );
  });
  it("produces five-horizon ladders accepted by the stored parser", () => {
    fc.assert(
      fc.property(currencyLadders, (ladder) => {
        const run = RegulatoryRunReadFromJSON({
          metrics: { currency_gaps: [ladder] },
          inputs: {},
          line_items: [],
          metric_results: [],
          validations: [],
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
        });
        const parsed = parseCurrencyGaps(run);
        expect(parsed).toHaveLength(1);
        assertLadderReconciles(
          parsed[0],
          ladder.assetsTotal,
          ladder.liabilitiesTotal,
        );
        expect(parsed[0].assets.every((v) => v >= 0)).toBe(true);
        expect(parsed[0].liabilities.every((v) => v >= 0)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });
  it("produces nonnegative amount quanta and complete horizons", () => {
    fc.assert(
      fc.property(liquidityAmountQuanta, liquiditySeries, (amount, series) => {
        expect(Number.isSafeInteger(amount) && amount >= 0).toBe(true);
        expect(series).toHaveLength(5);
        expect(series.every((v) => Number.isSafeInteger(v) && v >= 0)).toBe(
          true,
        );
      }),
      { numRuns: 100 },
    );
  });
  it("produces ordered capital ratios with consistent governed headroom", () => {
    fc.assert(
      fc.property(capitalMeasurements, (values) => {
        const [car, headroom, tier1, tier1Headroom, cet1, cet1Headroom] =
          values;
        expect(car >= tier1 && tier1 >= cet1 && cet1 >= 0).toBe(true);
        expect(headroom).toBe(car - capitalFloorHundredths.car);
        expect(tier1Headroom).toBe(tier1 - capitalFloorHundredths.tier1);
        expect(cet1Headroom).toBe(cet1 - capitalFloorHundredths.cet1);
        values.forEach((v, i) =>
          assertCapitalDisplay(
            `${(v / 100).toFixed(2)}${i % 2 ? " pp" : "%"}`,
            v,
            i % 2 === 1,
          ),
        );
      }),
      { numRuns: 100 },
    );
  });
});
