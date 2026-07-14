import type { CaseDecisionRead } from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { mockCase } from "../demo-data/demo-data";
import { CaseWorkspace } from "./case-workspace";

vi.mock("../capital/capital-tab", () => ({
  CapitalTab: ({
    mutationDisabled,
    mutationDisabledReason,
  }: {
    mutationDisabled: boolean;
    mutationDisabledReason: string;
  }) => (
    <div>
      Capital controls: {String(mutationDisabled)} · {mutationDisabledReason}
    </div>
  ),
}));

vi.mock("../findings/findings-tab", () => ({
  FindingsTab: ({
    mutationDisabled,
    mutationDisabledReason,
  }: {
    mutationDisabled: boolean;
    mutationDisabledReason: string;
  }) => (
    <div>
      Finding controls: {String(mutationDisabled)} · {mutationDisabledReason}
    </div>
  ),
}));

type WorkspaceProps = Parameters<typeof CaseWorkspace>[0];

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

const caseId = "90000000-0000-4000-8000-000000000001";

function renderWorkspace(overrides: Partial<WorkspaceProps> = {}) {
  const props: WorkspaceProps = {
    tenant,
    caseId,
    activeTab: "overview",
    reportMode: "json",
    updateSearch: vi.fn<WorkspaceProps["updateSearch"]>(),
    caseQuery: {
      data: undefined,
      error: null,
      isError: false,
      isFetching: false,
    },
    mockCaseData: mockCase(DEFAULT_ORG_ID, caseId),
    mockWorkspace: false,
    ...overrides,
  };

  return {
    props,
    ...renderWithQuery(<CaseWorkspace {...props} />),
  };
}

describe("CaseWorkspace", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("prompts the operator when no case is selected", () => {
    renderWorkspace({
      caseId: undefined,
      mockCaseData: undefined,
    });

    expect(screen.getByText("No case selected")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Select a case from the queue or current case selector.",
      ),
    ).toBeInTheDocument();
  });

  it("renders overview summary and disables deprecated single-case actions", () => {
    renderWorkspace();

    expect(
      screen.getByText("Covenant review - Northstar Foods"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Assign, unassign, and archive"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Single-case actions are left disabled/),
    ).toBeInTheDocument();
  });

  it("loads HTML reports in the report preview mode", async () => {
    const reportHtml = vi
      .spyOn(riskApi, "reportHtml")
      .mockResolvedValue("<main>HTML report</main>");

    renderWorkspace({
      activeTab: "report",
      reportMode: "html",
    });

    await waitFor(() => {
      expect(reportHtml).toHaveBeenCalledWith(tenant, caseId);
    });
    expect(
      await screen.findByTitle("Risk report HTML preview"),
    ).toHaveAttribute("srcdoc", "<main>HTML report</main>");
  });

  it("submits the decision form with the expected payload", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "decisions").mockResolvedValue([]);
    const createDecision = vi
      .spyOn(riskApi, "createDecision")
      .mockResolvedValue({
        id: "decision-1",
        organizationId: DEFAULT_ORG_ID,
        caseId,
        decision: "approved",
        previousDecision: null,
        reason: "Ready for approval",
        decidedBy: DEFAULT_USER_ID,
        createdAt: new Date(),
      } as CaseDecisionRead);

    renderWorkspace({
      activeTab: "decisions",
    });

    await user.type(
      await screen.findByPlaceholderText("Reason"),
      "Ready for approval",
    );
    await user.click(screen.getByRole("button", { name: "Submit decision" }));

    await waitFor(() => {
      expect(createDecision).toHaveBeenCalledWith(tenant, caseId, {
        decision: "approved",
        reason: "Ready for approval",
      });
    });
  });

  it("retires capital mutations when the selected case is archived", async () => {
    renderWorkspace({
      activeTab: "capital",
      mockCaseData: {
        ...mockCase(DEFAULT_ORG_ID, caseId),
        archivedAt: new Date("2026-07-14T12:00:00Z"),
      },
    });

    expect(
      await screen.findByText("Capital controls: true · retired-case"),
    ).toBeInTheDocument();
  });

  it("retires shared finding mutations when the selected case is archived", async () => {
    renderWorkspace({
      activeTab: "findings",
      mockCaseData: {
        ...mockCase(DEFAULT_ORG_ID, caseId),
        status: "archived",
      },
    });

    expect(
      await screen.findByText("Finding controls: true · retired-case"),
    ).toBeInTheDocument();
  });

  it("disables shared finding mutations in demo mode", async () => {
    renderWorkspace({
      activeTab: "findings",
      mockWorkspace: true,
    });

    expect(
      await screen.findByText("Finding controls: true · demo"),
    ).toBeInTheDocument();
  });
});
