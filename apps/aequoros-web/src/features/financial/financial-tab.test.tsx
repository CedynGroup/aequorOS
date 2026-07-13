import type { FinancialDataWorkspaceRead } from "@aequoros/risk-service-api";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../../test/render";
import { financialReviewClient } from "./financial-client";
import { FinancialTab } from "./financial-tab";

const tenant = { orgId: "org-1", userId: "user-1" };

function emptyWorkspace(): FinancialDataWorkspaceRead {
  return {
    organizationId: tenant.orgId,
    caseId: "case-1",
    institutions: [],
    accounts: [],
    reportingPeriods: [],
    balances: [],
    cashFlows: [],
    covenants: [],
    obligations: [],
    sourceRows: [],
    recordSourceLinks: [],
    manualEdits: [],
    validationIssues: [],
    validationSummary: { error: 0, warning: 0, info: 0, total: 0 },
  };
}

describe("FinancialTab", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows loading and read-error states", async () => {
    let rejectRequest: ((error: Error) => void) | undefined;
    vi.spyOn(financialReviewClient, "workspace").mockReturnValue(
      new Promise((_, reject) => {
        rejectRequest = reject;
      }),
    );
    renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />,
    );

    expect(
      screen.getByLabelText("Loading financial workspace"),
    ).toBeInTheDocument();
    rejectRequest?.(new Error("Workspace unavailable"));
    expect(
      await screen.findByText("Workspace unavailable"),
    ).toBeInTheDocument();
  });

  it("maps source data and reflects an explicit revalidation result", async () => {
    const user = userEvent.setup();
    vi.spyOn(financialReviewClient, "workspace").mockResolvedValue(
      emptyWorkspace(),
    );
    const map = vi.spyOn(financialReviewClient, "map").mockResolvedValue({
      organizationId: tenant.orgId,
      caseId: "case-1",
      documentId: "document-1",
      documentExtractionId: "extraction-1",
      created: {},
      reused: {},
      summary: {
        sourceRowCount: 3,
        mappedSourceRowCount: 3,
        unmappedSourceRowCount: 0,
      },
      unmappedRows: [],
    });
    const validate = vi
      .spyOn(financialReviewClient, "validate")
      .mockResolvedValue({
        organizationId: tenant.orgId,
        caseId: "case-1",
        issueCount: 1,
        issues: [],
        summary: { error: 0, warning: 1, info: 0, total: 1 },
      });
    renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />,
    );
    const controls = await screen.findByLabelText(
      "Map and validate financial data",
    );

    await user.click(
      within(controls).getByRole("button", { name: "Map financial data" }),
    );
    expect(
      within(controls).getByText(/Enter exactly one document ID/),
    ).toBeInTheDocument();
    await user.type(
      within(controls).getByLabelText("Document ID"),
      "document-1",
    );
    await user.type(
      within(controls).getByLabelText("Extraction ID"),
      "extraction-1",
    );
    await user.click(
      within(controls).getByRole("button", { name: "Map financial data" }),
    );
    expect(
      within(controls).getByText(/Enter exactly one document ID/),
    ).toBeInTheDocument();
    expect(map).not.toHaveBeenCalled();
    await user.clear(within(controls).getByLabelText("Extraction ID"));
    await user.click(
      within(controls).getByRole("button", { name: "Map financial data" }),
    );
    await waitFor(() =>
      expect(map).toHaveBeenCalledWith(tenant, "case-1", {
        documentId: "document-1",
        documentExtractionId: undefined,
      }),
    );
    expect(
      await within(controls).findByText(/3 source rows reviewed/),
    ).toBeInTheDocument();

    await user.click(
      within(controls).getByRole("button", { name: "Revalidate" }),
    );
    await waitFor(() =>
      expect(validate).toHaveBeenCalledWith(tenant, "case-1"),
    );
    expect(
      await within(controls).findByText(/Validation refreshed: 1 issues/),
    ).toBeInTheDocument();
  });
});
