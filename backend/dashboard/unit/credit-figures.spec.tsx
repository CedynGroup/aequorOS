// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import fc from "fast-check";
import type {
  CreditPdRead,
  MigrationCellRead,
  PdEstimateRead,
  RollRateCellRead,
} from "@aequoros/risk-service-api";
import PdPanel from "../components/credit/PdPanel";
import MigrationMatrix from "../components/credit/MigrationMatrix";
import RollRateHeatmap from "../components/credit/RollRateHeatmap";
import { setActiveJurisdiction } from "../lib/format";

beforeEach(() =>
  setActiveJurisdiction({ currencyCode: "EUR", locale: "en-US" }),
);
afterEach(() => {
  cleanup();
  setActiveJurisdiction({});
});
const advisory =
  "Fixture advisory: these estimates are analysis, not approved register values.";
function pd(overrides: Partial<CreditPdRead> = {}): CreditPdRead {
  return {
    available: true,
    advisoryStatement: advisory,
    asOf: "2026-09-30",
    windowStart: "2026-01-31",
    monthPairsObserved: 8,
    matchedLoanMonths: 1200,
    exitedLoanMonths: 50,
    overall: [],
    segments: [],
    eclSuggestions: [],
    ...overrides,
  };
}
function estimate(overrides: Partial<PdEstimateRead> = {}): PdEstimateRead {
  return {
    grade: "Standard",
    loanMonths: 1200,
    defaultsObserved: 3,
    monthlyHazardPct: "0.25",
    pd12mPct: "2.96",
    ...overrides,
  };
}
function rowCells(table: HTMLElement, label: string | RegExp) {
  const row = within(table)
    .getAllByRole("row")
    .find((candidate) =>
      within(candidate).queryByRole("cell", { name: label }),
    );
  if (!row) throw new Error(`No visible row for ${label}`);
  return within(row).getAllByRole("cell");
}

describe("credit stored analytical figures", () => {
  it("discloses unavailable analysis and the server reason without substituting a zero estimate", () => {
    render(
      <PdPanel
        pd={pd({
          available: false,
          reason: "Insufficient matched observations for estimation.",
        })}
      />,
    );
    expect(
      screen.getByText("Insufficient matched observations for estimation."),
    ).toBeVisible();
    expect(screen.getByText(advisory)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
  });

  it("preserves independently stored hazard, PD and observation counts rather than deriving another estimate", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 10_000 }),
        fc.integer({ min: 0, max: 10_000 }),
        fc.integer({ min: 100, max: 100_000 }),
        fc.integer({ min: 0, max: 100 }),
        (hazard, probability, loanMonths, defaults) => {
          render(
            <PdPanel
              pd={pd({
                overall: [
                  estimate({
                    monthlyHazardPct: String(hazard / 100),
                    pd12mPct: String(probability / 100),
                    loanMonths,
                    defaultsObserved: defaults,
                  }),
                ],
              })}
            />,
          );
          try {
            const cells = rowCells(screen.getByRole("table"), "Standard");
            expect(
              Number((cells[1].textContent ?? "").replaceAll(",", "")),
            ).toBe(loanMonths);
            expect(
              Number((cells[2].textContent ?? "").replaceAll(",", "")),
            ).toBe(defaults);
            expect(Number((cells[3].textContent ?? "").replace("%", ""))).toBe(
              hazard / 100,
            );
            expect(Number((cells[4].textContent ?? "").replace("%", ""))).toBe(
              probability / 100,
            );
            expect(screen.getByText(advisory)).toBeVisible();
          } finally {
            cleanup();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("keeps missing estimates, actual zero, segment evidence and display-only ECL suggestions distinct", () => {
    render(
      <PdPanel
        pd={pd({
          overall: [
            estimate({
              monthlyHazardPct: null,
              pd12mPct: null,
              notEstimableReason: "Too few observations.",
            }),
          ],
          segments: [
            estimate({
              grade: "Standard",
              segment: "Fixture retail",
              monthlyHazardPct: "0",
              pd12mPct: "0",
              notEstimableReason: "Too few observations.",
            }),
          ],
          eclSuggestions: [
            {
              segment: "Fixture retail",
              stage: 1,
              suggestedPdPct: "3.125",
              basis: "Saved migration evidence.",
            },
          ],
        })}
      />,
    );
    const tables = screen.getAllByRole("table");
    expect(rowCells(tables[0], "Standard")[3]).toHaveTextContent("—");
    expect(rowCells(tables[0], "Standard")[4]).toHaveTextContent(
      "Not estimable",
    );
    expect(screen.getByText("Not estimable")).toHaveAttribute(
      "title",
      "Too few observations.",
    );
    expect(screen.getAllByText("Too few observations.")).toHaveLength(1);
    expect(rowCells(tables[0], /Fixture retail/)[4]).toHaveTextContent("0.00%");
    expect(
      screen.getByText(
        /1,200 matched performing loan-months \(50 excluded as departures\)/,
      ),
    ).toBeVisible();
    expect(
      within(tables[1]).getByRole("cell", { name: "3.13%" }),
    ).toBeVisible();
    expect(
      screen.getByText(/Display only.*never writes the register/),
    ).toBeVisible();
    expect(
      screen.getByText(
        /Until adopted there, these figures affect no calculation/,
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("link", {
        name: "Institution → Registers → ECL assumptions",
      }),
    ).toHaveAttribute("href", "/institution/registers");
  });

  it("retains an as-of-only window when no start date is available and avoids empty suggestion rows", () => {
    render(
      <PdPanel
        pd={pd({
          windowStart: undefined,
          monthPairsObserved: undefined,
          matchedLoanMonths: undefined,
          exitedLoanMonths: undefined,
          overall: undefined,
          segments: undefined,
          eclSuggestions: undefined,
        })}
      />,
    );
    expect(
      screen.getByText(
        /2026-09-30 · 0 month pairs · 0 matched performing loan-months/,
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Suggested ECL register rows"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
  });

  it("places stored migration amounts and loan counts in the correct matrix, entry and departure cells", () => {
    const states = ["performing", "performing_restructured", "npl"] as const;
    const labels = ["Performing", "Performing restructured", "Non-performing"];
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 2 }),
        fc.integer({ min: 0, max: 2 }),
        fc.integer({ min: 0, max: 999 }),
        fc.integer({ min: 0, max: 100_000 }),
        (from, to, exposure, loans) => {
          const cell: MigrationCellRead = {
            fromState: states[from],
            toState: states[to],
            exposureGhs: String(exposure),
            loanCount: loans,
          };
          render(
            <MigrationMatrix
              matrix={[cell]}
              entries={[
                { ...cell, fromState: "entry", exposureGhs: "0", loanCount: 0 },
              ]}
              exits={[
                { ...cell, toState: "exit", exposureGhs: "250", loanCount: 4 },
              ]}
            />,
          );
          try {
            const table = screen.getByRole("table");
            const origin = rowCells(table, labels[from]);
            expect(origin[to + 1]).toHaveTextContent(`EUR ${exposure}`);
            expect(origin[to + 1]).toHaveTextContent(
              `${loans.toLocaleString("en-US")} loans`,
            );
            expect(within(origin[4]).getByText("EUR 250")).toBeVisible();
            expect(within(origin[4]).getByText("4 loans")).toBeVisible();
            const arriving = rowCells(table, "New in month")[to + 1];
            expect(within(arriving).getByText("EUR 0")).toBeVisible();
            expect(within(arriving).getByText("0 loans")).toBeVisible();
            for (let other = 0; other < 3; other++)
              if (other !== to)
                expect(origin[other + 1]).toHaveTextContent("—");
            expect(screen.queryByText(/GHS/)).not.toBeInTheDocument();
          } finally {
            cleanup();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("distinguishes absent roll rates from zero and preserves rates across all bucket pairs", () => {
    const buckets = [
      "current",
      "1_29",
      "30_59",
      "60_89",
      "90_179",
      "180_359",
      "360_plus",
    ] as const;
    const labels = [
      "Current",
      "1–29",
      "30–59",
      "60–89",
      "90–179",
      "180–359",
      "360+",
    ];
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 6 }),
        fc.integer({ min: 0, max: 6 }),
        fc.integer({ min: 0, max: 1000 }),
        fc.integer({ min: 0, max: 100_000 }),
        (from, to, tenths, loans) => {
          const cell: RollRateCellRead = {
            fromBucket: buckets[from],
            toBucket: buckets[to],
            ratePct: String(tenths / 10),
            loanCount: loans,
            exposureGhs: "123",
          };
          render(<RollRateHeatmap rollRates={[cell]} />);
          try {
            const row = rowCells(screen.getByRole("table"), labels[from]);
            expect(
              Number((row[to + 1].textContent ?? "").replace("%", "")),
            ).toBe(tenths / 10);
            expect(row[to + 1]).toHaveAttribute("title", `${loans} loans`);
            for (let other = 0; other < 7; other++)
              if (other !== to) expect(row[other + 1]).toHaveTextContent("—");
          } finally {
            cleanup();
          }
        },
      ),
      {
        numRuns: 100,
        examples: [
          [0, 0, 0, 0],
          [6, 0, 1000, 1],
          [0, 6, 500, 3],
        ],
      },
    );
  });
});
