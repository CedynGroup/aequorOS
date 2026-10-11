// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import fc from "fast-check";
import type { FxScenarioNopRead } from "@aequoros/risk-service-api";
import ScenarioStrip, { fxScenarioLabel } from "../components/fx/ScenarioStrip";
import { setActiveJurisdiction } from "../lib/format";

beforeEach(() =>
  setActiveJurisdiction({ currencyCode: "EUR", locale: "en-US" }),
);
afterEach(() => {
  cleanup();
  setActiveJurisdiction({});
});

function scenario(
  overrides: Partial<FxScenarioNopRead> = {},
): FxScenarioNopRead {
  return {
    scenarioCode: "baseline",
    shockPct: "0",
    nopGhs: "0",
    nopPctTier1: "0",
    withinAggregateLimit: true,
    ...overrides,
  };
}

describe("FX scenario disclosures", () => {
  it("shows saved verdicts and signed amounts in the selected bank's currency", () => {
    render(
      <ScenarioStrip
        aggregateLimitPct={20}
        scenarios={[
          scenario({
            nopGhs: "-1500000",
            nopPctTier1: "21.25",
            withinAggregateLimit: false,
          }),
          scenario({
            scenarioCode: "cedi_crisis",
            shockPct: "25",
            nopGhs: "1250",
            nopPctTier1: "3.50",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Baseline")).toBeVisible();
    expect(screen.getByText("Currency crisis")).toBeVisible();
    expect(screen.getByText("No shock")).toBeVisible();
    expect(screen.getByText("Depreciation 25.0%")).toBeVisible();
    expect(screen.getByText("Breach")).toBeVisible();
    expect(screen.getByText("Within")).toBeVisible();
    expect(screen.getByText("NOP EUR -1.5M · limit 20%")).toBeVisible();
    expect(screen.getByText("NOP EUR 1.3K · limit 20%")).toBeVisible();
    expect(screen.queryByText(/GHS/)).not.toBeInTheDocument();
  });

  it("preserves decimal percentages and the server verdict independently of the shown limit", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: -20_000, max: 20_000 }),
        fc.integer({ min: 0, max: 1_000 }),
        fc.integer({ min: 1, max: 100 }),
        fc.boolean(),
        (hundredths, tenths, limit, within) => {
          render(
            <ScenarioStrip
              aggregateLimitPct={limit}
              scenarios={[
                scenario({
                  nopPctTier1: String(hundredths / 100),
                  shockPct: String(tenths / 10),
                  withinAggregateLimit: within,
                }),
              ]}
            />,
          );
          try {
            const figure = screen.getByText(/^-?\d+\.\d{2}%$/);
            expect(
              Number((figure.textContent ?? "").replace("% of Tier 1", "")),
            ).toBe(hundredths / 100);
            const shock = screen.getByText(
              tenths === 0 ? "No shock" : /^Depreciation /,
            );
            if (tenths !== 0)
              expect(
                Number(
                  (shock.textContent ?? "")
                    .replace("Depreciation ", "")
                    .replace("%", ""),
                ),
              ).toBe(tenths / 10);
            expect(
              screen.getByText(within ? "Within" : "Breach"),
            ).toBeVisible();
            expect(
              screen.queryByText(within ? "Breach" : "Within"),
            ).not.toBeInTheDocument();
          } finally {
            cleanup();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("explains absent scenarios and keeps unfamiliar scenario identities readable", () => {
    render(<ScenarioStrip aggregateLimitPct={20} scenarios={[]} />);
    expect(
      screen.getByText("No depreciation scenarios computed for this period."),
    ).toBeVisible();
    expect(screen.queryByText("Within")).not.toBeInTheDocument();
    expect(screen.queryByText(/NOP /)).not.toBeInTheDocument();
    expect(fxScenarioLabel("supervisory_fixture")).toBe("supervisory_fixture");
    expect(fxScenarioLabel("mild_depreciation")).toBe("Mild depreciation");
    expect(fxScenarioLabel("severe_depreciation")).toBe("Severe depreciation");
  });
});
