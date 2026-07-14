import type {
  CalculationRunListRead,
  FinancialDataWorkspaceRead,
  FindingRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import type { ConsoleTab } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { mockCaseHealth } from "../demo-data/demo-data";
import { CaseHealthHeader } from "./case-health-header";

const tenant: TenantHeaders = { orgId: "org-1", userId: "user-1" };
const caseId = "case-1";
const now = new Date("2026-07-14T12:00:00Z");

function financial(
  overrides: Partial<FinancialDataWorkspaceRead> = {},
): FinancialDataWorkspaceRead {
  return {
    organizationId: tenant.orgId,
    caseId,
    institutions: [],
    accounts: [],
    reportingPeriods: [],
    balances: [],
    cashFlows: [],
    obligations: [],
    covenants: [],
    sourceRows: [],
    recordSourceLinks: [],
    manualEdits: [],
    validationIssues: [],
    validationSummary: { error: 0, warning: 0, info: 0, total: 0 },
    ...overrides,
  };
}

function scenario(
  overrides: Partial<ScenarioWorkspaceRead["readiness"]> = {},
): ScenarioWorkspaceRead {
  return {
    caseId,
    scenarios: [],
    readiness: {
      caseId,
      ready: false,
      scenarioCount: 0,
      completeScenarioCount: 0,
      incompleteScenarioIds: [],
      ...overrides,
    },
  };
}

function runs(
  status?: "failed" | "queued" | "running" | "succeeded",
): CalculationRunListRead {
  const items = status
    ? [
        {
          id: "run-1",
          scenarioId: "scenario-1",
          rerunOfRunId: null,
          status,
          engineVersion: "balance-sheet-v1.0.0",
          inputHash: "a".repeat(64),
          forecastPeriods: 3,
          asOfDate: now,
          startedAt: now,
          completedAt: status === "queued" || status === "running" ? null : now,
          error: null,
          createdAt: now,
        },
      ]
    : [];
  return {
    caseId,
    runs: items,
    latestSuccessfulRunId: status === "succeeded" ? "run-1" : null,
    latestSuccessfulRunsByScenario: status === "succeeded" ? items : [],
    total: items.length ? 7 : 0,
    limit: 25,
    offset: 0,
    hasMore: false,
  };
}

function finding(
  severity: string,
  id: string,
  status: FindingRead["status"] = "open",
): FindingRead {
  return {
    id,
    organizationId: tenant.orgId,
    caseId,
    assessmentId: null,
    runId: null,
    riskType: "covenant",
    title: `${severity} finding`,
    summary: "Summary",
    severity,
    status,
    source: "manual",
    ruleId: null,
    ruleVersion: null,
    rationale: null,
    likelihood: null,
    impact: null,
    confidence: null,
    scoreImpact: null,
    dispositionReason: null,
    details: {},
    createdAt: now,
    updatedAt: now,
  };
}

function installQueries({
  workspace = financial(),
  scenarioWorkspace = scenario(),
  runList = runs(),
  findingList = [],
}: {
  workspace?: FinancialDataWorkspaceRead;
  scenarioWorkspace?: ScenarioWorkspaceRead;
  runList?: CalculationRunListRead;
  findingList?: FindingRead[];
} = {}) {
  vi.spyOn(riskApi, "financialWorkspace").mockResolvedValue(workspace);
  vi.spyOn(riskApi, "scenarios").mockResolvedValue(scenarioWorkspace);
  vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList);
  vi.spyOn(riskApi, "findings").mockResolvedValue(findingList);
}

function renderHeader(
  decision:
    | "approved"
    | "escalated"
    | "needs_more_info"
    | "rejected"
    | null
    | undefined,
  onNavigate = vi.fn<(tab: ConsoleTab) => void>(),
) {
  return {
    onNavigate,
    ...renderWithQuery(
      <CaseHealthHeader
        tenant={tenant}
        caseId={caseId}
        decision={decision}
        onNavigate={onNavigate}
      />,
    ),
  };
}

describe("CaseHealthHeader", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading skeletons instead of optimistic health states", () => {
    const pending = new Promise<never>(() => undefined);
    vi.spyOn(riskApi, "financialWorkspace").mockReturnValue(pending);
    vi.spyOn(riskApi, "scenarios").mockReturnValue(pending);
    vi.spyOn(riskApi, "calculationRuns").mockReturnValue(pending);
    vi.spyOn(riskApi, "findings").mockReturnValue(pending);

    renderHeader(undefined);

    expect(screen.getAllByLabelText("Loading status")).toHaveLength(6);
    expect(screen.queryByText("Validated")).not.toBeInTheDocument();
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
  });

  it("renders explicit empty states", async () => {
    installQueries();
    renderHeader(null);

    expect(await screen.findByText("No financial data")).toBeInTheDocument();
    expect(screen.getByText("No scenarios")).toBeInTheDocument();
    expect(screen.getByText("No forecasts")).toBeInTheDocument();
    expect(screen.getByText("No findings")).toBeInTheDocument();
    expect(screen.getByText("No covenants")).toBeInTheDocument();
    expect(screen.getByText("No decision")).toBeInTheDocument();
  });

  it("renders adverse validation, readiness, run, severity, covenant, and decision states", async () => {
    installQueries({
      workspace: financial({
        institutions: [
          {} as FinancialDataWorkspaceRead["institutions"][number],
        ],
        validationSummary: { error: 2, warning: 1, info: 0, total: 3 },
        covenants: [
          {
            complianceStatus: "non_compliant",
          } as FinancialDataWorkspaceRead["covenants"][number],
        ],
      }),
      scenarioWorkspace: scenario({
        scenarioCount: 3,
        completeScenarioCount: 1,
        incompleteScenarioIds: ["scenario-2", "scenario-3"],
      }),
      runList: runs("failed"),
      findingList: [
        finding("critical", "finding-1"),
        finding("high", "finding-2"),
        finding("high", "finding-3"),
        finding("medium", "finding-4"),
      ],
    });
    renderHeader("needs_more_info");

    expect(
      await screen.findByRole("button", { name: /Validation.*2 errors/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Scenarios.*1\/3 ready/i }),
    ).toBeInTheDocument();
    expect(screen.getByTitle("Forecast #7 · Failed")).toBeInTheDocument();
    expect(
      screen.getByLabelText("1 critical, 2 high, 1 medium, 0 low"),
    ).toBeInTheDocument();
    expect(screen.getByText("Non-compliant")).toBeInTheDocument();
    expect(screen.getByText("Needs More Info")).toBeInTheDocument();
  });

  it("renders confirmed healthy states only when returned by the APIs", async () => {
    installQueries({
      workspace: financial({
        institutions: [
          {} as FinancialDataWorkspaceRead["institutions"][number],
        ],
        covenants: [
          {
            complianceStatus: "compliant",
          } as FinancialDataWorkspaceRead["covenants"][number],
        ],
      }),
      scenarioWorkspace: scenario({
        ready: true,
        scenarioCount: 2,
        completeScenarioCount: 2,
      }),
      runList: runs("succeeded"),
    });
    renderHeader("approved");

    expect(await screen.findByText("Validated")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByTitle("Forecast #7 · Succeeded")).toBeInTheDocument();
    expect(screen.getByText("Compliant")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });

  it("counts only active findings and separates historical findings", async () => {
    installQueries({
      findingList: [
        finding("critical", "finding-1", "resolved"),
        finding("high", "finding-2", "open"),
        finding("medium", "finding-3", "needs_review"),
        finding("high", "finding-4", "dismissed"),
        finding("low", "finding-5", "acknowledged"),
        finding("critical", "finding-6", "accepted"),
        finding("critical", "finding-7", "superseded"),
      ],
    });
    renderHeader(null);

    expect(
      await screen.findByLabelText(
        "0 critical, 1 high, 1 medium, 0 low, 5 historical",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("+5 historical")).toHaveClass(
      "text-[rgb(var(--muted-foreground))]",
    );
  });

  it("shows historical traceability when no findings are active", async () => {
    installQueries({
      findingList: [
        finding("critical", "finding-1", "resolved"),
        finding("high", "finding-2", "dismissed"),
      ],
    });
    renderHeader(null);

    expect(
      await screen.findByLabelText("No active findings, 2 historical"),
    ).toBeInTheDocument();
    expect(screen.getByText("+2 historical")).toBeInTheDocument();
    expect(screen.queryByText("C0")).not.toBeInTheDocument();
    expect(screen.queryByText("H0")).not.toBeInTheDocument();
  });

  it.each(["queued", "running"] as const)(
    "renders warning and unknown aggregates for a %s forecast",
    async (status) => {
      installQueries({
        workspace: financial({
          institutions: [
            {} as FinancialDataWorkspaceRead["institutions"][number],
          ],
          validationSummary: { error: 0, warning: 2, info: 0, total: 2 },
          covenants: [
            {
              complianceStatus: "unknown",
            } as FinancialDataWorkspaceRead["covenants"][number],
          ],
        }),
        runList: runs(status),
      });
      renderHeader("rejected");

      expect(
        await screen.findByRole("button", {
          name: /Validation.*2 warnings/i,
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByTitle(
          `Forecast #7 · ${status === "queued" ? "Queued" : "Running"}`,
        ),
      ).toBeInTheDocument();
      expect(screen.getByText("Unknown")).toBeInTheDocument();
      expect(screen.getByText("Rejected")).toBeInTheDocument();
    },
  );

  it("polls an active latest forecast until it reaches a terminal state", async () => {
    installQueries({ runList: runs("running") });
    vi.mocked(riskApi.calculationRuns)
      .mockResolvedValueOnce(runs("running"))
      .mockResolvedValue(runs("succeeded"));

    renderHeader(null);

    expect(
      await screen.findByTitle("Forecast #7 · Running"),
    ).toBeInTheDocument();
    expect(
      await screen.findByTitle("Forecast #7 · Succeeded", undefined, {
        timeout: 2500,
      }),
    ).toBeInTheDocument();
    expect(riskApi.calculationRuns).toHaveBeenCalledTimes(2);
  });

  it("renders complete demo health without making live requests", () => {
    const financialWorkspace = vi.spyOn(riskApi, "financialWorkspace");
    const scenarios = vi.spyOn(riskApi, "scenarios");
    const calculationRuns = vi.spyOn(riskApi, "calculationRuns");
    const findings = vi.spyOn(riskApi, "findings");

    renderWithQuery(
      <CaseHealthHeader
        tenant={tenant}
        caseId={caseId}
        decision="needs_more_info"
        demoData={mockCaseHealth(tenant.orgId, caseId)}
        onNavigate={vi.fn<(tab: ConsoleTab) => void>()}
      />,
    );

    expect(screen.getByText("Validated")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByTitle("Forecast #1 · Succeeded")).toBeInTheDocument();
    expect(screen.getByText("Non-compliant")).toBeInTheDocument();
    expect(screen.getByText("Needs More Info")).toBeInTheDocument();
    expect(financialWorkspace).not.toHaveBeenCalled();
    expect(scenarios).not.toHaveBeenCalled();
    expect(calculationRuns).not.toHaveBeenCalled();
    expect(findings).not.toHaveBeenCalled();
  });

  it("renders unknown states when health queries fail", async () => {
    vi.spyOn(riskApi, "financialWorkspace").mockRejectedValue(
      new Error("no financial"),
    );
    vi.spyOn(riskApi, "scenarios").mockRejectedValue(new Error("no scenarios"));
    vi.spyOn(riskApi, "calculationRuns").mockRejectedValue(
      new Error("no runs"),
    );
    vi.spyOn(riskApi, "findings").mockRejectedValue(new Error("no findings"));

    renderHeader("escalated");

    expect(await screen.findAllByText("Unknown")).toHaveLength(5);
    expect(screen.getByText("Escalated")).toBeInTheDocument();
  });

  it("updates from tenant and case-scoped invalidations after mutations and runs", async () => {
    installQueries();
    const { queryClient } = renderHeader(null);
    expect(await screen.findByText("No forecasts")).toBeInTheDocument();

    vi.mocked(riskApi.financialWorkspace).mockResolvedValue(
      financial({
        institutions: [
          {} as FinancialDataWorkspaceRead["institutions"][number],
        ],
        covenants: [
          {
            complianceStatus: "non_compliant",
          } as FinancialDataWorkspaceRead["covenants"][number],
        ],
      }),
    );
    vi.mocked(riskApi.scenarios).mockResolvedValue(
      scenario({
        ready: true,
        scenarioCount: 1,
        completeScenarioCount: 1,
      }),
    );
    vi.mocked(riskApi.calculationRuns).mockResolvedValue(runs("succeeded"));
    vi.mocked(riskApi.findings).mockResolvedValue([
      finding("high", "finding-1"),
    ]);

    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: ["financial-workspace", tenant, caseId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["scenarios", tenant, caseId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["calculation-runs", tenant, caseId],
      }),
      queryClient.invalidateQueries({
        queryKey: ["findings", tenant, caseId],
      }),
    ]);

    expect(await screen.findByText("Non-compliant")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByTitle("Forecast #7 · Succeeded")).toBeInTheDocument();
    expect(
      screen.getByLabelText("0 critical, 1 high, 0 medium, 0 low"),
    ).toBeInTheDocument();
  });

  it("deep-links every health element to its owning tab", async () => {
    const user = userEvent.setup();
    installQueries();
    const { onNavigate } = renderHeader(null);

    for (const [label, tab] of [
      ["Validation", "financial"],
      ["Scenarios", "scenarios"],
      ["Latest forecast", "calculations"],
      ["Findings", "findings"],
      ["Covenants", "financial"],
      ["Decision", "decisions"],
    ] as const) {
      await user.click(screen.getByRole("button", { name: new RegExp(label) }));
      expect(onNavigate).toHaveBeenLastCalledWith(tab);
    }
    expect(onNavigate).toHaveBeenCalledTimes(6);
  });
});
