// @vitest-environment jsdom
/**
 * An SDI's classified book is Credit's figure, not Capital's.
 *
 * `GET /banks/{id}/loan-classification` became CREDIT/`aggregated` in the #182
 * remainder (`docs/credit_enforcement_rollout.md` §Phase 5). The s.29 capital
 * screen still shows the panel, so it must render the requirement instead of
 * asking for a figure this reader may not read — and it must not fetch at all,
 * because a refetch on re-render would 403 behind an otherwise calm panel.
 *
 * `useSdiLoanClassification` is the boundary here, so it is the only mock: the
 * component's own behavior under each capability shape is what is asserted.
 */
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { LoanGradeBucket } from "../components/basel/sdiHooks";

const scope = vi.hoisted(() => ({
  creditAggregatedView: false as boolean | undefined,
  creditRestrictedView: false as boolean | undefined,
}));

const classificationCalls = vi.hoisted(() => [] as (string | undefined)[]);

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    moduleScope: {
      capitalAggregatedView: true,
      capitalRestrictedView: true,
      creditAggregatedView: scope.creditAggregatedView,
      creditRestrictedView: scope.creditRestrictedView,
    },
  }),
}));

const buckets: LoanGradeBucket[] = [
  {
    grade: "standard",
    count: 2,
    exposure_ghs: "1000",
    provision_required_ghs: "10",
    non_performing: false,
  },
  {
    grade: "substandard",
    count: 1,
    exposure_ghs: "500",
    provision_required_ghs: "125",
    non_performing: true,
  },
];

vi.mock("../components/basel/sdiHooks", () => ({
  useSdiCapitalSummary: () => ({
    isLoading: false,
    error: null,
    refetch: () => {},
    data: {
      car_pct: "65.5",
      car_min_pct: "10",
      status: "green",
      net_own_funds_ghs: "131000",
      total_rwa_ghs: "200000",
      pending_parameters: [],
      risk_classes: [],
      bands: [],
    },
  }),
  useSdiCapitalChecks: () => ({ isLoading: false, data: { checks: [] } }),
  useSdiCapitalAssurance: () => ({ data: { history: [] } }),
  useSdiLoanClassification: (bankId: string | undefined) => {
    classificationCalls.push(bankId);
    return {
      isLoading: false,
      error: null,
      refetch: () => {},
      data:
        bankId === undefined
          ? undefined
          : {
              npl_ratio: "0.0625",
              total_provision_required_ghs: "135",
              buckets,
            },
    };
  },
}));

vi.mock("../components/basel/charts/SdiCapitalReviewCharts", () => ({
  SdiCapitalControlsChart: () => null,
  SdiCarThresholdChart: () => null,
  SdiCapitalTrendChart: () => null,
  SdiRwaCompositionChart: () => null,
}));

const { default: SdiCapitalView } = await import(
  "../components/basel/SdiCapitalView"
);

afterEach(() => {
  cleanup();
  classificationCalls.length = 0;
  scope.creditAggregatedView = false;
  scope.creditRestrictedView = false;
});

function renderView() {
  render(<SdiCapitalView bankId="BK-SAMP0001" />);
}

const DENIED = /Loan classification requires Credit · Aggregated · View/;

describe("an SDI capital screen without the Credit sentence", () => {
  it("renders the requirement instead of the table and never asks for the book", () => {
    renderView();
    expect(screen.getByText(DENIED)).toBeVisible();
    expect(screen.queryByRole("columnheader", { name: "Grade" })).toBeNull();
    // Both KPIs read the requirement, and neither invents a figure.
    expect(screen.getByText("NPL ratio")).toBeVisible();
    expect(screen.getByText("NPL ratio").closest("*")).not.toBeNull();
    expect(screen.getAllByText("Requires Credit · Aggregated · View").length).toBe(
      2,
    );
    expect(screen.getByText("Provisions required").closest("*")).not.toBeNull();
    // The hook was called with no bank: no request is possible.
    expect(classificationCalls).toEqual([undefined]);
  });

  it("treats restricted Credit as a different sentence, not a superset", () => {
    scope.creditRestrictedView = true;
    renderView();
    expect(screen.getByText(DENIED)).toBeVisible();
    expect(classificationCalls).toEqual([undefined]);
  });
});

describe("an SDI capital screen holding the Credit sentence", () => {
  it("reads the book and renders the graded table", () => {
    scope.creditAggregatedView = true;
    renderView();
    expect(classificationCalls).toEqual(["BK-SAMP0001"]);
    expect(screen.queryByText(DENIED)).toBeNull();
    expect(
      screen.getByRole("columnheader", { name: "Provision required" }),
    ).toBeVisible();
    expect(screen.getByText("substandard")).toBeVisible();
    expect(screen.getByText("Non-performing loans ÷ total exposure")).toBeVisible();
  });
});
