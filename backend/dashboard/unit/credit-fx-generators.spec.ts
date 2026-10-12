import { describe, expect, it } from "vitest";
import fc from "fast-check";
import {
  RegulatoryRunReadFromJSON,
  PdEstimateReadFromJSON,
  MigrationCellReadFromJSON,
  RollRateCellReadFromJSON,
  FxScenarioNopReadFromJSON,
} from "@aequoros/risk-service-api";
import { fxPositionSplits, fxRunParameters } from "../components/fx/params";
import {
  creditEstimates,
  adversarialCreditEstimates,
  creditMigrationCells,
  creditRollRateCells,
} from "./credit/testing/generators";
import { assertCreditPercentage } from "./credit/testing/assertions";
import {
  fxPositions,
  adversarialFxPositions,
  fxScenarios,
  adversarialFxScenarios,
  fxParameterSnapshots,
  alternateFxParameterSnapshots,
} from "./fx/testing/generators";
import {
  assertFxStoredPositions,
  assertFxDisplay,
} from "./fx/testing/assertions";

function run(inputs: unknown) {
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
describe("credit and FX generator validity", () => {
  it("keeps realistic estimate counts and independent stored adversaries within the wire range", () => {
    fc.assert(
      fc.property(
        creditEstimates,
        adversarialCreditEstimates,
        (realistic, adversarial) => {
          expect(realistic[3]).toBe(realistic[0]);
          expect(realistic[1]).toBeGreaterThanOrEqual(realistic[0]);
          for (const [hazard, probability, loanMonths, defaults] of [
            realistic,
            adversarial,
          ]) {
            const decoded = PdEstimateReadFromJSON({
              grade: "standard",
              loan_months: loanMonths,
              defaults_observed: defaults,
              monthly_hazard_pct: String(hazard / 100),
              pd_12m_pct: String(probability / 100),
            });
            expect(decoded.loanMonths).toBe(loanMonths);
            expect(defaults).toBeLessThanOrEqual(loanMonths);
            expect(Number(decoded.pd12mPct)).toBeLessThanOrEqual(100);
            assertCreditPercentage(
              `${(hazard / 100).toFixed(2)}%`,
              hazard / 100,
            );
          }
        },
      ),
      { numRuns: 100 },
    );
  });
  it("keeps migration flows and roll cells finite with matching counts", () => {
    fc.assert(
      fc.property(
        creditMigrationCells,
        creditRollRateCells,
        ([from, to, exposure, loans], roll) => {
          const states = ["performing", "performing_restructured", "npl"];
          const decoded = MigrationCellReadFromJSON({
            from_state: states[from],
            to_state: states[to],
            exposure_ghs: String(exposure),
            loan_count: loans,
          });
          expect(Number(decoded.exposureGhs)).toBe(exposure);
          expect((exposure === 0) === (loans === 0)).toBe(true);
          const [origin, destination, tenths, count] = roll;
          const buckets = [
            "current",
            "1_29",
            "30_59",
            "60_89",
            "90_179",
            "180_359",
            "360_plus",
          ];
          const rollDecoded = RollRateCellReadFromJSON({
            from_bucket: buckets[origin],
            to_bucket: buckets[destination],
            exposure_ghs: String(count * 10_000),
            loan_count: count,
            rate_pct: String(tenths / 10),
          });
          expect(Number(rollDecoded.ratePct)).toBe(tenths / 10);
          expect(Number(rollDecoded.ratePct)).toBeGreaterThanOrEqual(0);
          expect(Number(rollDecoded.ratePct)).toBeLessThanOrEqual(100);
        },
      ),
      { numRuns: 100 },
    );
  });
  it("produces coherent currency splits accepted by the stored decoder", () => {
    fc.assert(
      fc.property(fxPositions, (positions) => {
        const facts = positions.map((p) => ({
          fact_group: "fx_position",
          attributes: {
            currency: p.currency,
            assets_ccy: p.assets / 100,
            liabilities_ccy: p.liabilities / 100,
            net_derivatives_ccy: p.derivatives / 100,
            net_ccy: p.net / 100,
          },
        }));
        assertFxStoredPositions(
          [...fxPositionSplits(run({ facts })).values()],
          positions.map((p) => ({
            currency: p.currency,
            assetsCcy: p.assets / 100,
            liabilitiesCcy: p.liabilities / 100,
            netDerivativesCcy: p.derivatives / 100,
            netCcy: p.net / 100,
          })),
        );
        expect(new Set(positions.map((p) => p.currency)).size).toBe(
          positions.length,
        );
        for (const p of positions) {
          expect(p.assets).toBeGreaterThanOrEqual(0);
          expect(p.liabilities).toBeGreaterThanOrEqual(0);
          expect(p.net).toBe(p.assets - p.liabilities + p.derivatives);
        }
      }),
      { numRuns: 100 },
    );
  });
  it("retains finite independent adversarial currency figures", () => {
    fc.assert(
      fc.property(adversarialFxPositions, (positions) => {
        const facts = positions.map(
          ([currency, assets, liabilities, derivatives, net, strings]) => {
            const encode = (amount: number) =>
              strings ? (amount / 100).toFixed(2) : amount / 100;
            return {
              fact_group: "fx_position",
              attributes: {
                currency,
                assets_ccy: encode(assets),
                liabilities_ccy: encode(liabilities),
                net_derivatives_ccy: encode(derivatives),
                net_ccy: encode(net),
              },
            };
          },
        );
        assertFxStoredPositions(
          [...fxPositionSplits(run({ facts })).values()],
          positions.map(
            ([currency, assets, liabilities, derivatives, net]) => ({
              currency,
              assetsCcy: assets / 100,
              liabilitiesCcy: liabilities / 100,
              netDerivativesCcy: derivatives / 100,
              netCcy: net / 100,
            }),
          ),
        );
      }),
      { numRuns: 100 },
    );
  });
  it("keeps realistic scenarios at the limit and saved adversaries representable", () => {
    fc.assert(
      fc.property(
        fxScenarios,
        adversarialFxScenarios,
        (realistic, adversarial) => {
          expect(realistic[3]).toBe(realistic[0] <= realistic[2] * 100);
          for (const [hundredths, tenths, , within] of [
            realistic,
            adversarial,
          ]) {
            const decoded = FxScenarioNopReadFromJSON({
              scenario_code: "baseline",
              shock_pct: String(tenths / 10),
              nop_ghs: "1000000",
              nop_pct_tier1: String(hundredths / 100),
              within_aggregate_limit: within,
            });
            expect(decoded.withinAggregateLimit).toBe(within);
            assertFxDisplay(
              `${Number(decoded.nopPctTier1).toFixed(2)}%`,
              hundredths / 100,
            );
          }
        },
      ),
      { numRuns: 100 },
    );
  });
  it("retains complete alternate institution parameter snapshots", () => {
    fc.assert(
      fc.property(
        alternateFxParameterSnapshots,
        ([start, length, uplift, r2, low, high, shock, strings]) => {
          const encode = (v: number) => (strings ? String(v) : v);
          const decoded = fxRunParameters(
            run({
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
                depreciation_shocks_pct: { synthetic: encode(shock) },
              },
            }),
          );
          expect(decoded).toEqual({
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
            depreciationShocksPct: { synthetic: shock },
          });
        },
      ),
      { numRuns: 100 },
    );
  });
  it("produces ordered crisis windows and source-defined hedge bands", () => {
    fc.assert(
      fc.property(
        fxParameterSnapshots,
        ([start, length, uplift, r2, low, high, shock, strings]) => {
          const encode = (v: number) => (strings ? String(v) : v);
          const decoded = fxRunParameters(
            run({
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
                  baseline: 0,
                  synthetic: encode(shock),
                },
              },
            }),
          );
          expect(decoded?.crisis?.windowEnd).toBeGreaterThan(
            decoded?.crisis?.windowStart ?? 0,
          );
          expect(decoded?.hedgeBands).toEqual({
            r2MinPct: 80,
            offsetLowPct: 80,
            offsetHighPct: 125,
          });
          expect(decoded?.depreciationShocksPct.synthetic).toBe(shock);
        },
      ),
      { numRuns: 100 },
    );
  });
});
