import { describe, expect, it } from "vitest";

import { formatPercent } from "./money";

describe("formatPercent", () => {
  it("uses the locale percent decimal separator", () => {
    expect(formatPercent("0.0833", 2, "de-DE")).toBe("8,33\u00a0%");
  });

  it("uses the locale numbering system for fractional digits", () => {
    expect(formatPercent("0.08335", 2, "ar-EG")).toBe("٨٫٣٤٪؜");
  });

  it("formats decimal strings without Number precision loss", () => {
    expect(formatPercent("9999999999999999.999")).toBe(
      "999,999,999,999,999,999.9%",
    );
  });
});
