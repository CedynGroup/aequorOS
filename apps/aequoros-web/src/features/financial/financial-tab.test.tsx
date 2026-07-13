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

  it("preserves concurrent mutations and refreshes them in order", async () => {
    const user = userEvent.setup();
    const initialWorkspace = emptyWorkspace();
    let resolveFirstRefresh:
      | ((workspace: FinancialDataWorkspaceRead) => void)
      | undefined;
    const firstRefresh = new Promise<FinancialDataWorkspaceRead>((resolve) => {
      resolveFirstRefresh = resolve;
    });
    const institution = {
      id: "institution-1",
      organizationId: tenant.orgId,
      caseId: "case-1",
      name: "Northstar Bank",
      institutionType: null,
      referenceCode: null,
      metadata: {},
      createdAt: new Date("2026-07-13T12:00:00Z"),
      updatedAt: new Date("2026-07-13T12:00:00Z"),
    };
    const account = {
      id: "account-1",
      organizationId: tenant.orgId,
      caseId: "case-1",
      accountName: "Operating account",
      accountNumber: null,
      accountType: null,
      currency: null,
      status: null,
      institutionId: null,
      metadata: {},
      createdAt: new Date("2026-07-13T12:00:00Z"),
      updatedAt: new Date("2026-07-13T12:00:00Z"),
    };
    const finalWorkspace = {
      ...initialWorkspace,
      institutions: [institution],
      accounts: [account],
    } as FinancialDataWorkspaceRead;
    const workspaceRequest = vi
      .spyOn(financialReviewClient, "workspace")
      .mockResolvedValueOnce(initialWorkspace)
      .mockReturnValueOnce(firstRefresh)
      .mockResolvedValueOnce(finalWorkspace);
    vi.spyOn(financialReviewClient, "create").mockImplementation(
      async (kind) =>
        ({
          record: kind === "institution" ? institution : account,
          validation: {
            organizationId: tenant.orgId,
            caseId: "case-1",
            issueCount: 0,
            issues: [],
            summary: { error: 0, warning: 0, info: 0, total: 0 },
          },
        }) as Awaited<ReturnType<typeof financialReviewClient.create>>,
    );
    const { queryClient } = renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Add institution" }),
    );
    const institutionForm = screen.getByRole("form", {
      name: "Add institution",
    });
    await user.type(
      within(institutionForm).getByLabelText("Name"),
      institution.name,
    );
    await user.type(
      within(institutionForm).getByLabelText("Reason"),
      "Add verified institution",
    );
    await user.click(
      within(institutionForm).getByRole("button", { name: "Add institution" }),
    );
    await waitFor(() => expect(workspaceRequest).toHaveBeenCalledTimes(2));

    await user.click(screen.getByRole("button", { name: "Add account" }));
    const accountForm = screen.getByRole("form", { name: "Add account" });
    await user.type(
      within(accountForm).getByLabelText("Account name"),
      account.accountName,
    );
    await user.type(
      within(accountForm).getByLabelText("Reason"),
      "Add verified account",
    );
    await user.click(
      within(accountForm).getByRole("button", { name: "Add account" }),
    );

    await waitFor(() => {
      const cached = queryClient.getQueryData<FinancialDataWorkspaceRead>([
        "financial-workspace",
        tenant,
        "case-1",
      ]);
      expect(cached?.institutions).toHaveLength(1);
      expect(cached?.accounts).toHaveLength(1);
    });
    expect(workspaceRequest).toHaveBeenCalledTimes(2);

    resolveFirstRefresh?.({
      ...initialWorkspace,
      institutions: [institution],
    } as FinancialDataWorkspaceRead);

    await waitFor(() => expect(workspaceRequest).toHaveBeenCalledTimes(3));
    await waitFor(() => {
      const cached = queryClient.getQueryData<FinancialDataWorkspaceRead>([
        "financial-workspace",
        tenant,
        "case-1",
      ]);
      expect(cached?.institutions).toHaveLength(1);
      expect(cached?.accounts).toHaveLength(1);
    });
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

  it("resets open mutation forms when the case identity changes", async () => {
    const user = userEvent.setup();
    vi.spyOn(financialReviewClient, "workspace").mockImplementation(
      async (requestTenant, requestCaseId) => ({
        ...emptyWorkspace(),
        organizationId: requestTenant.orgId,
        caseId: requestCaseId,
      }),
    );
    const { queryClient, rerender } = renderWithQuery(
      <FinancialTab tenant={tenant} caseId="case-1" mockWorkspace={false} />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Add institution" }),
    );
    await user.type(
      within(
        screen.getByRole("form", { name: "Add institution" }),
      ).getByLabelText("Name"),
      "case one draft",
    );
    queryClient.setQueryData(["financial-workspace", tenant, "case-2"], {
      ...emptyWorkspace(),
      caseId: "case-2",
    });

    rerender(
      <FinancialTab tenant={tenant} caseId="case-2" mockWorkspace={false} />,
    );

    expect(
      screen.queryByRole("form", { name: "Add institution" }),
    ).not.toBeInTheDocument();
  });
});
