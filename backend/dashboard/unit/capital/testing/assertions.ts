import { expect } from "vitest";

/** CapitalPlanProjection formats to two decimals; generated stored values are
 * hundredths. Ratio display comparisons therefore require exact round trips.
 */
export const capitalTolerance = {
  absolute: 0,
  unit: "displayed percentage points",
} as const;
export function assertCapitalDisplay(
  text: string,
  hundredths: number,
  point: boolean,
) {
  const unit = point ? " pp" : "%";
  expect(text.endsWith(unit), `Capital: missing ${unit} unit in ${text}`).toBe(
    true,
  );
  const value = Number(text.replace("−", "-").replace(unit, ""));
  expect(
    Math.abs(value - hundredths / 100),
    `Capital: stored measurement ${hundredths / 100} changed to ${text}`,
  ).toBeLessThanOrEqual(capitalTolerance.absolute);
}
