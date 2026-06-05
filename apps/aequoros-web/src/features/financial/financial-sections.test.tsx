import type { FinancialDataWorkspaceRead } from "@aequoros/risk-service-api";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FinancialSections } from "./financial-sections";

describe("FinancialSections", () => {
  it("renders every financial workspace section when empty", () => {
    const workspace: FinancialDataWorkspaceRead = {
      organizationId: "org-1",
      caseId: "case-1",
      institutions: [],
      accounts: [],
      reportingPeriods: [],
      balances: [],
      obligations: [],
      sourceRows: [],
      recordSourceLinks: [],
      manualEdits: [],
      validationIssues: [],
    };

    render(<FinancialSections workspace={workspace} mocked={false} />);

    expect(screen.getByText("Institutions")).toBeInTheDocument();
    expect(screen.getByText("Accounts")).toBeInTheDocument();
    expect(screen.getByText("Reporting Periods")).toBeInTheDocument();
    expect(screen.getByText("Balances")).toBeInTheDocument();
    expect(screen.getByText("Obligations")).toBeInTheDocument();
    expect(screen.getByText("Source Rows")).toBeInTheDocument();
    expect(screen.getByText("Record Source Links")).toBeInTheDocument();
    expect(screen.getByText("Manual Edits")).toBeInTheDocument();
    expect(screen.getByText("Validation Issues")).toBeInTheDocument();
  });
});
