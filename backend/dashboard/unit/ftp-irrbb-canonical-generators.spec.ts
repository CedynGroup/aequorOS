import { describe, expect, it } from "vitest";
import fc from "fast-check";
import {
  CanonicalPositionListReadFromJSON,
  CanonicalPositionListReadToJSON,
  IngestionBatchReadFromJSON,
  IngestionBatchReadToJSON,
  PositionSnapshotReadFromJSON,
  PositionSnapshotReadToJSON,
  RegulatoryRunReadFromJSON,
} from "@aequoros/risk-service-api";
import { aggregateBusinessLines } from "../components/ftp/businessLines";
import { ftpRunParameters } from "../components/ftp/params";
import { referenceRowTotal } from "../components/data-engine/shared";
import { normalizeStandardisedFramework } from "../lib/api/irrbbSfNormalize";
import { ftpParameters, ftpProducts } from "./ftp/testing/generators";
import {
  assertFtpProductCoherent,
  assertFtpMargin,
  assertFtpScaling,
  assertFtpStored,
  assertFtpTotal,
} from "./ftp/testing/assertions";
import { irrbbSnapshots } from "./irrbb/testing/generators";
import { assertIrrbbStored } from "./irrbb/testing/assertions";
import { canonicalGraphs } from "./data-engine/testing/generators";
import { assertCanonicalGraph } from "./data-engine/testing/assertions";

describe("shared FTP, IRRBB and canonical generators", () => {
  it("produces coherent FTP products and preserves grouping under scaling", () => {
    fc.assert(
      fc.property(
        ftpProducts,
        fc.integer({ min: 1, max: 10 }),
        (products, scale) => {
          products.forEach(assertFtpProductCoherent);
          expect(() =>
            assertFtpProductCoherent({
              ...products[0],
              contributionGhs: String(Number(products[0].contributionGhs) + 1),
            }),
          ).toThrow("FTP:");
          const before = aggregateBusinessLines(products);
          before.forEach((line) =>
            assertFtpTotal(
              line.balanceGhs,
              line.products.map((p) => Number(p.balanceGhs)),
            ),
          );
          const reversed = aggregateBusinessLines([...products].reverse());
          reversed.forEach((line, index) => {
            assertFtpTotal(
              line.contributionGhs,
              before[index].products.map((product) =>
                Number(product.contributionGhs),
              ),
            );
            assertFtpMargin(
              line.impliedMarginPct,
              line.balanceGhs * 100,
              line.products.map((p) => Number(p.contributionGhs) * 100),
            );
          });
          const scaled = products.map((p) => ({
            ...p,
            balanceGhs: (Number(p.balanceGhs) * scale).toFixed(4),
            contributionGhs: (Number(p.contributionGhs) * scale).toFixed(4),
          }));
          assertFtpScaling(aggregateBusinessLines(scaled), before, scale);
        },
      ),
      { numRuns: 100 },
    );
  });
  it("produces ordered policy bands near source thresholds and decodes them exactly", () => {
    fc.assert(
      fc.property(ftpParameters, (values) => {
        const [roe, margin, liquidity, funding, low, high, shift, stress] =
          values;
        expect(low).toBeLessThan(high);
        const run = RegulatoryRunReadFromJSON({
          inputs: {
            parameters: {
              thresholds: {
                ftp_target_roe_pct: roe,
                ftp_min_product_margin_pct: margin,
                ftp_liquidity_premium_max_bps: liquidity,
                ftp_funding_spread_max_bps: funding,
                nmd_core_min_pct: low,
                nmd_core_max_pct: high,
              },
              stress_overlays_bps: {
                rates_up_200: shift,
                funding_stress: stress,
              },
            },
          },
          metrics: {},
          line_items: [],
          validations: [],
          metric_results: [],
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
        });
        assertFtpStored(ftpRunParameters(run), {
          targetRoePct: roe,
          minProductMarginPct: margin,
          liquidityPremiumMaxBps: liquidity,
          fundingSpreadMaxBps: funding,
          nmdCoreMinPct: low,
          nmdCoreMaxPct: high,
          ratesUpShiftBps: shift,
          fundingStressAddBps: stress,
        });
      }),
      { numRuns: 100 },
    );
  });
  it("produces consistent IRRBB disclosure scenarios and preserves saved verdicts", () => {
    fc.assert(
      fc.property(irrbbSnapshots, (snapshot) => {
        expect(new Set(snapshot.scenarios.map((row) => row.code)).size).toBe(6);
        snapshot.scenarios.forEach((row) => {
          expect(Number(row.loss)).toBeGreaterThanOrEqual(0);
          expect(Number(row.loss)).toBeLessThanOrEqual(
            Number(snapshot.measures.allScenarios.measure),
          );
        });
        assertIrrbbStored(normalizeStandardisedFramework(snapshot), snapshot);
        expect(() =>
          assertIrrbbStored(
            { ...normalizeStandardisedFramework(snapshot), tier1: "0" },
            snapshot,
          ),
        ).toThrow("IRRBB:");
        const reordered = {
          ...snapshot,
          scenarios: [...snapshot.scenarios].reverse(),
        };
        assertIrrbbStored(normalizeStandardisedFramework(reordered), reordered);
      }),
      { numRuns: 100 },
    );
  });
  it("produces one-bank canonical graphs and preserves API identity round trips", () => {
    fc.assert(
      fc.property(canonicalGraphs, (graph) => {
        const batch = IngestionBatchReadFromJSON(graph.batch);
        const list = CanonicalPositionListReadFromJSON(graph.list);
        const snapshots = graph.snapshots.map(PositionSnapshotReadFromJSON);
        assertCanonicalGraph(batch, list, snapshots);
        expect(list.positions.map((row) => row.balance)).toEqual(
          graph.list.positions.map((row) => row.balance),
        );
        assertCanonicalGraph(
          IngestionBatchReadFromJSON(IngestionBatchReadToJSON(batch)),
          CanonicalPositionListReadFromJSON(
            CanonicalPositionListReadToJSON(list),
          ),
          snapshots.map((row) =>
            PositionSnapshotReadFromJSON(PositionSnapshotReadToJSON(row)),
          ),
        );
        expect(() =>
          assertCanonicalGraph(
            { ...batch, bankId: "BK-000000000002" },
            list,
            snapshots,
          ),
        ).toThrow("Canonical:");
        assertCanonicalGraph(
          batch,
          { ...list, positions: [...list.positions].reverse() },
          [...snapshots].reverse(),
        );
        expect(referenceRowTotal(batch)).toBe(2);
        expect(
          referenceRowTotal({
            ...batch,
            validationReport: {
              summary: {
                reference_rows: {
                  behavioral_assumptions: 1,
                  capital_structure: 1,
                },
              },
            },
          }),
        ).toBe(2);
      }),
      { numRuns: 100 },
    );
  });
});
