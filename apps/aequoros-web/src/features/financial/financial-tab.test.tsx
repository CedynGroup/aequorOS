import type { FinancialDataWorkspaceRead } from "@aequoros/risk-service-api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
    const refreshedWorkspace = emptyWorkspace();
    refreshedWorkspace.sourceRows = [
      {
        id: "source-3",
        organizationId: tenant.orgId,
        caseId: "case-1",
        documentId: "document-1",
        documentExtractionId: "extraction-1",
        rowIndex: 8,
        locator: { sheet: "Debt", cell: "A8" },
        rawPayload: { obligation_type: "term_loan" },
        createdAt: new Date("2026-07-13T12:00:00Z"),
      },
    ];
    vi.spyOn(financialReviewClient, "workspace")
      .mockResolvedValueOnce(emptyWorkspace())
      .mockResolvedValue(refreshedWorkspace);
    const map = vi.spyOn(financialReviewClient, "map").mockResolvedValue({
      organizationId: tenant.orgId,
      caseId: "case-1",
      documentId: "document-1",
      documentExtractionId: "extraction-1",
      created: {},
      reused: {},
      summary: {
        sourceRowCount: 3,
        mappedSourceRowCount: 2,
        unmappedSourceRowCount: 1,
      },
      unmappedRows: [
        {
          sourceRowId: "source-3",
          rowIndex: 8,
          reason: "No canonical record matched",
          locator: { sheet: "Debt", cell: "A8" },
        },
      ],
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
      await within(controls).findByText(/2 of 3 source rows mapped/),
    ).toBeInTheDocument();
    const unmapped = within(controls).getByLabelText("Unmapped source rows");
    expect(within(unmapped).getByText(/Row 8/)).toBeInTheDocument();
    expect(within(unmapped).getByText(/Debt/)).toBeInTheDocument();
    expect(within(unmapped).getByText(/term_loan/)).toBeInTheDocument();

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

  it("reports a mapping refresh failure instead of declaring success", async () => {
    const user = userEvent.setup();
    vi.spyOn(financialReviewClient, "workspace")
      .mockResolvedValueOnce(emptyWorkspace())
      .mockRejectedValueOnce(new Error("Workspace refresh failed"));
    vi.spyOn(financialReviewClient, "map").mockResolvedValue({
      organizationId: tenant.orgId,
      caseId: "case-1",
      documentId: "document-1",
      documentExtractionId: "extraction-1",
      created: {},
      reused: {},
      summary: {
        sourceRowCount: 1,
        mappedSourceRowCount: 1,
        unmappedSourceRowCount: 0,
      },
      unmappedRows: [],
    });
    renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />,
    );
    const controls = await screen.findByLabelText(
      "Map and validate financial data",
    );

    await user.type(
      within(controls).getByLabelText("Document ID"),
      "document-1",
    );
    await user.click(
      within(controls).getByRole("button", { name: "Map financial data" }),
    );

    expect(
      await within(controls).findByText("Workspace refresh failed"),
    ).toBeInTheDocument();
    expect(
      within(controls).queryByText(/Mapping complete/),
    ).not.toBeInTheDocument();
  });

  it("forces a workspace refresh despite the production stale time", async () => {
    const user = userEvent.setup();
    const workspaceRequest = vi
      .spyOn(financialReviewClient, "workspace")
      .mockResolvedValue(emptyWorkspace());
    vi.spyOn(financialReviewClient, "map").mockResolvedValue({
      organizationId: tenant.orgId,
      caseId: "case-1",
      documentId: "document-1",
      documentExtractionId: "extraction-1",
      created: {},
      reused: {},
      summary: {
        sourceRowCount: 1,
        mappedSourceRowCount: 1,
        unmappedSourceRowCount: 0,
      },
      unmappedRows: [],
    });
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 20_000 },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />
      </QueryClientProvider>,
    );
    const controls = await screen.findByLabelText(
      "Map and validate financial data",
    );

    await user.type(
      within(controls).getByLabelText("Document ID"),
      "document-1",
    );
    await user.click(
      within(controls).getByRole("button", { name: "Map financial data" }),
    );

    await within(controls).findByText(/Mapping complete/);
    expect(workspaceRequest).toHaveBeenCalledTimes(2);
  });

  it("keeps demo mode read-only when the workspace request succeeds", async () => {
    vi.spyOn(financialReviewClient, "workspace").mockResolvedValue(
      emptyWorkspace(),
    );

    renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={true} />,
    );

    expect(
      await screen.findByText("Editing disabled for demo data"),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Map and validate financial data"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add institution" }),
    ).not.toBeInTheDocument();
  });

  it("shows persisted source rows without canonical links as unmapped", async () => {
    const persistedWorkspace = emptyWorkspace();
    persistedWorkspace.sourceRows = [
      {
        id: "source-unmapped",
        organizationId: tenant.orgId,
        caseId: "case-1",
        documentId: "document-1",
        documentExtractionId: "extraction-1",
        rowIndex: 12,
        locator: { sheet: "Debt", cell: "A12" },
        rawPayload: { principal: "500000" },
        createdAt: new Date("2026-07-13T12:00:00Z"),
      },
    ];
    vi.spyOn(financialReviewClient, "workspace").mockResolvedValue(
      persistedWorkspace,
    );

    renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />,
    );

    const unmapped = await screen.findByLabelText("Unmapped source rows");
    expect(within(unmapped).getByText(/Row 12/)).toBeInTheDocument();
    expect(within(unmapped).getByText(/500000/)).toBeInTheDocument();
  });
});
