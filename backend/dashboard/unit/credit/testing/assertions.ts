import { expect } from "vitest";
import { fmtCurrency } from "../../../lib/format";

/** PdPanel has 2dp, RollRateHeatmap 1dp. Fixtures use exact display quanta;
 * no tolerance is carried into counts, state labels, or missing measurements.
 */
export const creditTolerance = {
  absolute: 0,
  unit: "displayed percentage points",
} as const;

export function assertCreditPercentage(text: string, expected: number) {
  expect(text.includes("%"), `Credit: missing percentage unit in ${text}`).toBe(
    true,
  );
  expect(
    Math.abs(Number(text.replace("%", "")) - expected),
    `Credit: saved percentage ${expected} changed to ${text}`,
  ).toBeLessThanOrEqual(creditTolerance.absolute);
}
export function assertCreditFlow(
  text: string,
  exposure: number,
  loans: number,
) {
  expect(
    text,
    `Credit: saved migration exposure ${exposure} missing from ${text}`,
  ).toContain(fmtCurrency(exposure));
  expect(
    text,
    `Credit: migration count ${loans} missing from ${text}`,
  ).toContain(`${loans.toLocaleString("en-US")} loans`);
}
