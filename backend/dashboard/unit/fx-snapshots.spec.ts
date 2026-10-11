import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { RegulatoryRunReadFromJSON } from "@aequoros/risk-service-api";
import { fxPositionSplits, fxRunParameters } from "../components/fx/params";

function storedRun(inputs: unknown) {
  return RegulatoryRunReadFromJSON({
    inputs,
    metrics: {},
    line_items: [],
    metric_results: [],
    validations: [],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  });
}
const cents = fc.integer({ min: -100_000_000, max: 100_000_000 });
const currency = fc.constantFrom("EUR", "JPY", "GBP", "USD", "CHF");
const invalid = fc.constantFrom(
  null,
  undefined,
  true,
  false,
  "NaN",
  "Infinity",
  "not a number",
  {},
  [],
);
const crisis = {
  window_start: "0",
  window_end: "12",
  correlation_uplift: "0.25",
};
const bands = {
  hedge_r2_min_pct: "80",
  hedge_offset_low_pct: "80",
  hedge_offset_high_pct: "125",
};

describe("stored FX run inputs", () => {
  it("round-trips independent per-currency positions without recomputing the saved net", () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(
          fc.tuple(currency, cents, cents, cents, cents, fc.boolean()),
          { selector: (row) => row[0], minLength: 1, maxLength: 5 },
        ),
        (positions) => {
          const facts = positions.map(
            ([ccy, assets, liabilities, derivatives, net, strings]) => {
              const encode = (value: number) =>
                strings ? (value / 100).toFixed(2) : value / 100;
              return {
                fact_group: "fx_position",
                attributes: {
                  currency: ccy,
                  assets_ccy: encode(assets),
                  liabilities_ccy: encode(liabilities),
                  net_derivatives_ccy: encode(derivatives),
                  net_ccy: encode(net),
                },
              };
            },
          );
          expect([...fxPositionSplits(storedRun({ facts })).values()]).toEqual(
            positions.map(([ccy, assets, liabilities, derivatives, net]) => ({
              currency: ccy,
              assetsCcy: assets / 100,
              liabilitiesCcy: liabilities / 100,
              netDerivativesCcy: derivatives / 100,
              netCcy: net / 100,
            })),
          );
        },
      ),
      { numRuns: 100 },
    );
  });

  it("retains real zero and negative amounts while isolating malformed fact records", () => {
    const valid = {
      fact_group: "fx_position",
      attributes: {
        currency: "EUR",
        assets_ccy: "0",
        liabilities_ccy: "12.50",
        net_derivatives_ccy: "-1.25",
        net_ccy: "-13.75",
      },
    };
    expect([
      ...fxPositionSplits(
        storedRun({
          facts: [
            null,
            [],
            {},
            { ...valid, fact_group: "credit" },
            { ...valid, attributes: [] },
            { ...valid, attributes: { currency: null } },
            valid,
          ],
        }),
      ).values(),
    ]).toEqual([
      {
        currency: "EUR",
        assetsCcy: 0,
        liabilitiesCcy: 12.5,
        netDerivativesCcy: -1.25,
        netCcy: -13.75,
      },
    ]);
    fc.assert(
      fc.property(invalid, (amount) => {
        const result = fxPositionSplits(
          storedRun({
            facts: [
              {
                ...valid,
                attributes: {
                  ...valid.attributes,
                  assets_ccy: amount,
                  net_derivatives_ccy: amount,
                },
              },
            ],
          }),
        ).get("EUR");
        expect(result).toEqual({
          currency: "EUR",
          assetsCcy: 0,
          liabilitiesCcy: 12.5,
          netDerivativesCcy: 0,
          netCcy: -13.75,
        });
      }),
      { numRuns: 100 },
    );
  });

  it("has no positions or parameters before a run or a valid snapshot is available", () => {
    expect(fxPositionSplits(undefined).size).toBe(0);
    expect(fxRunParameters(undefined)).toBeNull();
    for (const inputs of [null, undefined, [], "missing", {}]) {
      expect(fxPositionSplits(storedRun(inputs)).size).toBe(0);
      expect(fxRunParameters(storedRun(inputs))).toBeNull();
    }
    for (const malformed of [null, undefined, [], "missing"]) {
      expect(fxPositionSplits(storedRun({ facts: malformed })).size).toBe(0);
      expect(fxRunParameters(storedRun({ parameters: malformed }))).toBeNull();
    }
  });

  it("preserves the saved crisis, hedge bands and scenario shocks across numeric representations", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 500 }),
        fc.integer({ min: 1, max: 500 }),
        fc.integer({ min: 0, max: 100 }),
        fc.integer({ min: 0, max: 100 }),
        fc.integer({ min: 0, max: 100 }),
        fc.integer({ min: 101, max: 200 }),
        fc.integer({ min: -100, max: 100 }),
        fc.boolean(),
        (start, length, uplift, r2, low, high, shock, strings) => {
          const encode = (value: number) => (strings ? String(value) : value);
          expect(
            fxRunParameters(
              storedRun({
                parameters: {
                  crisis: {
                    window_start: encode(start),
                    window_end: encode(start + length),
                    correlation_uplift: encode(uplift / 100),
                  },
                  hedge_bands_pct: {
                    hedge_r2_min_pct: encode(r2),
                    hedge_offset_low_pct: encode(low),
                    hedge_offset_high_pct: encode(high),
                  },
                  depreciation_shocks_pct: {
                    baseline: encode(0),
                    fixture_scenario: encode(shock),
                  },
                },
              }),
            ),
          ).toEqual({
            crisis: {
              windowStart: start,
              windowEnd: start + length,
              correlationUplift: uplift / 100,
            },
            hedgeBands: {
              r2MinPct: r2,
              offsetLowPct: low,
              offsetHighPct: high,
            },
            depreciationShocksPct: { baseline: 0, fixture_scenario: shock },
          });
        },
      ),
      { numRuns: 100 },
    );
  });

  it("omits incomplete parameter groups without losing valid neighboring disclosures", () => {
    fc.assert(
      fc.property(invalid, (bad) => {
        for (const key of Object.keys(crisis)) {
          expect(
            fxRunParameters(
              storedRun({
                parameters: {
                  crisis: { ...crisis, [key]: bad },
                  hedge_bands_pct: bands,
                  depreciation_shocks_pct: { valid: "10", invalid: bad },
                },
              }),
            ),
          ).toEqual({
            crisis: null,
            hedgeBands: { r2MinPct: 80, offsetLowPct: 80, offsetHighPct: 125 },
            depreciationShocksPct: { valid: 10 },
          });
        }
        for (const key of Object.keys(bands)) {
          expect(
            fxRunParameters(
              storedRun({
                parameters: {
                  crisis,
                  hedge_bands_pct: { ...bands, [key]: bad },
                },
              }),
            ),
          ).toEqual({
            crisis: { windowStart: 0, windowEnd: 12, correlationUplift: 0.25 },
            hedgeBands: null,
            depreciationShocksPct: {},
          });
        }
      }),
      { numRuns: 100 },
    );
    expect(fxRunParameters(storedRun({ parameters: {} }))).toEqual({
      crisis: null,
      hedgeBands: null,
      depreciationShocksPct: {},
    });
    expect(
      fxRunParameters(
        storedRun({
          parameters: {
            crisis: [],
            hedge_bands_pct: [],
            depreciation_shocks_pct: [],
          },
        }),
      ),
    ).toEqual({ crisis: null, hedgeBands: null, depreciationShocksPct: {} });
  });
});
