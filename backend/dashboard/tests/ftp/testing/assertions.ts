import { expect } from "vitest";
import type { FtpProductRead } from "@aequoros/risk-service-api";
import type { BusinessLine } from "../../../components/ftp/businessLines";

/** Source: businessLines.ts sums JS numbers; stored fields compare exactly.
 * Summation tolerance scales with the absolute input magnitude and row count;
 * no ratio rounding is carried into flags or saved policy values.
 */
export const ftpTolerance = {
  stored: 0,
  moneyQuantum: 0.0001,
  roundoffFactor: 4 * Number.EPSILON,
} as const;
export function assertFtpStored(actual: unknown, expected: unknown) {
  expect(actual, "FTP: stored pricing assumptions changed").toEqual(expected);
}
export function assertFtpTotal(actual: number | undefined, values: number[]) {
  const sum = values.reduce((total, value) => total + value, 0);
  const budget =
    ftpTolerance.roundoffFactor *
    (values.length + 4) *
    Math.max(
      1,
      values.reduce((total, value) => total + Math.abs(value), 0),
    );
  expect(
    Math.abs((actual ?? NaN) - sum),
    "FTP: grouped amounts do not reconcile",
  ).toBeLessThanOrEqual(budget);
}
export function assertFtpMargin(
  actual: number | null | undefined,
  balanceCents: number,
  contributionsCents: number[],
) {
  const contribution = contributionsCents.reduce(
    (sum, value) => sum + value,
    0,
  );
  const reconstructed = ((actual ?? NaN) * balanceCents) / 100;
  const magnitude = contributionsCents.reduce(
    (sum, value) => sum + Math.abs(value),
    0,
  );
  const budget =
    ftpTolerance.roundoffFactor *
    (contributionsCents.length + 4) *
    Math.max(balanceCents / 100, magnitude);
  expect(
    Math.abs(reconstructed - contribution),
    "FTP: implied margin does not reconcile to saved contribution",
  ).toBeLessThanOrEqual(budget);
}
export function assertFtpScaling(
  after: BusinessLine[],
  before: BusinessLine[],
  scale: number,
) {
  expect(
    after.map((row) => row.key),
    "FTP: scaling changed grouping",
  ).toEqual(before.map((row) => row.key));
  after.forEach((row, index) => {
    assertFtpTotal(row.balanceGhs, [before[index].balanceGhs * scale]);
    assertFtpTotal(
      row.contributionGhs,
      before[index].products.map(
        (product) => Number(product.contributionGhs) * scale,
      ),
    );
    if (
      row.impliedMarginPct !== null &&
      before[index].impliedMarginPct !== null
    )
      assertFtpMargin(
        row.impliedMarginPct,
        before[index].balanceGhs * scale * 100,
        before[index].products.map(
          (product) => Number(product.contributionGhs) * scale * 100,
        ),
      );
  });
}

export function assertFtpProductCoherent(product: FtpProductRead) {
  const balance = Number(product.balanceGhs);
  const margin = Number(product.netMarginPct);
  expect(
    balance,
    "FTP: realistic product balance is nonpositive",
  ).toBeGreaterThan(0);
  expect(
    product.belowMinMargin,
    "FTP: stored floor flag contradicts margin",
  ).toBe(margin < 0);
  const contribution = Number(product.contributionGhs);
  const reconstructed = (balance * margin) / 100;
  const budget =
    ftpTolerance.moneyQuantum / 2 +
    ftpTolerance.roundoffFactor * Math.max(1, Math.abs(reconstructed));
  expect(
    Math.abs(contribution - reconstructed),
    "FTP: rounded saved contribution disagrees with margin",
  ).toBeLessThanOrEqual(budget);
}
