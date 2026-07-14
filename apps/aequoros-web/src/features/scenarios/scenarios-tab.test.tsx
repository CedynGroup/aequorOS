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
    window.history.replaceState(null, "", window.location.pathname);
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn<() => void>(),
    });
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

  it("opens and focuses a scenario assumption from an evidence deep link", async () => {
    const target = `scenario-${scenario().id}-assumption-${assumption().id}`;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    const archivedScenario = scenario({
      name: "Archived downside",
      archivedAt: now,
    });
    const scenarios = vi
      .spyOn(riskApi, "scenarios")
      .mockResolvedValue(workspace([archivedScenario]));

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);

    await waitFor(() => expect(document.activeElement?.id).toBe(target));
    expect(scenarios).toHaveBeenCalledWith(tenant, caseId, true);
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(
      screen.getByText("Archived scenario audit mode"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Scenario name")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Revenue growth value type")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save details" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy scenario" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Archive scenario" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Add assumption" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
    expect(screen.queryByLabelText("Custom scenario name")).toBeNull();
  });

  it("focuses an evidence deep link only once across rerenders and refetches", async () => {
    const target = `scenario-${scenario().id}-assumption-${assumption().id}`;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    const scenarios = vi
      .spyOn(riskApi, "scenarios")
      .mockResolvedValueOnce(workspace())
      .mockResolvedValue(
        workspace([scenario({ description: "Refetched scenario" })]),
      );
    const view = renderWithQuery(
      <ScenariosTab tenant={tenant} caseId={caseId} />,
    );

    await waitFor(() => expect(document.activeElement?.id).toBe(target));
    const scrollIntoView = vi.mocked(Element.prototype.scrollIntoView);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);

    view.rerender(<ScenariosTab tenant={tenant} caseId={caseId} />);
    await view.queryClient.refetchQueries({
      queryKey: ["scenarios", tenant, caseId, true],
      exact: true,
    });

    await waitFor(() => expect(scenarios).toHaveBeenCalledTimes(2));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("shows archived scenarios with no issues as historically valid", async () => {
    const target = `scenario-${scenario().id}-assumption-${assumption().id}`;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(
      workspace([scenario({ archivedAt: now })]),
    );
    vi.spyOn(riskApi, "scenarioValidation").mockResolvedValue({
      scenarioId: scenario().id,
      complete: false,
      issueCount: 0,
      issues: [],
    });

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("Scenario validation passed"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("All required assumptions are present and reviewed."),
    ).toBeInTheDocument();
    expect(screen.queryByText("0 validation issues")).toBeNull();
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
  });

  it("keeps normal scenario navigation active-only", async () => {
    const scenarios = vi
      .spyOn(riskApi, "scenarios")
      .mockResolvedValue(workspace());

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByLabelText("Scenario name")).toHaveValue(
      "Baseline",
    );
    expect(scenarios).toHaveBeenCalledWith(tenant, caseId, false);
  });

  it("ignores malformed scenario evidence fragments", async () => {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#scenario-not-a-uuid-assumption-also-invalid`,
    );
    const scenarios = vi
      .spyOn(riskApi, "scenarios")
      .mockResolvedValue(workspace());

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByLabelText("Scenario name")).toHaveValue(
      "Baseline",
    );
    expect(scenarios).toHaveBeenCalledWith(tenant, caseId, false);
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
    const updateScenario = vi
      .spyOn(riskApi, "updateScenario")
      .mockResolvedValueOnce(mutation(scenario({ name: "Operating plan" })))
      .mockResolvedValueOnce(
        mutation(
          scenario({
            name: "Operating plan",
            description: "Updated operating plan",
          }),
        ),
      );
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
    const scenarioName = await screen.findByLabelText("Scenario name");
    await user.clear(scenarioName);
    await user.type(scenarioName, "  Operating plan  ");
    await user.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => {
      expect(updateScenario).toHaveBeenCalledWith(
        tenant,
        caseId,
        scenario().id,
        {
          name: "  Operating plan  ",
          reason: "Update scenario details",
        },
      );
    });
    expect(scenarioName).toHaveValue("Operating plan");
    expect(screen.getByRole("button", { name: "Save details" })).toBeDisabled();

    const scenarioDescription = screen.getByLabelText("Scenario description");
    await user.clear(scenarioDescription);
    await user.type(scenarioDescription, "Updated operating plan");
    await user.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => {
      expect(updateScenario).toHaveBeenLastCalledWith(
        tenant,
        caseId,
        scenario().id,
        {
          description: "Updated operating plan",
          reason: "Update scenario details",
        },
      );
    });
    expect(scenarioDescription).toHaveValue("Updated operating plan");

    const value = await screen.findByLabelText("Revenue growth value");
    await user.clear(value);
    await user.type(value, "0.05");
    expect(screen.getByRole("button", { name: "Review" })).toBeDisabled();
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

  it("preserves values entered with the explicit string type", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(workspace());
    const update = vi
      .spyOn(riskApi, "updateAssumption")
      .mockResolvedValue(mutation());
    const create = vi
      .spyOn(riskApi, "createAssumption")
      .mockResolvedValue(mutation());

    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);
    await user.selectOptions(
      await screen.findByLabelText("Revenue growth value type"),
      "string",
    );
    const value = screen.getByLabelText("Revenue growth value");
    await user.clear(value);
    await user.type(value, "001");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(
        tenant,
        caseId,
        scenario().id,
        assumption().id,
        { value: "001", reason: "Reviewer updated assumption" },
      );
    });

    await user.type(screen.getByLabelText("Assumption key"), "policy_code");
    await user.type(screen.getByLabelText("Assumption label"), "Policy code");
    await user.type(screen.getByLabelText("New assumption value"), "true");
    await user.click(screen.getByRole("button", { name: "Add assumption" }));
    await waitFor(() => {
      expect(create).toHaveBeenCalledWith(tenant, caseId, scenario().id, {
        category: "other",
        key: "policy_code",
        label: "Policy code",
        value: "true",
        unit: undefined,
        reason: "Add scenario assumption",
      });
    });
  });

  it.each([
    { persistedValue: 0, invalidValue: "" },
    { persistedValue: false, invalidValue: "invalid" },
  ])(
    "prevents reviewing $persistedValue while invalid text is displayed",
    async ({ persistedValue, invalidValue }) => {
      const user = userEvent.setup();
      vi.spyOn(riskApi, "scenarios").mockResolvedValue(
        workspace([
          scenario({ assumptions: [assumption({ value: persistedValue })] }),
        ]),
      );
      const review = vi
        .spyOn(riskApi, "reviewAssumption")
        .mockResolvedValue(mutation());

      renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);
      const value = await screen.findByLabelText("Revenue growth value");
      await user.clear(value);
      if (invalidValue) await user.type(value, invalidValue);

      expect(screen.getByRole("button", { name: "Review" })).toBeDisabled();
      expect(review).not.toHaveBeenCalled();
    },
  );

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
