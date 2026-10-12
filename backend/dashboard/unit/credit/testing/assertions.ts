import { expect } from "vitest";
import type { PdEstimateRead } from "@aequoros/risk-service-api";
import { fmtCurrency } from "../../../lib/format";

/** PdPanel has 2dp, RollRateHeatmap 1dp. Fixtures use exact display quanta;
 * no tolerance is carried into counts, state labels, or missing measurements.
 */
export const creditTolerance = {
  absolute: 0,
  percentageQuantum: 0.01,
  unit: "displayed percentage points",
} as const;

export function assertCreditEstimateConsistent(estimate: PdEstimateRead) {
  const { loanMonths, defaultsObserved, monthlyHazardPct, pd12mPct } = estimate;
  expect(
    loanMonths,
    "Credit: insufficient matched loan-months",
  ).toBeGreaterThanOrEqual(30);
  expect(defaultsObserved, "Credit: no observed defaults").toBeGreaterThan(0);
  expect(
    defaultsObserved,
    "Credit: defaults exceed matched loan-months",
  ).toBeLessThanOrEqual(loanMonths);
  expect(monthlyHazardPct, "Credit: missing monthly hazard").not.toBeNull();
  expect(pd12mPct, "Credit: missing annual PD").not.toBeNull();
  assertCreditPercentage(
    `${monthlyHazardPct}%`,
    Number(((defaultsObserved / loanMonths) * 100).toFixed(2)),
  );
  const annualPd = Number(pd12mPct);
  const halfQuantum = creditTolerance.percentageQuantum / 2;
  const monthlySurvival = 1 - defaultsObserved / loanMonths;
  const message = `Credit: annual PD ${pd12mPct}% is inconsistent with ${defaultsObserved} defaults over ${loanMonths} loan-months`;
  expect(monthlySurvival, message).toBeGreaterThanOrEqual(
    (1 - (annualPd + halfQuantum) / 100) ** (1 / 12),
  );
  expect(monthlySurvival, message).toBeLessThanOrEqual(
    (1 - (annualPd - halfQuantum) / 100) ** (1 / 12),
  );
}

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
