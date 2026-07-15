import { describe, expect, it } from "vitest";

import {
  formatMoneyCompact,
  formatPct,
  formatPp,
  runStatusTone,
  severityTone,
  statusTone,
} from "./format";

describe("alm format helpers", () => {
  it("maps API ratio statuses to badge tones", () => {
    expect(statusTone("green")).toBe("success");
    expect(statusTone("amber")).toBe("warning");
    expect(statusTone("red")).toBe("danger");
    expect(statusTone("na")).toBe("neutral");
  });

  it("maps validation severities and run statuses to tones", () => {
    expect(severityTone("error")).toBe("danger");
    expect(severityTone("warning")).toBe("warning");
    expect(severityTone("info")).toBe("info");
    expect(runStatusTone("succeeded")).toBe("success");
    expect(runStatusTone("failed")).toBe("danger");
    expect(runStatusTone("running")).toBe("warning");
  });

  it("formats compact GHS money with full-precision fallbacks", () => {
    expect(formatMoneyCompact("2400000000", "GHS")).toBe("GHS 2.40B");
    expect(formatMoneyCompact("735000000", "GHS")).toBe("GHS 735.0M");
    expect(formatMoneyCompact("12500", "GHS")).toBe("GHS 12.50K");
    expect(formatMoneyCompact("512.75", "GHS")).toBe("GHS 512.75");
    expect(formatMoneyCompact(null, "GHS")).toBe("n/a");
  });

  it("formats percentages and pp differences", () => {
    expect(formatPct("142.53")).toBe("142.5%");
    expect(formatPct(null)).toBe("n/a");
    expect(formatPp(4.25)).toBe("+4.3 pp");
    expect(formatPp(-1.2)).toBe("-1.2 pp");
  });
});
