import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fc from "fast-check";
import {
  currencyCode,
  fmtCurrency,
  fmtCurrencyFull,
  fmtCurrencySigned,
  fmtNum,
  fmtPctSigned,
  fmtRatioVsThreshold,
  setActiveJurisdiction,
} from "../lib/format";
import { fmtDateUTC, isoDate } from "../lib/api/values";

// Decode the public display vocabulary so properties compare monetary values,
// rather than duplicating the formatter's choice of compact units.
function displayedMoney(display: string): { amount: number; unit: number } {
  const match = /^EUR ([\d,.-]+)([KMB]?)$/.exec(display);
  expect(match).not.toBeNull();
  const unit = { "": 1, K: 1_000, M: 1_000_000, B: 1_000_000_000 }[
    match![2] as "" | "K" | "M" | "B"
  ];
  return { amount: Number(match![1].replaceAll(",", "")) * unit, unit };
}

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

  it("bounds compact money rounding at unit boundaries and preserves order", () => {
    const cents = fc.oneof(
      fc.integer({ min: 0, max: 1_000_000_000_000 }),
      fc.constantFrom(
        99_999,
        100_000,
        99_999_999,
        100_000_000,
        99_999_999_999,
        100_000_000_000,
      ),
    );
    fc.assert(
      fc.property(
        cents,
        cents,
        fc.integer({ min: 0, max: 3 }),
        (left, right, decimals) => {
          const lower = Math.min(left, right) / 100;
          const upper = Math.max(left, right) / 100;
          const low = displayedMoney(
            fmtCurrency(lower, undefined, { decimals }),
          );
          const high = displayedMoney(
            fmtCurrency(upper, undefined, { decimals }),
          );
          expect(low.amount).toBeLessThanOrEqual(high.amount);
          for (const amount of [lower, upper, -lower, -upper]) {
            const shown = displayedMoney(
              fmtCurrency(amount, undefined, { decimals }),
            );
            const tolerance =
              shown.unit === 1 ? 0.0005 : shown.unit / (2 * 10 ** decimals);
            expect(Math.abs(shown.amount - amount)).toBeLessThanOrEqual(
              tolerance + 0.00001,
            );
            expect(fmtCurrencySigned(amount)).toBe(
              `${amount >= 0 ? "+" : "-"}${fmtCurrency(Math.abs(amount))}`,
            );
            expect(
              Math.abs(displayedMoney(fmtCurrencyFull(amount)).amount - amount),
            ).toBeLessThanOrEqual(0.5 + 0.00001);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("keeps a reporting date in UTC throughout its whole day", () => {
    const months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sept",
      "Oct",
      "Nov",
      "Dec",
    ];
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 47_481 }),
        fc.integer({ min: 0, max: 86_399_999 }),
        (day, offset) => {
          const midnight = new Date(day * 86_400_000);
          const instant = new Date(midnight.getTime() + offset);
          const expected = `${String(midnight.getUTCDate()).padStart(2, "0")} ${months[midnight.getUTCMonth()]} ${midnight.getUTCFullYear()}`;
          expect(isoDate(instant)).toBe(isoDate(midnight));
          expect(fmtDateUTC(instant)).toBe(expected);
          expect(isoDate(new Date(isoDate(instant)))).toBe(isoDate(midnight));
        },
      ),
      { numRuns: 100 },
    );
  });
});
