import { expect } from "vitest";
import type { FilingAuthority } from "../../../lib/submissions/filingAuthority";

/** Filing status, authority and receipts are exact. No floating-point/time tolerance.
 * Source: submissions/filingAuthority.ts and the generated PackageStatus contract.
 */
export const filingTolerance = {
  absolute: 0,
  unit: "workflow evidence",
} as const;

export function assertFilingAuthorityUnchanged(
  actual: FilingAuthority,
  before: FilingAuthority,
) {
  expect(
    actual,
    "Filing: changing row order or sibling authority changed selected bank actions",
  ).toEqual(before);
}

export function assertFilingReceipt(
  actual: string | null,
  expected: string | null,
) {
  expect(
    actual,
    "Filing: officer action receipt must match enabled scoped control",
  ).toBe(expected);
}
