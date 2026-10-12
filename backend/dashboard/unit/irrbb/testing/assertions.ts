import { expect } from "vitest";
import { normalizeStandardisedFramework } from "../../../lib/api/irrbbSfNormalize";

/** Stored six-place figures are strings: comparison is exact, no float re-pricing. */
export const irrbbTolerance = {
  absolute: 0,
  unit: "stored currency / percentage strings",
} as const;
export function assertIrrbbStored(
  actual: ReturnType<typeof normalizeStandardisedFramework>,
  expected: {
    tier1: string;
    pctTier1: string;
    outlier: boolean;
    scenarios: { code: string; loss: string; net: string }[];
  },
) {
  expect(actual.tier1, "IRRBB: saved capital changed").toBe(expected.tier1);
  expect(actual.pctTier1, "IRRBB: saved outlier ratio changed").toBe(
    expected.pctTier1,
  );
  expect(actual.outlier, "IRRBB: saved verdict changed").toBe(expected.outlier);
  expect(
    actual.scenarios.map((row) => [row.code, row.loss, row.net]),
    "IRRBB: saved scenario losses changed",
  ).toEqual(expected.scenarios.map((row) => [row.code, row.loss, row.net]));
}
