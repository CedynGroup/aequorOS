import type {
  ScenarioAssumptionRead,
  ScenarioMutationResponse,
  ScenarioRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { ScenariosTab } from "./scenarios-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};
const caseId = "90000000-0000-4000-8000-000000000001";
const now = new Date("2026-07-13T12:00:00Z");

function assumption(
  overrides: Partial<ScenarioAssumptionRead> = {},
): ScenarioAssumptionRead {
  return {
    id: "10000000-0000-4000-8000-000000000001",
    organizationId: tenant.orgId,
    caseId,
    scenarioId: "20000000-0000-4000-8000-000000000001",
    category: "growth",
    key: "revenue_growth_rate",
    label: "Revenue growth",
    value: 0,
    unit: "ratio",
    provenance: { source: "system_default" },
    reviewStatus: "draft",
    reviewedBy: null,
    reviewedAt: null,
    createdAt: now,
    updatedAt: now,
    ...overrides,
  };
}

function scenario(overrides: Partial<ScenarioRead> = {}): ScenarioRead {
  return {
    id: "20000000-0000-4000-8000-000000000001",
    organizationId: tenant.orgId,
    caseId,
    name: "Baseline",
    description: "Default baseline scenario",
    scenarioType: "baseline",
    copiedFromScenarioId: null,
    createdBy: tenant.userId,
    archivedAt: null,
    createdAt: now,
    updatedAt: now,
    assumptions: [assumption()],
    ...overrides,
  };
}

function workspace(
  scenarios: ScenarioRead[] = [scenario()],
): ScenarioWorkspaceRead {
  return {
    caseId,
    scenarios,
    readiness: {
      caseId,
      ready: false,
      scenarioCount: scenarios.length,
      completeScenarioCount: 0,
      incompleteScenarioIds: scenarios.map((item) => item.id),
    },
  };
}

function mutation(result = scenario()): ScenarioMutationResponse {
  return {
    scenario: result,
    validation: {
      scenarioId: result.id,
      complete: false,
      issueCount: 1,
      issues: [
        {
          code: "assumption_review_required",
          message: "Revenue growth must be reviewed.",
          category: "growth",
          assumptionId: assumption().id,
        },
      ],
    },
    readiness: workspace([result]).readiness,
  };
}

describe("ScenariosTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(riskApi, "scenarioValidation").mockResolvedValue({
      scenarioId: scenario().id,
      complete: false,
      issueCount: 1,
      issues: [
        {
          code: "assumption_review_required",
          message: "Revenue growth must be reviewed.",
          category: "growth",
          assumptionId: assumption().id,
        },
      ],
    });
  });

  it("renders an explicit empty state and initializes baseline and downside", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "scenarios")
      .mockResolvedValueOnce(workspace([]))
      .mockResolvedValue(workspace([scenario()]));
    const initialize = vi
      .spyOn(riskApi, "initializeScenarios")
      .mockResolvedValue(workspace([scenario()]));

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);
    expect(await screen.findByText("No scenarios yet")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Initialize baseline and downside" }),
    );

    await waitFor(() => {
      expect(initialize).toHaveBeenCalledWith(tenant, caseId, {
        reason: "Initialize scenario workspace",
      });
    });
    expect(await screen.findByText("Saved successfully")).toBeInTheDocument();
  });

  it("edits, reviews, copies, and archives scenarios with successful-save feedback", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(workspace());
    const update = vi
      .spyOn(riskApi, "updateAssumption")
      .mockResolvedValue(mutation());
    const review = vi
      .spyOn(riskApi, "reviewAssumption")
      .mockResolvedValue(mutation());
    const copy = vi
      .spyOn(riskApi, "copyScenario")
      .mockResolvedValue(
        mutation(scenario({ id: "copy-id", name: "Baseline copy" })),
      );
    const archive = vi
      .spyOn(riskApi, "archiveScenario")
      .mockResolvedValue(mutation());

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);
    const value = await screen.findByLabelText("Revenue growth value");
    await user.clear(value);
    await user.type(value, "0.05");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(
        tenant,
        caseId,
        scenario().id,
        assumption().id,
        { value: 0.05, reason: "Reviewer updated assumption" },
      );
    });

    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(review).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Copy scenario" }));
    await waitFor(() => expect(copy).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Archive scenario" }));
    await waitFor(() => expect(archive).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Saved successfully")).toBeInTheDocument();
  });

  it("renders API errors explicitly", async () => {
    vi.spyOn(riskApi, "scenarios").mockRejectedValue({
      statusCode: 503,
      code: "unavailable",
      message: "Scenario service unavailable",
    });
    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);
    expect(
      await screen.findByText("Scenario service unavailable"),
    ).toBeInTheDocument();
  });
});
