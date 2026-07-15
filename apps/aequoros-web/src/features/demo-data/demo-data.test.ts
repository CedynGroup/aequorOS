import { CaseSort, RiskLevel } from "@aequoros/risk-service-api";
import { describe, expect, it } from "vitest";

import { DEFAULT_USER_ID } from "../../lib/constants";
import {
  DEMO_CASE_IDS,
  emptyWorkspace,
  mockCase,
  mockCaseHealth,
  mockCaseList,
} from "./demo-data";

describe("demo data helpers", () => {
  it("returns every financial workspace section as an empty array", () => {
    expect(emptyWorkspace("org-1", "case-1")).toMatchObject({
      organizationId: "org-1",
      caseId: "case-1",
      institutions: [],
      accounts: [],
      reportingPeriods: [],
      balances: [],
      cashFlows: [],
      obligations: [],
      sourceRows: [],
      recordSourceLinks: [],
      manualEdits: [],
      validationIssues: [],
    });
  });

  it("filters and sorts mock case queue data", () => {
    const result = mockCaseList(
      "org-1",
      {
        q: "",
        status: "all",
        risk: RiskLevel.High,
        archived: false,
        sort: CaseSort.RiskScoreDesc,
      },
      1,
    );

    expect(result.total).toBe(2);
    expect(result.items[0].title).toContain("Adom Textiles");
    expect(result.items[0].riskLevel).toBe(RiskLevel.High);
  });

  it("creates a mock detail record from queue data", () => {
    const detail = mockCase("org-1", "90000000-0000-4000-8000-000000000001");

    expect(detail.organizationId).toBe("org-1");
    expect(detail.assignedToUserId).toBe(DEFAULT_USER_ID);
    expect(detail.metadata).toMatchObject({ mocked: true });
  });

  it("creates internally consistent case-health data for demo cases", () => {
    const caseId = DEMO_CASE_IDS[1];
    const health = mockCaseHealth("org-1", caseId);

    expect(health.financial.caseId).toBe(caseId);
    expect(health.financial.institutions).toHaveLength(1);
    expect(health.financial.accounts).toHaveLength(1);
    expect(health.financial.reportingPeriods).toHaveLength(1);
    expect(health.financial.balances).toHaveLength(3);
    expect(health.financial.cashFlows).toHaveLength(2);
    expect(health.financial.obligations).toHaveLength(1);
    expect(health.financial.obligations[0]?.reportingPeriodId).toBe(
      health.financial.reportingPeriods[0]?.id,
    );
    expect(health.financial.covenants[0]?.complianceStatus).toBe(
      "non_compliant",
    );
    expect(health.scenarios.readiness).toMatchObject({
      ready: true,
      scenarioCount: 2,
      completeScenarioCount: 2,
    });
    expect(health.runs.latestSuccessfulRunId).toBe(health.runs.runs[0]?.id);
    expect(health.calculationRun.id).toBe(health.runs.runs[0]?.id);
    expect(health.calculationRun.outputs).toHaveLength(3);
    expect(health.calculationRun.inputs).toMatchObject({
      reportingPeriodId: health.financial.reportingPeriods[0]?.id,
      scenarioId: health.scenarios.scenarios[0]?.id,
    });
    expect(
      Object.values(health.scenarioValidations).every(
        (validation) => validation.complete && validation.issueCount === 0,
      ),
    ).toBe(true);
    expect(health.findings).toHaveLength(2);
    expect(
      health.findings.filter((finding) =>
        ["open", "needs_review"].includes(finding.status),
      ),
    ).toHaveLength(1);
    expect(
      health.findings.filter((finding) => finding.status === "resolved"),
    ).toHaveLength(1);
    expect(health.decisions).toMatchObject([
      { decision: "needs_more_info", caseId },
    ]);
    expect(mockCase("org-1", caseId).description).toContain(
      "populated, validated financial workspace",
    );
  });
});
