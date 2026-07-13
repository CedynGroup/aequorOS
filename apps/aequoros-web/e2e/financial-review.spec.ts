import { expect, test, type Page, type Route } from "playwright/test";

import { demoTenant, northstarCase } from "./support/test-data";

const now = "2026-07-13T12:00:00Z";
const documentId = "70000000-0000-4000-8000-000000000001";
const institutionId = "71000000-0000-4000-8000-000000000001";
const evidenceDir = process.env.NO_MISTAKES_EVIDENCE_DIR;

async function captureEvidence(page: Page, name: string) {
  if (!evidenceDir) return;
  await page.screenshot({ path: `${evidenceDir}/${name}.png`, fullPage: true });
}

type MockState = {
  institutionName: string;
  issueActive: boolean;
  correctionAttempts: number;
  covenantAdded: boolean;
  mappingComplete: boolean;
};

function issueJson() {
  return {
    id: "issue-1",
    organization_id: demoTenant.orgId,
    case_id: northstarCase.id,
    issue_key: "institution_name",
    severity: "warning",
    status: "open",
    rule_id: "institution_name_review",
    code: null,
    message: "Confirm institution name against the audited statement",
    field_name: "name",
    field: "name",
    entity_type: "institution",
    entity_id: institutionId,
    record_table: "financial_institutions",
    record_id: institutionId,
    details: {},
    resolved_at: null,
    created_at: now,
  };
}

function institutionJson(name: string) {
  return {
    id: institutionId,
    organization_id: demoTenant.orgId,
    case_id: northstarCase.id,
    name,
    institution_type: "bank",
    reference_code: "NSB",
    metadata: {},
    created_at: now,
    updated_at: now,
  };
}

function validationJson(issueActive: boolean) {
  return {
    organization_id: demoTenant.orgId,
    case_id: northstarCase.id,
    issue_count: issueActive ? 1 : 0,
    issues: issueActive ? [issueJson()] : [],
    summary: {
      error: 0,
      warning: issueActive ? 1 : 0,
      info: 0,
      total: issueActive ? 1 : 0,
    },
  };
}

function workspaceJson(state: MockState) {
  return {
    organization_id: demoTenant.orgId,
    case_id: northstarCase.id,
    institutions: [institutionJson(state.institutionName)],
    accounts: [],
    reporting_periods: [],
    balances: [],
    cash_flows: [
      {
        id: "cash-flow-1",
        organization_id: demoTenant.orgId,
        case_id: northstarCase.id,
        account_id: null,
        reporting_period_id: null,
        cash_flow_date: "2026-06-30",
        amount: "1250.00",
        currency: "USD",
        direction: "inflow",
        category: "receipts",
        metadata: {},
        created_at: now,
        updated_at: now,
      },
    ],
    covenants: state.covenantAdded ? [covenantJson()] : [],
    obligations: [],
    source_rows: [
      {
        id: "source-1",
        organization_id: demoTenant.orgId,
        case_id: northstarCase.id,
        document_id: documentId,
        document_extraction_id: "extraction-1",
        row_index: 4,
        locator: { sheet: "Balance Sheet", cell: "B4" },
        raw_payload: { name: "Northstar Bank" },
        created_at: now,
      },
      ...(state.mappingComplete
        ? [
            {
              id: "source-unmapped",
              organization_id: demoTenant.orgId,
              case_id: northstarCase.id,
              document_id: documentId,
              document_extraction_id: "extraction-1",
              row_index: 9,
              locator: { sheet: "Debt", cell: "A9" },
              raw_payload: { obligation_type: "term_loan" },
              created_at: now,
            },
          ]
        : []),
    ],
    record_source_links: [
      {
        id: "link-1",
        organization_id: demoTenant.orgId,
        case_id: northstarCase.id,
        record_table: "financial_institutions",
        record_id: institutionId,
        field_name: "name",
        source_row_id: "source-1",
        source_field: "name",
        confidence: "high",
        metadata: {},
        created_at: now,
      },
    ],
    manual_edits: [],
    validation_issues: state.issueActive ? [issueJson()] : [],
    validation_summary: validationJson(state.issueActive).summary,
  };
}

function covenantJson() {
  return {
    id: "covenant-1",
    organization_id: demoTenant.orgId,
    case_id: northstarCase.id,
    name: "Leverage ratio",
    metric: "debt_to_ebitda",
    operator: "lte",
    threshold: "3.5",
    actual_value: null,
    compliance_status: "unknown",
    obligation_id: null,
    reporting_period_id: null,
    metadata: {},
    reporting_context: {},
    source_record: {},
    created_at: now,
    updated_at: now,
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installFinancialBackend(page: Page) {
  const state: MockState = {
    institutionName: "Northstar Bank",
    issueActive: true,
    correctionAttempts: 0,
    covenantAdded: false,
    mappingComplete: false,
  };

  await page.route("http://127.0.0.1:8003/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/v1/taxonomies/cases")
      return json(route, {
        statuses: ["open"],
        risk_levels: ["medium"],
        decisions: [],
        sort_options: ["updated_at_desc"],
      });
    if (path === "/api/v1/cases" && method === "GET")
      return json(route, {
        items: [queueCaseJson()],
        total: 1,
        limit: 12,
        offset: 0,
        page: 1,
        pages: 1,
        has_more: false,
      });
    if (
      path === `/api/v1/cases/${northstarCase.id}/financial-workspace` &&
      method === "GET"
    )
      return json(route, workspaceJson(state));
    if (
      path === `/api/v1/cases/${northstarCase.id}/financial-workspace/map` &&
      method === "POST"
    ) {
      state.mappingComplete = true;
      return json(route, {
        organization_id: demoTenant.orgId,
        case_id: northstarCase.id,
        document_id: documentId,
        document_extraction_id: "extraction-1",
        created: { institutions: 1 },
        reused: {},
        summary: {
          source_row_count: 2,
          mapped_source_row_count: 1,
          unmapped_source_row_count: 1,
        },
        unmapped_rows: [
          {
            source_row_id: "source-unmapped",
            row_index: 9,
            reason: "No canonical record matched",
            locator: { sheet: "Debt", cell: "A9" },
          },
        ],
      });
    }
    if (
      path === `/api/v1/cases/${northstarCase.id}/financial-data/validate` &&
      method === "POST"
    )
      return json(route, validationJson(state.issueActive));
    if (
      path ===
        `/api/v1/cases/${northstarCase.id}/financial-workspace/institutions/${institutionId}` &&
      method === "PATCH"
    ) {
      state.correctionAttempts += 1;
      if (state.correctionAttempts === 1)
        return json(
          route,
          {
            error: {
              code: "temporary_failure",
              message: "Temporary mutation failure",
              details: {},
            },
          },
          503,
        );
      const payload = request.postDataJSON() as {
        name: string;
        reason: string;
      };
      expect(payload.reason).toBe("Verified against audited statement");
      state.institutionName = payload.name;
      state.issueActive = false;
      return json(route, {
        record: institutionJson(state.institutionName),
        validation: validationJson(false),
      });
    }
    if (
      path ===
        `/api/v1/cases/${northstarCase.id}/financial-workspace/covenants` &&
      method === "POST"
    ) {
      const payload = request.postDataJSON() as { reason: string };
      expect(payload.reason).toBe("Add missing covenant");
      state.covenantAdded = true;
      return json(route, {
        record: covenantJson(),
        validation: validationJson(false),
      });
    }
    if (
      path ===
        `/api/v1/cases/${northstarCase.id}/financial-workspace/covenants/covenant-1` &&
      method === "PATCH"
    ) {
      const payload = request.postDataJSON() as Record<string, unknown>;
      expect(payload.reason).toBe("Recalculate covenant compliance");
      expect(payload).toHaveProperty("actual_value", null);
      expect(payload).not.toHaveProperty("compliance_status");
      return json(route, {
        record: covenantJson(),
        validation: validationJson(false),
      });
    }
    if (path === "/api/v1/documents/upload-request" && method === "POST")
      return json(route, {
        document_id: documentId,
        upload_url: "https://uploads.example/document",
        method: "PUT",
        headers: { "Content-Type": "application/pdf" },
        expires_in_seconds: 900,
      });
    if (
      path === `/api/v1/documents/${documentId}/complete-upload` &&
      method === "POST"
    )
      return json(route, { document_id: documentId, status: "uploaded" });
    if (
      path === `/api/v1/cases/${northstarCase.id}/documents` &&
      method === "GET"
    )
      return json(route, []);
    if (path === `/api/v1/cases/${northstarCase.id}` && method === "GET")
      return json(route, caseJson());
    return json(
      route,
      {
        error: {
          code: "not_found",
          message: `Unhandled mock route: ${method} ${path}`,
          details: {},
        },
      },
      404,
    );
  });
  return state;
}

function queueCaseJson() {
  return {
    id: northstarCase.id,
    organization_id: demoTenant.orgId,
    title: northstarCase.title,
    case_type: "financial_statement_review",
    subject_name: "Northstar Foods",
    subject_type: "company",
    status: "open",
    risk_level: "medium",
    risk_score: 55,
    decision: null,
    assigned_to_user_id: demoTenant.userId,
    assignee_display_name: "Reviewer",
    assignee_email: "reviewer@example.com",
    findings_count: 0,
    open_findings_count: 0,
    created_at: now,
    updated_at: now,
  };
}

function caseJson() {
  return {
    ...queueCaseJson(),
    description: "Financial review case",
    metadata: {},
    created_by: demoTenant.userId,
    assigned_at: now,
    decided_at: null,
    scored_at: now,
    scoring_version: "v1",
    archived_at: null,
  };
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((tenant) => {
    localStorage.setItem("aequoros.orgId", tenant.orgId);
    localStorage.setItem("aequoros.userId", tenant.userId);
  }, demoTenant);
  await installFinancialBackend(page);
});

test("reviews validation and drills into mapped source metadata", async ({
  page,
}) => {
  await page.goto(`/cases/${northstarCase.id}?tab=financial&archived=false`);

  await page.getByRole("button", { name: /Confirm institution name/ }).click();
  await expect(
    page.locator(
      "#financial-financial_institutions-71000000-0000-4000-8000-000000000001-name",
    ),
  ).toBeFocused();
  await page.getByRole("button", { name: "Source (1)" }).click();
  await expect(page.getByLabel("Source traceability")).toContainText("row 4");
  await expect(page.getByLabel("Source traceability")).toContainText(
    "Balance Sheet",
  );
  await expect(
    page.getByText(/intentionally keeps cash flows read-only/),
  ).toBeVisible();
  await captureEvidence(page, "financial-review-source-traceability");

  await page.getByRole("button", { name: "Demo seed data" }).click();
  await expect(page.getByText("Editing disabled for demo data")).toBeVisible();
  await expect(
    page.getByLabel("Map and validate financial data"),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add institution" }),
  ).not.toBeVisible();
  await captureEvidence(page, "financial-review-demo-read-only");
});

test("uploads, maps, validates, retries correction, revalidates, and manually adds a covenant", async ({
  page,
}) => {
  await page.goto(`/cases/${northstarCase.id}?tab=documents&archived=false`);
  await page.getByRole("button", { name: "Create request" }).click();
  await expect(page.getByText("Upload request", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Complete upload" }).click();

  await page.getByRole("tab", { name: "Financial Workspace" }).click();
  await page.getByLabel("Document ID").fill(documentId);
  await page.getByRole("button", { name: "Map financial data" }).click();
  await expect(
    page.getByText(/Mapping complete: 1 of 2 source rows mapped/),
  ).toBeVisible();
  await expect(page.getByLabel("Unmapped source rows")).toContainText("Row 9");
  await expect(page.getByLabel("Unmapped source rows")).toContainText(
    "term_loan",
  );

  await page.getByRole("tab", { name: "Overview" }).click();
  await page.getByRole("tab", { name: "Financial Workspace" }).click();
  await expect(page.getByLabel("Unmapped source rows")).toContainText("Row 9");
  await page.reload();
  await expect(page.getByLabel("Unmapped source rows")).toContainText(
    "term_loan",
  );
  await captureEvidence(page, "financial-review-unmapped-row");
  await page.getByRole("button", { name: "Revalidate" }).click();
  await expect(page.getByText(/Validation refreshed: 1 issues/)).toBeVisible();

  await page.getByRole("button", { name: "Edit" }).click();
  const editForm = page.getByRole("form", { name: "Edit institution" });
  await editForm.getByLabel("Name").fill("Northstar Commercial Bank");
  await editForm
    .getByLabel("Reason")
    .fill("Verified against audited statement");
  await editForm.getByRole("button", { name: "Save correction" }).click();
  await expect(editForm.getByText(/Temporary mutation failure/)).toBeVisible();
  await expect(editForm.getByLabel("Name")).toHaveValue(
    "Northstar Commercial Bank",
  );
  await captureEvidence(page, "financial-review-recoverable-error");
  await editForm.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText(/Saved institution correction/)).toBeVisible();
  await expect(page.getByText("Northstar Commercial Bank")).toBeVisible();

  await page.getByRole("button", { name: "Revalidate" }).click();
  await expect(page.getByText(/Validation refreshed: 0 issues/)).toBeVisible();
  await page.getByRole("button", { name: "Add covenant" }).click();
  const covenantForm = page.getByRole("form", { name: "Add covenant" });
  await covenantForm.getByLabel("Name").fill("Leverage ratio");
  await covenantForm.getByLabel("Metric").fill("debt_to_ebitda");
  await covenantForm.getByLabel("Operator").selectOption("lte");
  await covenantForm.getByLabel("Threshold").fill("3.5");
  await covenantForm.getByLabel("Reason").fill("Add missing covenant");
  await covenantForm.getByRole("button", { name: "Add covenant" }).click();
  await expect(page.getByText(/Added covenant/)).toBeVisible();
  await expect(page.getByText("Leverage ratio")).toBeVisible();

  const covenantSection = page.locator(
    "#financial-section-financial_covenants",
  );
  await covenantSection.getByRole("button", { name: "Edit" }).click();
  const covenantEditForm = covenantSection.getByRole("form", {
    name: "Edit covenant",
  });
  await covenantEditForm.getByLabel("Compliance").selectOption("__automatic__");
  await covenantEditForm
    .getByLabel("Reason")
    .fill("Recalculate covenant compliance");
  await covenantEditForm
    .getByRole("button", { name: "Save correction" })
    .click();
  await expect(page.getByText(/Saved covenant correction/)).toBeVisible();
  await captureEvidence(page, "financial-review-completed-covenant");
});
