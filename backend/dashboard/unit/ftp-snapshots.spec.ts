import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { RegulatoryRunReadFromJSON } from "@aequoros/risk-service-api";
import { adversarialFtpParameters } from "./ftp/testing/generators";
import { assertFtpStored } from "./ftp/testing/assertions";
import { ftpRunParameters } from "../components/ftp/params";

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
const fields = [
  ["ftp_target_roe_pct", "targetRoePct"],
  ["ftp_min_product_margin_pct", "minProductMarginPct"],
  ["ftp_liquidity_premium_max_bps", "liquidityPremiumMaxBps"],
  ["ftp_funding_spread_max_bps", "fundingSpreadMaxBps"],
  ["nmd_core_min_pct", "nmdCoreMinPct"],
  ["nmd_core_max_pct", "nmdCoreMaxPct"],
  ["rates_up_200", "ratesUpShiftBps"],
  ["funding_stress", "fundingStressAddBps"],
] as const;
const absent = Object.fromEntries(fields.map(([, key]) => [key, null]));

describe("stored FTP pricing assumptions", () => {
  it("preserves every saved threshold and overlay independently across decimal strings and numbers", () => {
    fc.assert(
      fc.property(
        adversarialFtpParameters,
        fc.boolean(),
        (hundredths, strings) => {
          const values = hundredths.map((v) => v / 100);
          const encode = (v: number) => (strings ? v.toFixed(2) : v);
          const thresholds = Object.fromEntries(
            fields.slice(0, 6).map(([key], i) => [key, encode(values[i])]),
          );
          const stress_overlays_bps = Object.fromEntries(
            fields.slice(6).map(([key], i) => [key, encode(values[i + 6])]),
          );
          assertFtpStored(
            ftpRunParameters(
              storedRun({ parameters: { thresholds, stress_overlays_bps } }),
            ),
            Object.fromEntries(fields.map(([, key], i) => [key, values[i]])),
          );
        },
      ),
      { numRuns: 100 },
    );
  });

  it("marks each unavailable assumption null without fabricating a default or discarding its neighbors", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 7 }),
        fc.constantFrom(
          null,
          undefined,
          false,
          {},
          [],
          "NaN",
          "Infinity",
          "not configured",
        ),
        (index, invalid) => {
          const thresholds = Object.fromEntries(
            fields.slice(0, 6).map(([key]) => [key, "0"]),
          );
          const stress_overlays_bps = Object.fromEntries(
            fields.slice(6).map(([key]) => [key, "0"]),
          );
          const [input, output] = fields[index];
          const group: Record<string, unknown> =
            index < 6 ? thresholds : stress_overlays_bps;
          group[input] = invalid;
          expect(
            ftpRunParameters(
              storedRun({ parameters: { thresholds, stress_overlays_bps } }),
            ),
          ).toEqual({
            ...Object.fromEntries(fields.map(([, key]) => [key, 0])),
            [output]: null,
          });
        },
      ),
      { numRuns: 100 },
    );
  });

  it("has no assumption snapshot before a valid stored run, and keeps absent groups unconfigured", () => {
    expect(ftpRunParameters(undefined)).toBeNull();
    for (const inputs of [undefined, null, [], {}, "missing"])
      expect(ftpRunParameters(storedRun(inputs))).toBeNull();
    for (const parameters of [undefined, null, [], "missing"])
      expect(ftpRunParameters(storedRun({ parameters }))).toBeNull();
    expect(ftpRunParameters(storedRun({ parameters: {} }))).toEqual(absent);
    expect(
      ftpRunParameters(
        storedRun({ parameters: { thresholds: [], stress_overlays_bps: [] } }),
      ),
    ).toEqual(absent);
  });
});
