import type {
  ScenarioAssumptionRead,
  ScenarioRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { cleanup, screen } from "@testing-library/react";
import { page } from "vitest/browser";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../../styles.css";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { ScenariosTab } from "./scenarios-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};
const caseId = "90000000-0000-4000-8000-000000000001";
const scenarioId = "20000000-0000-4000-8000-000000000001";
const now = new Date("2026-07-13T12:00:00Z");

function assumption(
  id: string,
  category: string,
  key: string,
  label: string,
  value: number | string,
  unit: string,
): ScenarioAssumptionRead {
  return {
    id,
    organizationId: tenant.orgId,
    caseId,
    scenarioId,
    category,
    key,
    label,
    value,
    unit,
    provenance: { source: "system_default" },
    reviewStatus: "reviewed",
    reviewedBy: tenant.userId,
    reviewedAt: now,
    createdAt: now,
    updatedAt: now,
  };
}

const assumptions = [
  assumption("10000000-0000-4000-8000-000000000001", "cash_flow_timing", "cash_flow_delay_days", "Cash-flow delay", 5, "days"),
  assumption("10000000-0000-4000-8000-000000000002", "credit_usage", "credit_usage_rate", "Credit usage", "0.35", "ratio"),
  assumption("10000000-0000-4000-8000-000000000003", "expenses", "expense_growth_rate", "Expense growth", "0.03", "ratio"),
  assumption("10000000-0000-4000-8000-000000000004", "growth", "revenue_growth_rate", "Revenue growth", "0.04", "ratio"),
  assumption("10000000-0000-4000-8000-000000000005", "repayment_behavior", "repayment_rate", "Repayment rate", "1.0", "ratio"),
];

const scenario: ScenarioRead = {
  id: scenarioId,
  organizationId: tenant.orgId,
  caseId,
  name: "Baseline",
  description: "Management plan with modest growth and normal collection timing.",
  scenarioType: "baseline",
  copiedFromScenarioId: null,
  createdBy: tenant.userId,
  archivedAt: null,
  createdAt: now,
  updatedAt: now,
  assumptions,
};

const workspace: ScenarioWorkspaceRead = {
  caseId,
  scenarios: [scenario],
  readiness: {
    caseId,
    ready: true,
    scenarioCount: 1,
    completeScenarioCount: 1,
    incompleteScenarioIds: [],
  },
};

describe("scenario editor browser rendering", () => {
  beforeEach(() => {
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(workspace);
    vi.spyOn(riskApi, "scenarioValidation").mockResolvedValue({
      scenarioId,
      complete: true,
      issueCount: 0,
      issues: [],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders API string ratios as editable percentages in the compact table", async () => {
    await page.viewport(1280, 800);
    renderWithQuery(<ScenariosTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByLabelText("Credit usage value")).toHaveValue("35");
    expect(screen.getByLabelText("Expense growth value")).toHaveValue("3");
    expect(screen.getByLabelText("Revenue growth value")).toHaveValue("4");
    expect(screen.getByLabelText("Repayment rate value")).toHaveValue("100");
    expect(screen.getByTestId("assumption-table")).toBeVisible();

    const evidenceDir = import.meta.env.VITE_NO_MISTAKES_EVIDENCE_DIR;
    if (evidenceDir) {
      await page.screenshot({
        path: `${evidenceDir}/scenario-string-ratios-as-percentages.png`,
      });
    }
  });
});
