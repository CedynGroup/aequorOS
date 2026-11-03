import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { RegulatoryRunReadFromJSON } from "@aequoros/risk-service-api";
import {
  parseCurrencyGaps,
  parseStressedLadder,
} from "../features/liquidity/components/ladderData";

function storedRun(metrics: Record<string, unknown>) {
  return RegulatoryRunReadFromJSON({
    metrics,
    inputs: {},
    line_items: [],
    metric_results: [],
    validations: [],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  });
}

import {
  currencyLadders,
  adversarialStoredStressFigures,
  liquidityAmountQuanta as cents,
  decimalStrings,
} from "./liquidity/testing/generators";
import { assertLadderReconciles } from "./liquidity/testing/assertions";
const gap = (values: unknown = ["100", "0", "-25", "10.50", "2"]) => ({
  currency: "EUR",
  assets: values,
  liabilities: values,
  net: values,
  cumulative: values,
});
const stress = (values: unknown = ["100", "0", "-25", "10.50", "2"]) => ({
  currency: "USD",
  contractual_liabilities: values,
  stressed_liabilities: values,
  stressed_net: values,
  stressed_cumulative: values,
  demand_deposits: "250.50",
  stable_core: "125.25",
});

describe("stored liquidity ladder figures", () => {
  it("preserves currency isolation and amount ordering when a stored horizon changes", () => {
    fc.assert(
      fc.property(
        currencyLadders,
        cents,
        fc.integer({ min: 0, max: 4 }),
        (ladder, increment, horizon) => {
          const amounts = ladder.assets.map((v) =>
            Math.round(Number(v) * 10_000),
          );
          const original = [ladder, { ...ladder, currency: "JPY" }];
          const updatedAmounts = [...amounts];
          updatedAmounts[horizon] += Math.abs(increment);
          const changed = [
            { ...original[0], assets: decimalStrings(updatedAmounts) },
            original[1],
          ];
          const before = parseCurrencyGaps(
            storedRun({ currency_gaps: original }),
          );
          const after = parseCurrencyGaps(
            storedRun({ currency_gaps: changed }),
          );
          expect(after[0].assets[horizon]).toBeGreaterThanOrEqual(
            before[0].assets[horizon],
          );
          expect(after[0].assets[horizon]).toBe(
            updatedAmounts[horizon] / 10_000,
          );
          expect(after[0].liabilities).toEqual(before[0].liabilities);
          expect(after[0].net).toEqual(before[0].net);
          expect(after[0].cumulative).toEqual(before[0].cumulative);
          expect(after[1]).toEqual(before[1]);
          amounts.forEach((amount, index) => {
            if (index !== horizon)
              expect(after[0].assets[index]).toBe(amount / 10_000);
          });
          expect(
            parseCurrencyGaps(
              storedRun({ currency_gaps: changed.toReversed() }),
            ),
          ).toEqual(after.toReversed());
        },
      ),
      { numRuns: 100 },
    );
  });

  it("preserves zero, deficits, decimals and currency identity", () => {
    expect(parseCurrencyGaps(storedRun({ currency_gaps: [gap()] }))).toEqual([
      {
        currency: "EUR",
        assets: [100, 0, -25, 10.5, 2],
        liabilities: [100, 0, -25, 10.5, 2],
        net: [100, 0, -25, 10.5, 2],
        cumulative: [100, 0, -25, 10.5, 2],
      },
    ]);
    expect(
      parseStressedLadder(storedRun({ stressed_ladder: [stress()] })),
    ).toEqual([
      {
        currency: "USD",
        contractualLiabilities: [100, 0, -25, 10.5, 2],
        stressedLiabilities: [100, 0, -25, 10.5, 2],
        stressedNet: [100, 0, -25, 10.5, 2],
        stressedCumulative: [100, 0, -25, 10.5, 2],
        demandDeposits: 250.5,
        stableCore: 125.25,
      },
    ]);
  });

  it("round-trips valid contractual ladders and reconciles stored horizons", () => {
    fc.assert(
      fc.property(currencyLadders, (ladder) => {
        const parsed = parseCurrencyGaps(
          storedRun({ currency_gaps: [ladder] }),
        );
        expect(parsed).toHaveLength(1);
        assertLadderReconciles(
          parsed[0],
          ladder.assetsTotal,
          ladder.liabilitiesTotal,
        );
        for (const field of [
          "assets",
          "liabilities",
          "net",
          "cumulative",
        ] as const)
          expect(parsed[0][field]).toEqual(ladder[field].map(Number));
        expect(parsed[0].currency).toBe(ladder.currency);
      }),
      { numRuns: 100 },
    );
  });

  it("round-trips stressed horizons and deposit assumptions independently", () => {
    fc.assert(
      fc.property(adversarialStoredStressFigures, (stored) => {
        const parsed = parseStressedLadder(
          storedRun({ stressed_ladder: [stored] }),
        );
        expect(parsed).toEqual([
          {
            currency: stored.currency,
            contractualLiabilities: stored.contractual_liabilities.map(Number),
            stressedLiabilities: stored.stressed_liabilities.map(Number),
            stressedNet: stored.stressed_net.map(Number),
            stressedCumulative: stored.stressed_cumulative.map(Number),
            demandDeposits: Number(stored.demand_deposits),
            stableCore: Number(stored.stable_core),
          },
        ]);
      }),
      { numRuns: 100 },
    );
  });

  it.each([undefined, null, {}, "unavailable"])(
    "shows no ladder when the collection is %s",
    (raw) => {
      expect(parseCurrencyGaps(storedRun({ currency_gaps: raw }))).toEqual([]);
      expect(parseStressedLadder(storedRun({ stressed_ladder: raw }))).toEqual(
        [],
      );
      expect(parseCurrencyGaps(undefined)).toEqual([]);
      expect(parseStressedLadder(undefined)).toEqual([]);
    },
  );

  it("drops malformed horizons without hiding valid neighboring currencies", () => {
    const invalid = fc.oneof(
      fc.constant(null),
      fc.constant(false),
      fc.constant("not a decimal"),
      fc.constant("Infinity"),
      fc.constant({}),
      fc.constant([]),
    );
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 4 }), invalid, (index, value) => {
        const bad = ["1", "2", "3", "4", "5"] as unknown[];
        bad[index] = value;
        for (const key of ["assets", "liabilities", "net", "cumulative"]) {
          expect(
            parseCurrencyGaps(
              storedRun({ currency_gaps: [{ ...gap(), [key]: bad }, gap()] }),
            ),
          ).toEqual(parseCurrencyGaps(storedRun({ currency_gaps: [gap()] })));
        }
        for (const key of [
          "contractual_liabilities",
          "stressed_liabilities",
          "stressed_net",
          "stressed_cumulative",
        ]) {
          expect(
            parseStressedLadder(
              storedRun({
                stressed_ladder: [{ ...stress(), [key]: bad }, stress()],
              }),
            ),
          ).toEqual(
            parseStressedLadder(storedRun({ stressed_ladder: [stress()] })),
          );
        }
      }),
      { numRuns: 100 },
    );
  });

  it("refuses truncated or extra horizons and absent deposit assumptions", () => {
    fc.assert(
      fc.property(
        fc.array(cents, { maxLength: 10 }).filter((v) => v.length !== 5),
        (values) => {
          expect(
            parseCurrencyGaps(storedRun({ currency_gaps: [gap(values)] })),
          ).toEqual([]);
          expect(
            parseStressedLadder(
              storedRun({ stressed_ladder: [stress(values)] }),
            ),
          ).toEqual([]);
        },
      ),
      { numRuns: 100 },
    );
    for (const key of ["demand_deposits", "stable_core"]) {
      for (const missing of [null, undefined, "NaN", false]) {
        expect(
          parseStressedLadder(
            storedRun({ stressed_ladder: [{ ...stress(), [key]: missing }] }),
          ),
        ).toEqual([]);
      }
    }
    expect(
      parseCurrencyGaps(
        storedRun({ currency_gaps: [null, [], { ...gap(), currency: null }] }),
      ),
    ).toEqual([]);
    expect(
      parseStressedLadder(
        storedRun({
          stressed_ladder: [null, [], { ...stress(), currency: null }],
        }),
      ),
    ).toEqual([]);
  });
});
