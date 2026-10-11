import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { RegulatoryRunReadFromJSON } from "@aequoros/risk-service-api";
import {
  parseCurrencyGaps,
  parseStressedLadder,
} from "../components/liquidity/ladderData";

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

const cents = fc.integer({ min: -100_000_000, max: 100_000_000 });
const series = fc.array(cents, { minLength: 5, maxLength: 5 });
const decimalStrings = (values: number[]) =>
  values.map((value) => (value / 100).toFixed(2));
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
        series,
        cents,
        fc.integer({ min: 0, max: 4 }),
        (amounts, increment, horizon) => {
          const original = [
            { ...gap(decimalStrings(amounts)), currency: "EUR" },
            { ...gap(decimalStrings(amounts.toReversed())), currency: "JPY" },
          ];
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
          expect(after[0].assets[horizon]).toBe(updatedAmounts[horizon] / 100);
          expect(after[0].liabilities).toEqual(before[0].liabilities);
          expect(after[0].net).toEqual(before[0].net);
          expect(after[0].cumulative).toEqual(before[0].cumulative);
          expect(after[1]).toEqual(before[1]);
          amounts.forEach((amount, index) => {
            if (index !== horizon)
              expect(after[0].assets[index]).toBe(amount / 100);
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

  it("round-trips all contractual horizon amounts without recomputing persisted figures", () => {
    fc.assert(
      fc.property(
        series,
        series,
        series,
        series,
        (assets, liabilities, net, cumulative) => {
          const result = parseCurrencyGaps(
            storedRun({
              currency_gaps: [
                {
                  currency: "JPY",
                  assets: decimalStrings(assets),
                  liabilities: decimalStrings(liabilities),
                  net: decimalStrings(net),
                  cumulative: decimalStrings(cumulative),
                },
              ],
            }),
          );
          expect(result).toEqual([
            {
              currency: "JPY",
              assets: assets.map((v) => v / 100),
              liabilities: liabilities.map((v) => v / 100),
              net: net.map((v) => v / 100),
              cumulative: cumulative.map((v) => v / 100),
            },
          ]);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("round-trips stressed horizons and deposit assumptions independently", () => {
    fc.assert(
      fc.property(
        series,
        series,
        series,
        series,
        cents,
        cents,
        (contractual, stressed, net, cumulative, demand, core) => {
          const result = parseStressedLadder(
            storedRun({
              stressed_ladder: [
                {
                  currency: "GBP",
                  contractual_liabilities: decimalStrings(contractual),
                  stressed_liabilities: decimalStrings(stressed),
                  stressed_net: decimalStrings(net),
                  stressed_cumulative: decimalStrings(cumulative),
                  demand_deposits: (demand / 100).toFixed(2),
                  stable_core: (core / 100).toFixed(2),
                },
              ],
            }),
          );
          expect(result).toEqual([
            {
              currency: "GBP",
              contractualLiabilities: contractual.map((v) => v / 100),
              stressedLiabilities: stressed.map((v) => v / 100),
              stressedNet: net.map((v) => v / 100),
              stressedCumulative: cumulative.map((v) => v / 100),
              demandDeposits: demand / 100,
              stableCore: core / 100,
            },
          ]);
        },
      ),
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
