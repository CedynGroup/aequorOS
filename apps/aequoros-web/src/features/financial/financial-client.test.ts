import { beforeEach, describe, expect, it, vi } from "vitest";

import { financialReviewClient } from "./financial-client";

const tenant = { orgId: "org-1", userId: "user-1" };
const now = "2026-07-13T12:00:00Z";

function validation() {
  return {
    organization_id: tenant.orgId,
    case_id: "case-1",
    issue_count: 0,
    issues: [],
    summary: { error: 0, warning: 0, info: 0, total: 0 },
  };
}

function institution(name: string) {
  return {
    id: "institution-1",
    organization_id: tenant.orgId,
    case_id: "case-1",
    name,
    institution_type: "bank",
    reference_code: "NSB",
    metadata: {},
    created_at: now,
    updated_at: now,
  };
}

describe("generated financial client integration", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("loads the workspace through FinancialDataApi with tenant headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          organization_id: tenant.orgId,
          case_id: "case-1",
          institutions: [],
          accounts: [],
          reporting_periods: [],
          balances: [],
          cash_flows: [],
          covenants: [],
          obligations: [],
          source_rows: [],
          record_source_links: [],
          manual_edits: [],
          validation_issues: [],
          validation_summary: { error: 0, warning: 0, info: 0, total: 0 },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await financialReviewClient.workspace(tenant, "case-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      "http://127.0.0.1:8003/api/v1/cases/case-1/financial-workspace",
    );
    expect(init?.headers).toMatchObject({
      "X-Org-Id": tenant.orgId,
      "X-User-Id": tenant.userId,
    });
  });

  it("uses generated serialization and mutation-response decoding", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          record: institution("Northstar Bank"),
          validation: validation(),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const response = await financialReviewClient.create(
      "institution",
      tenant,
      "case-1",
      {
        name: "Northstar Bank",
        institutionType: "bank",
        referenceCode: "NSB",
        reason: "Missing source mapping",
      },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(
      String(url).endsWith(
        "/api/v1/cases/case-1/financial-workspace/institutions",
      ),
    ).toBe(true);
    expect(JSON.parse(String(init?.body))).toEqual({
      institution_type: "bank",
      name: "Northstar Bank",
      reason: "Missing source mapping",
      reference_code: "NSB",
    });
    expect(response.record).toMatchObject({
      name: "Northstar Bank",
      createdAt: new Date(now),
    });
    expect(response.validation.summary.total).toBe(0);
  });

  it("uses the canonical cash-flow mutation contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          record: {
            id: "cash-flow-1",
            organization_id: tenant.orgId,
            case_id: "case-1",
            account_id: null,
            reporting_period_id: null,
            cash_flow_date: "2026-07-01",
            amount: "2750.00",
            currency: "USD",
            direction: "outflow",
            category: "supplier payment",
            metadata: { provenance: "manual" },
            created_at: now,
            updated_at: now,
          },
          validation: validation(),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const response = await financialReviewClient.create(
      "cashFlow",
      tenant,
      "case-1",
      {
        cashFlowDate: "2026-07-01",
        amount: "2750.00",
        currency: "USD",
        direction: "outflow",
        category: "supplier payment",
        reason: "Missing bank statement row",
      },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain(
      "/api/v1/cases/case-1/financial-workspace/cash-flows",
    );
    expect(JSON.parse(String(init?.body))).toEqual({
      cash_flow_date: "2026-07-01",
      amount: "2750.00",
      currency: "USD",
      direction: "outflow",
      category: "supplier payment",
      reason: "Missing bank statement row",
    });
    expect(response.record).toMatchObject({
      id: "cash-flow-1",
      cashFlowDate: new Date("2026-07-01"),
      metadata: { provenance: "manual" },
    });
  });
});
