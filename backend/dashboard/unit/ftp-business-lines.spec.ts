import { describe, expect, it } from "vitest";
import fc from "fast-check";
import type { FtpProductRead } from "@aequoros/risk-service-api";
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
const categories = [
  ["Corporate lending", "asset", "corporate_sme"],
  ["Retail mortgage", "asset", "retail_lending"],
  ["Treasury bills", "asset", "treasury"],
  ["Special facility", "asset", "other_assets"],
  ["Savings account", "liability", "transactional_deposits"],
  ["Wholesale borrowing", "liability", "term_wholesale"],
  ["Special facility", "liability", "other_funding"],
] as const;

function expectImpliedMargin(
  actual: number | null | undefined,
  balanceCents: number,
  contributionsCents: number[],
) {
  const contributionCents = contributionsCents.reduce(
    (sum, value) => sum + value,
    0,
  );
  const expected = (contributionCents * 100) / balanceCents;
  const magnitude =
    (contributionsCents.reduce((sum, value) => sum + Math.abs(value), 0) * 100) /
    balanceCents;
  const errorBudget =
    4 * (contributionsCents.length + 4) * Number.EPSILON * Math.max(1, magnitude);
  expect(Math.abs((actual ?? NaN) - expected)).toBeLessThanOrEqual(errorBudget);
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
    const row = fc.record({
      group: fc.integer({ min: 0, max: categories.length - 1 }),
      balance: fc.integer({ min: 1, max: 10_000_000 }),
      contribution: fc.integer({ min: -1_000_000, max: 1_000_000 }),
      flagged: fc.boolean(),
      uppercase: fc.boolean(),
    });
    fc.assert(
      fc.property(
        fc.array(row, { minLength: 1, maxLength: 25 }),
        (generated) => {
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
            const contribution = members.reduce(
              (sum, r) => sum + r.contribution,
              0,
            );
            expect(line?.balanceGhs).toBeCloseTo(balance / 100, 8);
            expect(line?.contributionGhs).toBeCloseTo(contribution / 100, 8);
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
          expect(
            lines.reduce((sum, line) => sum + line.products.length, 0),
          ).toBe(products.length);
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
        },
      ),
      { numRuns: 100 },
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
