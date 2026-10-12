import { describe, expect, it } from "vitest";
import fc from "fast-check";
import type { FtpProductRead } from "@aequoros/risk-service-api";
import {
  adversarialFtpRows,
  ftpCategories as categories,
} from "./ftp/testing/generators";
import {
  assertFtpMargin as expectImpliedMargin,
  assertFtpTotal,
  assertFtpScaling,
} from "./ftp/testing/assertions";
import { aggregateBusinessLines } from "../components/ftp/businessLines";

function product(overrides: Partial<FtpProductRead> = {}): FtpProductRead {
  return {
    product: "Corporate lending",
    category: "asset",
    balanceGhs: "100",
    contributionGhs: "5",
    belowMinMargin: false,
    capitalChargePct: "1",
    customerRatePct: "8",
    expectedCreditLossPct: "1",
    ftpRatePct: "4",
    netMarginPct: "5",
    operatingCostPct: "1",
    tenorYears: "1",
    ...overrides,
  };
}
describe("FTP business-line view aggregation", () => {
  it("groups by the disclosed name/category rule and keeps asset and funding books separate", () => {
    const rows = categories.map(([name, category]) =>
      product({ product: name, category }),
    );
    expect(
      aggregateBusinessLines([...rows].reverse()).map((line) => [
        line.key,
        line.side,
        line.products,
      ]),
    ).toEqual(
      categories.map(([, category, key], index) => [
        key,
        category,
        [rows[index]],
      ]),
    );
    expect(
      aggregateBusinessLines([
        product({ product: "Interbank loan" }),
        product({ product: "Interbank funding", category: "liability" }),
      ]).map((line) => line.key),
    ).toEqual(["treasury", "term_wholesale"]);
  });

  it("conserves independently generated balances, contributions and server flags for every grouping", () => {
    fc.assert(
      fc.property(adversarialFtpRows, (generated) => {
        const products = generated.map((r) => {
          const [name, category] = categories[r.group];
          return product({
            product: r.uppercase ? name.toUpperCase() : name,
            category,
            balanceGhs: (r.balance / 100).toFixed(2),
            contributionGhs: (r.contribution / 100).toFixed(2),
            belowMinMargin: r.flagged,
            netMarginPct: "999",
          });
        });
        const lines = aggregateBusinessLines(products);
        for (let group = 0; group < categories.length; group++) {
          const members = generated.filter((r) => r.group === group);
          const line = lines.find((r) => r.key === categories[group][2]);
          if (!members.length) {
            expect(line).toBeUndefined();
            continue;
          }
          expect(line).toBeDefined();
          const balance = members.reduce((sum, r) => sum + r.balance, 0);
          assertFtpTotal(
            line?.balanceGhs,
            members.map((r) => r.balance / 100),
          );
          assertFtpTotal(
            line?.contributionGhs,
            members.map((r) => r.contribution / 100),
          );
          expectImpliedMargin(
            line?.impliedMarginPct,
            balance,
            members.map((r) => r.contribution),
          );
          expect(line?.belowFloorCount).toBe(
            members.filter((r) => r.flagged).length,
          );
          expect(line?.products).toHaveLength(members.length);
        }
        expect(lines.reduce((sum, line) => sum + line.products.length, 0)).toBe(
          products.length,
        );
        const reversed = aggregateBusinessLines([...products].reverse());
        expect(reversed.map((line) => line.key)).toEqual(
          lines.map((line) => line.key),
        );
        lines.forEach((line, index) => {
          const members = generated.filter(
            (r) => categories[r.group][2] === line.key,
          );
          expectImpliedMargin(
            reversed[index].impliedMarginPct,
            members.reduce((sum, r) => sum + r.balance, 0),
            members.map((r) => r.contribution),
          );
          expect(reversed[index].balanceGhs).toBeCloseTo(line.balanceGhs, 8);
          expect(reversed[index].contributionGhs).toBeCloseTo(
            line.contributionGhs,
            8,
          );
          expect(reversed[index].belowFloorCount).toBe(line.belowFloorCount);
        });
      }),
      { numRuns: 100 },
    );
  });

  it("scales cancelling contributions within input-magnitude roundoff", () => {
    // Minimized counterexample from the realistic generator: scale 3, large
    // opposing contributions with a small net. Budget gross inputs, not net.
    const products = [
      product({ balanceGhs: "7229400.0000", contributionGhs: "-112522.6469" }),
      product({ balanceGhs: "2804985.0000", contributionGhs: "116978.3090" }),
    ];
    const scaled = products.map((row) => ({
      ...row,
      balanceGhs: (Number(row.balanceGhs) * 3).toFixed(4),
      contributionGhs: (Number(row.contributionGhs) * 3).toFixed(4),
    }));
    assertFtpScaling(
      aggregateBusinessLines(scaled),
      aggregateBusinessLines(products),
      3,
    );
  });

  it("accepts large-margin roundoff while rejecting materially incorrect margins", () => {
    const [line] = aggregateBusinessLines([
      product({ balanceGhs: "0.01", contributionGhs: "9999.98" }),
    ]);
    expectImpliedMargin(line.impliedMarginPct, 1, [999_998]);
    for (const incorrect of [99_999_800 - 0.01, 99_999_800 + 0.01, 0]) {
      expect(() => expectImpliedMargin(incorrect, 1, [999_998])).toThrow();
    }
  });

  it("shows no computable margin without positive grouped balance, while preserving zero contribution", () => {
    expect(aggregateBusinessLines([])).toEqual([]);
    fc.assert(
      fc.property(
        fc.integer({ min: -10_000, max: 0 }),
        fc.integer({ min: -10_000, max: 10_000 }),
        (balance, contribution) => {
          const [line] = aggregateBusinessLines([
            product({
              balanceGhs: String(balance),
              contributionGhs: String(contribution),
            }),
          ]);
          expect(line.impliedMarginPct).toBeNull();
          expect(line.balanceGhs).toBe(balance);
          expect(line.contributionGhs).toBe(contribution);
        },
      ),
      { numRuns: 100 },
    );
    expect(
      aggregateBusinessLines([product({ contributionGhs: "0" })])[0]
        .impliedMarginPct,
    ).toBe(0);
    expect(
      aggregateBusinessLines([
        product({ balanceGhs: "100" }),
        product({ balanceGhs: "-100" }),
      ])[0].impliedMarginPct,
    ).toBeNull();
  });
});
