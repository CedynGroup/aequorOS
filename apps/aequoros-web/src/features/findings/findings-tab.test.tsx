import type { FindingRead } from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { mockCaseHealth } from "../demo-data/demo-data";
import { FindingsTab } from "./findings-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

function finding(overrides: Partial<FindingRead> = {}): FindingRead {
  return {
    id: "finding-1",
    organizationId: DEFAULT_ORG_ID,
    caseId: "case-1",
    assessmentId: null,
    runId: null,
    riskType: "liquidity_gap",
    title: "Cash conversion cycle widened",
    summary: "Receivables increased faster than revenue.",
    rationale: null,
    severity: "high",
    likelihood: null,
    impact: null,
    confidence: null,
    status: "accepted",
    dispositionReason: "Accepted for covenant monitoring.",
    source: "manual",
    ruleId: "liquidity_cash_conversion",
    ruleVersion: null,
    scoreImpact: 24,
    details: {},
    createdAt: new Date(),
    updatedAt: new Date(),
    ...overrides,
  };
}

describe("FindingsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the create form and finding update controls", async () => {
    vi.spyOn(riskApi, "findings").mockResolvedValue([finding()]);

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    expect(
      await screen.findByText("Create manual finding"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Cash conversion cycle widened"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
  });

  it("submits manual finding payloads", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "findings").mockResolvedValue([]);
    const createFinding = vi.spyOn(riskApi, "createFinding").mockResolvedValue(
      finding({
        title: "Missing management commentary",
        summary: "Reviewer needs current quarter commentary.",
      }),
    );

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    await user.type(
      await screen.findByPlaceholderText("Title"),
      "Missing management commentary",
    );
    await user.type(
      screen.getByPlaceholderText("Summary"),
      "Reviewer needs current quarter commentary.",
    );
    await user.click(screen.getByRole("button", { name: "Create finding" }));

    await waitFor(() => {
      expect(createFinding).toHaveBeenCalledWith(tenant, "case-1", {
        riskType: "manual_review",
        title: "Missing management commentary",
        summary: "Reviewer needs current quarter commentary.",
        severity: "medium",
        details: {},
      });
    });
  });

  it("submits finding status updates", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "findings").mockResolvedValue([finding()]);
    const updateFinding = vi
      .spyOn(riskApi, "updateFinding")
      .mockResolvedValue(finding());

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    await user.click(await screen.findByRole("button", { name: "Update" }));

    await waitFor(() => {
      expect(updateFinding).toHaveBeenCalledWith(tenant, "finding-1", {
        status: "accepted",
        dispositionReason: "Accepted for covenant monitoring.",
      });
    });
  });

  it("invalidates capital views after capital finding updates", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "findings").mockResolvedValue([
      finding({ ruleVersion: "capital-projection-v1.0.0" }),
    ]);
    vi.spyOn(riskApi, "updateFinding").mockResolvedValue(
      finding({ ruleVersion: "capital-projection-v1.0.0" }),
    );
    const { queryClient } = renderWithQuery(
      <FindingsTab tenant={tenant} caseId="case-1" />,
    );
    const capitalQueryKeys = [
      ["capital-projections"],
      ["capital-projection"],
      ["capital-summary"],
      ["capital-comparison"],
    ];
    for (const queryKey of capitalQueryKeys) {
      queryClient.setQueryData(queryKey, {});
    }

    await user.click(await screen.findByRole("button", { name: "Update" }));

    await waitFor(() => {
      for (const queryKey of capitalQueryKeys) {
        expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(true);
      }
    });
  });

  it("keeps archived-case findings visible but disables mutations", async () => {
    vi.spyOn(riskApi, "findings").mockResolvedValue([finding()]);
    const createFinding = vi.spyOn(riskApi, "createFinding");
    const updateFinding = vi.spyOn(riskApi, "updateFinding");

    renderWithQuery(
      <FindingsTab tenant={tenant} caseId="case-1" mutationDisabled />,
    );

    expect(
      await screen.findByText("Cash conversion cycle widened"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Finding mutations are unavailable for retired cases."),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Title")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Create finding" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Update" })).toBeDisabled();
    expect(createFinding).not.toHaveBeenCalled();
    expect(updateFinding).not.toHaveBeenCalled();
  });

  it("explains disabled finding mutations in demo mode", async () => {
    vi.spyOn(riskApi, "findings").mockResolvedValue([finding()]);

    renderWithQuery(
      <FindingsTab
        tenant={tenant}
        caseId="case-1"
        mutationDisabled
        mutationDisabledReason="demo"
      />,
    );

    expect(
      await screen.findByText(
        "Finding mutations are unavailable in demo mode.",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Cash conversion cycle widened"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create finding" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Update" })).toBeDisabled();
  });

  it("renders complete demo findings without live queries", async () => {
    const findings = vi.spyOn(riskApi, "findings");
    const create = vi.spyOn(riskApi, "createFinding");
    const update = vi.spyOn(riskApi, "updateFinding");
    const demoFindings = mockCaseHealth(DEFAULT_ORG_ID, "case-1").findings;

    renderWithQuery(
      <FindingsTab
        tenant={tenant}
        caseId="case-1"
        mutationDisabled
        mutationDisabledReason="demo"
        demoFindings={demoFindings}
      />,
    );

    expect(await screen.findAllByText(/Demo high finding/)).not.toHaveLength(0);
    expect(
      screen.getByText("Resolved demo covenant finding"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Update" })[0]).toBeDisabled();
    expect(findings).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();
  });

  it("renders liquidity workflow findings as read-only", async () => {
    vi.spyOn(riskApi, "findings").mockResolvedValue([
      finding({
        riskType: "liquidity_risk",
        source: "deterministic_rule",
        status: "superseded",
        details: {
          liquidity: {
            workflow_id: "liquidity_analysis",
            calculation_run_id: "run-1",
          },
        },
      }),
    ]);
    const updateFinding = vi.spyOn(riskApi, "updateFinding");

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    expect(
      await screen.findByText(
        "Liquidity workflow finding — review in the Liquidity tab.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("superseded")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Update" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("Disposition reason"),
    ).not.toBeInTheDocument();
    expect(updateFinding).not.toHaveBeenCalled();
  });
});
