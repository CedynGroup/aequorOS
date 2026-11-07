// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import EnterpriseStressWorkbench from "@/components/stress/EnterpriseStressWorkbench";
import type { EnterpriseStressRead } from "@/components/stress/types";

type SelectedRun = Pick<EnterpriseStressRead, "run_id">;

const { oldRun, returnedRun, reopen } = vi.hoisted(() => ({
  reopen: vi.fn(),
  oldRun: {
    run_id: "run-before-redirect",
    summary: { car_erosion_pp: 1, binding_minima: [] },
    outcome: { coupling: undefined },
  },
  returnedRun: {
    run_id: "run-authorized-for-signoff",
    summary: { car_erosion_pp: 0, binding_minima: [] },
    outcome: { coupling: undefined },
  },
}));
const otherRun = { ...returnedRun, run_id: "run-selected-after-return" };

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/lib/api/useQueryScope", () => ({
  useQueryAuthorityScope: () => ({
    tenantId: "org-test",
    authorityId: "user-test",
  }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "bank-test", reporting_currency: "GHS" },
    period: { id: "period-test" },
    periods: [],
    periodsStatus: "success",
    isLoading: false,
    moduleScope: {
      institutionClass: "bank",
      fxRun: true,
      ftpRun: true,
      irrbbRun: true,
      capitalAggregatedView: true,
      liquidityConfidentialView: true,
    },
  }),
}));
vi.mock("@/components/stress/useStressAuthority", () => ({
  useStressAuthority: () => ({ isLoading: false, can: () => true }),
  stressPermissionReason: () => "Stress permission required",
}));
vi.mock("@/components/stress/hooks", () => ({
  useMacroScenarios: () => ({ data: { scenarios: [] } }),
  useMacroScenario: () => ({ isPending: false, error: null }),
  useManagementActionPlans: () => ({ data: { plans: [] } }),
  useEnterpriseStressRegistry: () => ({ data: [oldRun] }),
  useRunEnterpriseStress: () => ({ isPending: false }),
  useReopenEnterpriseStressRun: () => ({ mutateAsync: reopen }),
}));
vi.mock("@/components/basel/sdiHooks", () => ({
  useSdiCapitalSummary: () => ({ data: undefined }),
  useSdiLiquidityPosition: () => ({ data: undefined }),
}));
vi.mock("@/components/stress/SignoffPanel", () => ({
  default: ({ run }: { run: SelectedRun }) => (
    <div data-testid="selected-signoff-run">{run.run_id}</div>
  ),
}));

vi.mock("@/components/stress/ScenarioLibrary", () => ({ default: () => null }));
vi.mock("@/components/stress/ScenarioBuilder", () => ({ default: () => null }));
vi.mock("@/components/stress/charts/ProjectionPaths", () => ({
  default: () => null,
}));
vi.mock("@/components/stress/charts/DriverWaterfall", () => ({ default: () => null }));
vi.mock("@/components/stress/ScenarioComparison", () => ({ default: () => null }));
vi.mock("@/components/stress/AppendixIITables", () => ({ default: () => null }));
vi.mock("@/components/stress/ManagementActionsPanel", () => ({ default: () => null }));
vi.mock("@/components/stress/RunRegistry", () => ({
  default: ({ onOpen }: { onOpen: (run: SelectedRun) => void }) => (
    <button type="button" onClick={() => onOpen(otherRun)}>
      Choose another run
    </button>
  ),
}));

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  reopen.mockResolvedValue(returnedRun);
  window.history.replaceState(
    {},
    "",
    "/irr/enterprise-stress?tab=governance&runId=run-authorized-for-signoff&stepUp=ready",
  );
});

it("shows a failed restore without signing a fallback run", async () => {
  reopen.mockRejectedValue(new Error("synthetic reopen failure"));
  render(<EnterpriseStressWorkbench />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The selected stress run could not be reopened.",
  );
  expect(screen.queryByTestId("selected-signoff-run")).toBeNull();
  expect(reopen).toHaveBeenCalledTimes(1);
});

it("reopens the authorized run and restores governance after SSO", async () => {
  render(<EnterpriseStressWorkbench />);

  expect(reopen).toHaveBeenCalledWith("run-authorized-for-signoff");
  expect(await screen.findByTestId("selected-signoff-run")).toHaveTextContent(
    "run-authorized-for-signoff",
  );
  expect(
    screen.getByRole("button", { name: "Governance & sign-off" }),
  ).toHaveClass("border-action", "text-navy");
  await waitFor(() => expect(reopen).toHaveBeenCalledTimes(1));
  expect(window.location.search).toBe("?stepUp=ready");

  fireEvent.click(screen.getByRole("button", { name: "Run registry" }));
  fireEvent.click(screen.getByRole("button", { name: "Choose another run" }));
  expect(await screen.findByText("CAR — base vs stress")).toBeVisible();
  expect(reopen).toHaveBeenCalledTimes(1);
});
