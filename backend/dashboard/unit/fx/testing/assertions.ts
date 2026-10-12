import { expect } from "vitest";
import type { FxPositionSplit } from "../../../components/fx/params";

/** Saved cents and display hundredths round-trip exactly through params.ts and
 * ScenarioStrip. Currency identity, ordering and saved verdicts remain exact.
 */
export const fxTolerance = {
  absolute: 0,
  unit: "stored currency/display percentage units",
} as const;
export function assertFxStoredPositions(
  actual: FxPositionSplit[],
  expected: FxPositionSplit[],
) {
  expect(actual, "FX: stored currency splits changed during decoding").toEqual(
    expected,
  );
}
export function assertFxDisplay(text: string, expected: number) {
  const number = Number(
    text
      .replace("% of Tier 1", "")
      .replace("Depreciation ", "")
      .replace("%", ""),
  );
  expect(
    Math.abs(number - expected),
    `FX: stored measurement ${expected} changed to ${text}`,
  ).toBeLessThanOrEqual(fxTolerance.absolute);
}
