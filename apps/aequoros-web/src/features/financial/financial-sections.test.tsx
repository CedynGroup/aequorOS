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

  it("drills into source document and row metadata", async () => {
    const user = userEvent.setup();
    render(<FinancialSections workspace={workspace()} mocked={false} />);

    await user.click(screen.getByRole("button", { name: "Source (1)" }));

    const drilldown = screen.getByLabelText("Source traceability");
    expect(within(drilldown).getByText(/row 7/)).toBeInTheDocument();
    expect(within(drilldown).getByText(/Balance Sheet/)).toBeInTheDocument();
    expect(within(drilldown).getByText(/Northstar Bank/)).toBeInTheDocument();
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
    const onMutation = vi.fn<(workspace: FinancialDataWorkspaceRead) => void>();

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
    expect(onMutation).toHaveBeenCalledWith(
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
    const onMutation = vi.fn<(workspace: FinancialDataWorkspaceRead) => void>();

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
    expect(onMutation).toHaveBeenCalledWith(
      expect.objectContaining({
        covenants: [expect.objectContaining({ id: "covenant-1" })],
      }),
    );
    expect(screen.getByText(/Added covenant/)).toBeInTheDocument();
  });
});
