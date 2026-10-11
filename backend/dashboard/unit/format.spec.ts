import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fc from "fast-check";
import {
  currencyCode,
  fmtCurrency,
  fmtCurrencySigned,
  fmtNum,
  fmtPctSigned,
  fmtRatioVsThreshold,
  setActiveJurisdiction,
} from "../lib/format";

beforeEach(() =>
  setActiveJurisdiction({ currencyCode: "EUR", locale: "en-US" }),
);
afterEach(() => setActiveJurisdiction({}));

describe("bank display formatting", () => {
  it.each([
    [0, "EUR 0"],
    [999, "EUR 999"],
    [1_000, "EUR 1.0K"],
    [-1_500_000, "EUR -1.5M"],
    [2_400_000_000, "EUR 2.4B"],
  ])("formats %s in the active bank currency", (amount, display) => {
    expect(fmtCurrency(amount)).toBe(display);
  });

  it("supports an explicit currency and a full amount", () => {
    expect(fmtCurrency(1_234_567, "USD", { compact: false })).toBe(
      "$1,234,567",
    );
    expect(currencyCode()).toBe("EUR");
    expect(fmtCurrencySigned(-1_500)).toBe("-EUR 1.5K");
    expect(fmtRatioVsThreshold(97.5, 100)).toBe("-2.5 pts");
  });

  it("preserves cents when displaying bounded monetary amounts", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: -100_000_000, max: 100_000_000 }),
        (cents) => {
          const displayed = fmtNum(cents / 100, 2);
          expect(Number(displayed.replaceAll(",", ""))).toBe(cents / 100);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("preserves the value and sign of signed percentages", () => {
    fc.assert(
      fc.property(fc.integer({ min: -10_000, max: 10_000 }), (percent) => {
        const displayed = fmtPctSigned(percent);
        expect(Number(displayed.slice(0, -1))).toBe(percent);
        expect(displayed.startsWith(percent >= 0 ? "+" : "-")).toBe(true);
      }),
      { numRuns: 100 },
    );
  });
});
