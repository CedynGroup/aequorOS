import type {
  FinancialDataWorkspaceRead,
  FinancialInstitutionMutationResponse,
  FinancialCovenantMutationResponse,
} from "@aequoros/risk-service-api";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { TenantHeaders } from "../../lib/api";
import type { FinancialReviewClient } from "./financial-client";
import { FinancialSections } from "./financial-sections";

type WorkspaceUpdater = (
  workspace: FinancialDataWorkspaceRead,
) => FinancialDataWorkspaceRead;

const tenant: TenantHeaders = { orgId: "org-1", userId: "user-1" };

beforeAll(() => {
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn<() => void>(),
  });
});

function workspace(): FinancialDataWorkspaceRead {
  const now = new Date("2026-07-13T12:00:00Z");
  return {
    organizationId: tenant.orgId,
    caseId: "case-1",
    institutions: [
      {
        id: "institution-1",
        organizationId: tenant.orgId,
        caseId: "case-1",
        name: "Northstar Bank",
        institutionType: "bank",
        referenceCode: "NSB",
        metadata: {},
        createdAt: now,
        updatedAt: now,
      },
    ],
    accounts: [],
    reportingPeriods: [],
    balances: [],
    cashFlows: [
      {
        id: "cash-flow-1",
        organizationId: tenant.orgId,
        caseId: "case-1",
        accountId: null,
        reportingPeriodId: null,
        cashFlowDate: now,
        amount: "1250.00",
        currency: "USD",
        direction: "inflow",
        category: "receipts",
        metadata: {},
        createdAt: now,
        updatedAt: now,
      },
    ],
    covenants: [],
    obligations: [],
    sourceRows: [
      {
        id: "source-1",
        organizationId: tenant.orgId,
        caseId: "case-1",
        documentId: "document-1234567890",
        documentExtractionId: "extraction-1",
        rowIndex: 7,
        locator: { sheet: "Balance Sheet" },
        rawPayload: { name: "Northstar Bank" },
        createdAt: now,
      },
    ],
    recordSourceLinks: [
      {
        id: "link-1",
        organizationId: tenant.orgId,
        caseId: "case-1",
        recordTable: "financial_institutions",
        recordId: "institution-1",
        fieldName: "name",
        sourceRowId: "source-1",
        sourceField: "name",
        confidence: "high",
        metadata: {},
        createdAt: now,
      },
    ],
    manualEdits: [],
    validationIssues: [
      {
        id: "issue-1",
        organizationId: tenant.orgId,
        caseId: "case-1",
        issueKey: "institution_name",
        severity: "warning",
        status: "open",
        ruleId: "institution_name_review",
        code: null,
        message: "Confirm institution name",
        fieldName: "name",
        field: "name",
        entityType: "institution",
        entityId: "institution-1",
        recordTable: "financial_institutions",
        recordId: "institution-1",
        details: {},
        resolvedAt: null,
        createdAt: now,
      },
    ],
    validationSummary: { error: 0, info: 0, total: 1, warning: 1 },
  };
}

function validation(issueCount = 0) {
  return {
    organizationId: tenant.orgId,
    caseId: "case-1",
    issueCount,
    issues: [],
    summary: { error: 0, info: 0, total: issueCount, warning: issueCount },
  };
}

function client(
  overrides: Partial<FinancialReviewClient> = {},
): FinancialReviewClient {
  return {
    workspace: vi
      .fn<FinancialReviewClient["workspace"]>()
      .mockResolvedValue(workspace()),
    create: vi.fn<FinancialReviewClient["create"]>(),
    update: vi.fn<FinancialReviewClient["update"]>(),
    map: vi.fn<FinancialReviewClient["map"]>(),
    validate: vi.fn<FinancialReviewClient["validate"]>(),
    ...overrides,
  };
}

describe("FinancialSections", () => {
  it("renders grouped empty states, covenants, and read-only cash flows", () => {
    const data = workspace();
    data.institutions = [];
    data.cashFlows = [];
    data.validationIssues = [];
    data.validationSummary = { error: 0, info: 0, total: 0, warning: 0 };

    render(<FinancialSections workspace={data} mocked={false} />);

    expect(screen.getByText("Institutions")).toBeInTheDocument();
    expect(screen.getByText("Reporting Periods")).toBeInTheDocument();
    expect(screen.getByText("Cash Flows")).toBeInTheDocument();
    expect(screen.getByText("Covenants")).toBeInTheDocument();
    expect(screen.getByText("No institutions records.")).toBeInTheDocument();
    expect(
      screen.getByText(/intentionally keeps cash flows read-only/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No active validation issues."),
    ).toBeInTheDocument();
  });

  it("closes active mutation forms when demo mode activates", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add institution" }));
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(
      screen.getByRole("form", { name: "Add institution" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("form", { name: "Edit institution" }),
    ).toBeInTheDocument();

    rerender(
      <FinancialSections
        workspace={workspace()}
        mocked
        tenant={tenant}
        caseId="case-1"
        client={client()}
      />,
    );
    expect(screen.queryByRole("form")).not.toBeInTheDocument();

    rerender(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client()}
      />,
    );
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("constrains account and obligation statuses to contract values", async () => {
    const user = userEvent.setup();
    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add account" }));
    const accountForm = screen.getByRole("form", { name: "Add account" });
    expect(
      within(accountForm)
        .getAllByRole("option")
        .map((option) => option.getAttribute("value")),
    ).toEqual(["", "active", "inactive", "closed", "unknown"]);

    await user.click(screen.getByRole("button", { name: "Add obligation" }));
    const obligationForm = screen.getByRole("form", {
      name: "Add obligation",
    });
    expect(
      within(obligationForm)
        .getAllByRole("option")
        .map((option) => option.getAttribute("value")),
    ).toEqual([
      "",
      "active",
      "inactive",
      "closed",
      "matured",
      "defaulted",
      "unknown",
    ]);
  });

  it("navigates from a validation issue to the affected field", async () => {
    const user = userEvent.setup();
    render(<FinancialSections workspace={workspace()} mocked={false} />);

    await user.click(
      screen.getByRole("button", { name: /Confirm institution name/ }),
    );

    const cell = document.getElementById(
      "financial-financial_institutions-institution-1-name",
    );
    expect(cell).toHaveFocus();
    expect(cell).toHaveClass("bg-amber-100");
  });

  it("shows only open issues and summarizes the displayed issues", () => {
    const data = workspace();
    const issue = data.validationIssues[0];
    data.validationIssues = [
      issue,
      {
        ...issue,
        id: "issue-dismissed",
        severity: "error",
        status: "dismissed",
        message: "Dismissed institution issue",
      },
      {
        ...issue,
        id: "issue-resolved",
        severity: "info",
        status: "resolved",
        message: "Resolved institution issue",
        resolvedAt: issue.createdAt,
      },
    ];
    data.validationSummary = { error: 1, info: 1, total: 3, warning: 1 };

    render(<FinancialSections workspace={data} mocked={false} />);

    expect(
      screen.getByRole("button", { name: /Confirm institution name/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Dismissed institution issue"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Resolved institution issue"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("0 errors")).toBeInTheDocument();
    expect(screen.getByText("1 warnings")).toBeInTheDocument();
    expect(screen.getByText("0 info")).toBeInTheDocument();
    expect(screen.getByText("1 total")).toBeInTheDocument();
  });

  it("drills into source document and row metadata", async () => {
    const user = userEvent.setup();
    render(<FinancialSections workspace={workspace()} mocked={false} />);

    await user.click(screen.getByRole("button", { name: "Source (1)" }));

    const drilldown = screen.getByLabelText("Source traceability");
    expect(within(drilldown).getByText(/row 7/)).toBeInTheDocument();
    expect(within(drilldown).getByText(/Balance Sheet/)).toBeInTheDocument();
    expect(within(drilldown).getByText(/Northstar Bank/)).toBeInTheDocument();
  });

  it("renders manual edit audit history", () => {
    const data = workspace();
    data.manualEdits = [
      {
        id: "edit-1",
        organizationId: tenant.orgId,
        caseId: "case-1",
        recordTable: "financial_institutions",
        recordId: "institution-1",
        fieldName: "reference_code",
        previousValue: "OLD",
        newValue: "NSB",
        reason: "Matched audited statement",
        editedBy: "reviewer@example.com",
        createdAt: new Date("2026-07-13T12:00:00Z"),
      },
    ];

    render(<FinancialSections workspace={data} mocked={false} />);

    expect(screen.getByText("Manual edit history")).toBeInTheDocument();
    expect(screen.getByText("OLD")).toBeInTheDocument();
    expect(screen.getByText("Matched audited statement")).toBeInTheDocument();
    expect(screen.getByText("reviewer@example.com")).toBeInTheDocument();
  });

  it("requires a reason and applies refreshed validation after an inline correction", async () => {
    const user = userEvent.setup();
    const updated = {
      ...workspace().institutions[0],
      name: "Northstar Commercial Bank",
    };
    const response = {
      record: updated,
      validation: validation(),
    } as FinancialInstitutionMutationResponse;
    const update = vi
      .fn<FinancialReviewClient["update"]>()
      .mockResolvedValue(response);
    const onMutation = vi.fn<(updateWorkspace: WorkspaceUpdater) => void>();

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={onMutation}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit institution" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Northstar Commercial Bank");
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );
    expect(
      within(form).getByText(/non-empty reason is required/),
    ).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();

    await user.type(
      within(form).getByLabelText("Reason"),
      "Corrected against audited statement",
    );
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        "institution",
        tenant,
        "case-1",
        "institution-1",
        expect.objectContaining({
          name: "Northstar Commercial Bank",
          reason: "Corrected against audited statement",
        }),
      ),
    );
    const updateWorkspace = onMutation.mock.calls[0]?.[0];
    expect(updateWorkspace?.(workspace())).toEqual(
      expect.objectContaining({
        institutions: [
          expect.objectContaining({ name: "Northstar Commercial Bank" }),
        ],
        validationSummary: expect.objectContaining({ total: 0 }),
      }),
    );
    expect(
      screen.getByText(/Saved institution correction/),
    ).toBeInTheDocument();
  });

  it("prevents dismissing a form while its mutation is in flight", async () => {
    const user = userEvent.setup();
    const response = {
      record: {
        ...workspace().institutions[0],
        name: "Northstar Commercial Bank",
      },
      validation: validation(),
    } as FinancialInstitutionMutationResponse;
    let resolveUpdate!: (value: FinancialInstitutionMutationResponse) => void;
    const update = vi.fn<FinancialReviewClient["update"]>().mockReturnValue(
      new Promise<FinancialInstitutionMutationResponse>((resolve) => {
        resolveUpdate = resolve;
      }),
    );

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add institution" }));
    const addForm = screen.getByRole("form", { name: "Add institution" });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit institution" });
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(
      within(form).getByLabelText("Name"),
      "Northstar Commercial Bank",
    );
    await user.type(
      within(form).getByLabelText("Reason"),
      "Corrected against audited statement",
    );
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(within(form).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    for (const button of screen.getAllByRole("button", {
      name: "Add institution",
    })) {
      expect(button).toBeDisabled();
    }
    expect(
      within(addForm).getByRole("button", { name: "Add institution" }),
    ).toBeDisabled();
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(form).toBeInTheDocument();

    resolveUpdate(response);
    expect(
      await screen.findByText(/Saved institution correction/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Edit institution" })).toBeNull();
  });

  it("rejects unchanged corrections after a field is reverted", async () => {
    const user = userEvent.setup();
    const update = vi.fn<FinancialReviewClient["update"]>();

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit institution" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Temporary name");
    await user.clear(name);
    await user.type(name, " Northstar Bank ");
    await user.type(within(form).getByLabelText("Reason"), "Review complete");
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    expect(
      within(form).getByText(/Change at least one field/),
    ).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it("does not submit untouched fields changed by a workspace refresh", async () => {
    const user = userEvent.setup();
    const update = vi.fn<FinancialReviewClient["update"]>().mockResolvedValue({
      record: {
        ...workspace().institutions[0],
        name: "Northstar Commercial Bank",
        referenceCode: "CURRENT",
      },
      validation: validation(),
    } as FinancialInstitutionMutationResponse);
    const onMutation = vi.fn<(updateWorkspace: WorkspaceUpdater) => void>();
    const reviewClient = client({ update });
    const initialWorkspace = workspace();
    const { rerender } = render(
      <FinancialSections
        workspace={initialWorkspace}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={reviewClient}
        onMutation={onMutation}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit institution" });
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(
      within(form).getByLabelText("Name"),
      "Northstar Commercial Bank",
    );

    const refreshedWorkspace = workspace();
    refreshedWorkspace.institutions[0] = {
      ...refreshedWorkspace.institutions[0],
      referenceCode: "CURRENT",
    };
    rerender(
      <FinancialSections
        workspace={refreshedWorkspace}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={reviewClient}
        onMutation={onMutation}
      />,
    );

    await user.type(
      within(form).getByLabelText("Reason"),
      "Corrected against audited statement",
    );
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith(
      "institution",
      tenant,
      "case-1",
      "institution-1",
      {
        name: "Northstar Commercial Bank",
        reason: "Corrected against audited statement",
      },
    );
  });

  it("retries a failed workspace refresh without repeating the mutation", async () => {
    const user = userEvent.setup();
    const updated = {
      ...workspace().institutions[0],
      name: "Northstar Commercial Bank",
    };
    const update = vi.fn<FinancialReviewClient["update"]>().mockResolvedValue({
      record: updated,
      validation: validation(),
    } as FinancialInstitutionMutationResponse);
    const onMutation = vi
      .fn<(updateWorkspace: WorkspaceUpdater) => Promise<void>>()
      .mockRejectedValueOnce(new Error("Workspace refresh failed"))
      .mockResolvedValueOnce(undefined);

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={onMutation}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit institution" });
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(
      within(form).getByLabelText("Name"),
      "Northstar Commercial Bank",
    );
    await user.type(
      within(form).getByLabelText("Reason"),
      "Corrected against audited statement",
    );
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    expect(
      await within(form).findByText(/Change saved, but refresh failed/),
    ).toBeInTheDocument();
    expect(
      within(form).getByRole("button", { name: "Change saved" }),
    ).toBeDisabled();
    await user.click(
      within(form).getByRole("button", { name: "Retry refresh" }),
    );

    await waitFor(() => expect(onMutation).toHaveBeenCalledTimes(2));
    expect(update).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText(/Saved institution correction/),
    ).toBeInTheDocument();
  });

  it("preserves failed manual-entry input and retries successfully", async () => {
    const user = userEvent.setup();
    const covenantRecord = {
      id: "covenant-1",
      organizationId: tenant.orgId,
      caseId: "case-1",
      name: "Leverage ratio",
      metric: "debt_to_ebitda",
      operator: "lte",
      threshold: "3.5",
      actualValue: "3.2",
      complianceStatus: "compliant",
      obligationId: null,
      reportingPeriodId: null,
      metadata: {},
      reportingContext: {},
      sourceRecord: {},
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    const response = {
      record: covenantRecord,
      validation: validation(),
    } as FinancialCovenantMutationResponse;
    const create = vi
      .fn<FinancialReviewClient["create"]>()
      .mockRejectedValueOnce(new Error("Temporary server failure"))
      .mockResolvedValueOnce(response);
    const onMutation = vi.fn<(updateWorkspace: WorkspaceUpdater) => void>();

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ create })}
        onMutation={onMutation}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add covenant" }));
    const form = screen.getByRole("form", { name: "Add covenant" });
    await user.type(within(form).getByLabelText("Name"), "Leverage ratio");
    await user.type(within(form).getByLabelText("Metric"), "debt_to_ebitda");
    fireEvent.change(within(form).getByLabelText("Operator"), {
      target: { value: "lte" },
    });
    await user.type(within(form).getByLabelText("Threshold"), "3.5");
    await user.type(
      within(form).getByLabelText("Reason"),
      "Missing covenant from source mapping",
    );
    await user.click(
      within(form).getByRole("button", { name: "Add covenant" }),
    );

    expect(
      await within(form).findByText(/Temporary server failure/),
    ).toBeInTheDocument();
    expect(within(form).getByLabelText("Name")).toHaveValue("Leverage ratio");
    await user.click(within(form).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create).toHaveBeenLastCalledWith(
      "covenant",
      tenant,
      "case-1",
      expect.objectContaining({
        name: "Leverage ratio",
        reason: "Missing covenant from source mapping",
      }),
    );
    expect(onMutation.mock.calls[0]?.[0](workspace())).toEqual(
      expect.objectContaining({
        covenants: [expect.objectContaining({ id: "covenant-1" })],
      }),
    );
    expect(screen.getByText(/Added covenant/)).toBeInTheDocument();
  });

  it("submits date-only strings for financial dates", async () => {
    const user = userEvent.setup();
    const create = vi.fn<FinancialReviewClient["create"]>();

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ create })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Add reporting period" }),
    );
    const form = screen.getByRole("form", { name: "Add reporting period" });
    await user.selectOptions(
      within(form).getByLabelText("Period type"),
      "year",
    );
    fireEvent.change(within(form).getByLabelText("Start date"), {
      target: { value: "2025-01-01" },
    });
    fireEvent.change(within(form).getByLabelText("End date"), {
      target: { value: "2025-12-31" },
    });
    fireEvent.change(within(form).getByLabelText("As-of date"), {
      target: { value: "2025-12-31" },
    });
    await user.type(
      within(form).getByLabelText("Reason"),
      "Add audited reporting period",
    );
    await user.click(
      within(form).getByRole("button", { name: "Add reporting period" }),
    );

    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(create).toHaveBeenCalledWith(
      "reportingPeriod",
      tenant,
      "case-1",
      expect.objectContaining({
        periodType: "year",
        startDate: "2025-01-01",
        endDate: "2025-12-31",
        asOfDate: "2025-12-31",
      }),
    );
  });

  it("sends null only for optional fields intentionally cleared", async () => {
    const user = userEvent.setup();
    const response = {
      record: { ...workspace().institutions[0], referenceCode: null },
      validation: validation(),
    } as FinancialInstitutionMutationResponse;
    const update = vi
      .fn<FinancialReviewClient["update"]>()
      .mockResolvedValue(response);

    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit institution" });
    await user.clear(within(form).getByLabelText("Reference code"));
    await user.type(within(form).getByLabelText("Reason"), "Remove bad code");
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const payload = vi.mocked(update).mock.calls[0]?.[4] as unknown as Record<
      string,
      unknown
    >;
    expect(payload).toMatchObject({
      referenceCode: null,
      reason: "Remove bad code",
    });
    expect(payload.name).toBeUndefined();
  });

  it("omits untouched covenant compliance when inputs change", async () => {
    const user = userEvent.setup();
    const data = workspace();
    const covenant = {
      id: "covenant-1",
      organizationId: tenant.orgId,
      caseId: "case-1",
      name: "Leverage ratio",
      metric: "debt_to_ebitda",
      operator: "lte" as const,
      threshold: "3.5",
      actualValue: "3.2",
      complianceStatus: "compliant" as const,
      obligationId: null,
      reportingPeriodId: null,
      metadata: {},
      reportingContext: {},
      sourceRecord: {},
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    data.covenants = [covenant];
    const response = {
      record: { ...covenant, threshold: "3.0" },
      validation: validation(),
    } as FinancialCovenantMutationResponse;
    const update = vi
      .fn<FinancialReviewClient["update"]>()
      .mockResolvedValue(response);

    render(
      <FinancialSections
        workspace={data}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    const section = screen.getByText("Covenants").closest("section")!;
    await user.click(within(section).getByRole("button", { name: "Edit" }));
    const form = within(section).getByRole("form", { name: "Edit covenant" });
    await user.clear(within(form).getByLabelText("Threshold"));
    await user.type(within(form).getByLabelText("Threshold"), "3.0");
    await user.type(within(form).getByLabelText("Reason"), "Correct threshold");
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const payload = vi.mocked(update).mock.calls[0]?.[4] as unknown as Record<
      string,
      unknown
    >;
    expect(payload).toMatchObject({
      threshold: "3",
      reason: "Correct threshold",
    });
    expect(payload.complianceStatus).toBeUndefined();
  });

  it("submits an explicitly corrected covenant compliance status", async () => {
    const user = userEvent.setup();
    const data = workspace();
    const covenant = {
      id: "covenant-1",
      organizationId: tenant.orgId,
      caseId: "case-1",
      name: "Leverage ratio",
      metric: "debt_to_ebitda",
      operator: "lte" as const,
      threshold: "3.5",
      actualValue: "3.2",
      complianceStatus: "compliant" as const,
      obligationId: null,
      reportingPeriodId: null,
      metadata: {},
      reportingContext: {},
      sourceRecord: {},
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    data.covenants = [covenant];
    const update = vi.fn<FinancialReviewClient["update"]>().mockResolvedValue({
      record: { ...covenant, complianceStatus: "non_compliant" },
      validation: validation(),
    } as FinancialCovenantMutationResponse);

    render(
      <FinancialSections
        workspace={data}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    const section = screen.getByText("Covenants").closest("section")!;
    await user.click(within(section).getByRole("button", { name: "Edit" }));
    const form = within(section).getByRole("form", { name: "Edit covenant" });
    await user.selectOptions(
      within(form).getByLabelText("Compliance"),
      "non_compliant",
    );
    await user.type(within(form).getByLabelText("Reason"), "Override status");
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update).toHaveBeenCalledWith(
      "covenant",
      tenant,
      "case-1",
      "covenant-1",
      expect.objectContaining({
        complianceStatus: "non_compliant",
        reason: "Override status",
      }),
    );
  });

  it("requests automatic covenant recalculation without sending a status", async () => {
    const user = userEvent.setup();
    const data = workspace();
    const covenant = {
      id: "covenant-1",
      organizationId: tenant.orgId,
      caseId: "case-1",
      name: "Leverage ratio",
      metric: "debt_to_ebitda",
      operator: "lte" as const,
      threshold: "3.5",
      actualValue: "3.2",
      complianceStatus: "compliant" as const,
      obligationId: null,
      reportingPeriodId: null,
      metadata: {},
      reportingContext: {},
      sourceRecord: {},
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    data.covenants = [covenant];
    const update = vi.fn<FinancialReviewClient["update"]>().mockResolvedValue({
      record: { ...covenant, complianceStatus: "compliant" },
      validation: validation(),
    } as FinancialCovenantMutationResponse);

    render(
      <FinancialSections
        workspace={data}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ update })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    const section = screen.getByText("Covenants").closest("section")!;
    await user.click(within(section).getByRole("button", { name: "Edit" }));
    const form = within(section).getByRole("form", { name: "Edit covenant" });
    const complianceSelect = within(form).getByLabelText("Compliance");
    expect(
      within(complianceSelect).queryByRole("option", { name: "Select…" }),
    ).not.toBeInTheDocument();
    await user.selectOptions(complianceSelect, "__automatic__");
    await user.type(
      within(form).getByLabelText("Reason"),
      "Recalculate from corrected values",
    );
    await user.click(
      within(form).getByRole("button", { name: "Save correction" }),
    );

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const payload = vi.mocked(update).mock.calls[0]?.[4] as unknown as Record<
      string,
      unknown
    >;
    expect(payload.reason).toBe("Recalculate from corrected values");
    expect(payload.actualValue).toBe("3.2");
    expect(payload.complianceStatus).toBeUndefined();
  });

  it("includes obligation start date in manual entry", async () => {
    const user = userEvent.setup();
    const create = vi.fn<FinancialReviewClient["create"]>();
    render(
      <FinancialSections
        workspace={workspace()}
        mocked={false}
        tenant={tenant}
        caseId="case-1"
        client={client({ create })}
        onMutation={vi.fn<(updateWorkspace: WorkspaceUpdater) => void>()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add obligation" }));
    const form = screen.getByRole("form", { name: "Add obligation" });
    await user.type(
      within(form).getByLabelText("Obligation type"),
      "term_loan",
    );
    fireEvent.change(within(form).getByLabelText("Start date"), {
      target: { value: "2025-01-01" },
    });
    await user.type(within(form).getByLabelText("Reason"), "Add missing loan");
    await user.click(
      within(form).getByRole("button", { name: "Add obligation" }),
    );

    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    expect(create).toHaveBeenCalledWith(
      "obligation",
      tenant,
      "case-1",
      expect.objectContaining({ startDate: "2025-01-01" }),
    );
  });
});
