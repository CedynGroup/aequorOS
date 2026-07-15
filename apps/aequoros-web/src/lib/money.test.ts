import { describe, expect, it } from "vitest";

import { formatPercent } from "./money";

describe("formatPercent", () => {
  it("uses the locale percent decimal separator", () => {
    expect(formatPercent("0.0833", 2, "de-DE")).toBe("8,33\u00a0%");
  });
});
