// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import fc from "fast-check";
import ScenarioMetrics, {
  scenarioDelta,
  scenarioRisk,
  scenarioValue,
} from "../components/workbench/ScenarioMetrics";
import { currencyCode, fmtNum } from "../lib/format";

afterEach(cleanup);

it("formats money, percentage points and decimal zeros without scientific notation", () => {
  render(
    <ScenarioMetrics
      metrics={{ total_ghs: "1234567.125", lcr_pct: "98.125", reserve: "0E-8" }}
      baseline={{ total_ghs: "1000000", lcr_pct: "100", reserve: "0" }}
      statuses={{ lcr_pct: "red" }}
    />,
  );
  const table = screen.getByRole("table", { name: "Scenario metrics" });
  expect(
    within(table).getByText(`${currencyCode()} ${fmtNum(1234567.125, 2)}`),
  ).toBeVisible();
  expect(within(table).getByText("98.13%")).toBeVisible();
  expect(within(table).getByText("-1.88 pp")).toBeVisible();
  expect(within(table).getByText(/Policy breach/)).toBeVisible();
  expect(within(table).queryByText(/0E/)).not.toBeInTheDocument();
});

it("preserves absence rather than inventing a zero or delta", () => {
  fc.assert(
    fc.property(fc.constantFrom(null, undefined, ""), (missing) => {
      expect(scenarioValue("lcr_pct", missing)).toBe("Unavailable");
      expect(scenarioDelta("lcr_pct", missing, "100")).toBe("Not comparable");
      expect(scenarioDelta("lcr_pct", "100", missing)).toBe("Not comparable");
    }),
  );
});

it("keeps a percentage-point delta unchanged when both scenarios move by the same amount", () => {
  fc.assert(
    fc.property(
      fc.integer({ min: -10000, max: 10000 }),
      fc.integer({ min: -10000, max: 10000 }),
      fc.integer({ min: -1000, max: 1000 }),
      (a, b, shift) => {
        expect(scenarioDelta("nim_pct", a + shift, b + shift)).toBe(
          scenarioDelta("nim_pct", a, b),
        );
        expect(scenarioDelta("nim_pct", a, a)).toBe("0.00 pp");
      },
    ),
  );
});

it("separates successful execution from reported breaches and absent assessment", () => {
  expect(scenarioRisk({ within_single_limit: false })).toContain(
    "Risk breaches:",
  );
  expect(scenarioRisk({ within_single_limit: true })).toContain(
    "No reported breaches",
  );
  expect(scenarioRisk({ nop_ghs: "100" })).toContain(
    "Risk assessment unavailable",
  );
  expect(
    scenarioRisk({ triggers: [{ fired: true, action: "Convene ALCO" }] }),
  ).toContain("Convene ALCO");
});
