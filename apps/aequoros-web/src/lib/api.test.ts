import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "./constants";
import { apiJson, isApiError, riskApi, type TenantHeaders } from "./api";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

type FetchSpy = {
  mock: {
    calls: Array<Parameters<typeof fetch>>;
  };
};

function firstFetchCall(fetchMock: FetchSpy) {
  const call = fetchMock.mock.calls[0];
  expect(call).toBeDefined();
  return call;
}

function requestHeaders(init: RequestInit | undefined) {
  expect(init).toBeDefined();
  return init!.headers as Record<string, string>;
}

function requestJsonBody(init: RequestInit | undefined) {
  expect(init).toBeDefined();
  return JSON.parse(String(init!.body));
}

describe("risk API helpers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("attaches tenant and user headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 12, offset: 0, page: 1, pages: 0, has_more: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await riskApi.listCases(tenant, {
      includeArchived: false,
      limit: 12,
      offset: 0,
    });

    const [, init] = firstFetchCall(fetchMock);
    const headers = requestHeaders(init);
    expect(headers["X-Org-Id"]).toBe(DEFAULT_ORG_ID);
    expect(headers["X-User-Id"]).toBe(DEFAULT_USER_ID);
  });

  it("uses the single report endpoint with HTML Accept header", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("<main>Report</main>", { status: 200 }));

    await riskApi.reportHtml(tenant, "case-1");

    const [url, init] = firstFetchCall(fetchMock);
    expect(String(url)).toContain("/cases/case-1/report");
    expect(String(url)).not.toContain("report.html");
    expect(requestHeaders(init).Accept).toBe("text/html");
  });

  it("serializes bulk assign payloads with API field names", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ succeeded: [], failed: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await riskApi.bulkCases(tenant, {
      action: "assign",
      caseIds: ["90000000-0000-4000-8000-000000000003"],
      assignedToUserId: DEFAULT_USER_ID,
    });

    const [, init] = firstFetchCall(fetchMock);
    expect(requestJsonBody(init)).toEqual({
      action: "assign",
      case_ids: ["90000000-0000-4000-8000-000000000003"],
      assigned_to_user_id: DEFAULT_USER_ID,
    });
  });

  it("serializes decision submissions with API field names", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "decision-1",
          case_id: "case-1",
          organization_id: DEFAULT_ORG_ID,
          decision: "approved",
          previous_decision: null,
          reason: "Ready for approval",
          decided_by: DEFAULT_USER_ID,
          created_at: new Date().toISOString(),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await riskApi.createDecision(tenant, "case-1", {
      decision: "approved",
      reason: "Ready for approval",
    });

    const [url, init] = firstFetchCall(fetchMock);
    expect(String(url)).toContain("/cases/case-1/decisions");
    expect(requestJsonBody(init)).toEqual({
      decision: "approved",
      reason: "Ready for approval",
    });
  });

  it("serializes document upload requests with API field names", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          document_id: "document-1",
          upload_url: "https://storage.example/upload",
          method: "PUT",
          headers: { "Content-Type": "application/pdf" },
          expires_in_seconds: 900,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await riskApi.requestUpload(tenant, {
      caseId: "case-1",
      filename: "borrowing-base.pdf",
      contentType: "application/pdf",
      byteSize: 1200,
      sha256: "abc123",
    });

    const [url, init] = firstFetchCall(fetchMock);
    expect(String(url)).toContain("/documents/upload-request");
    expect(requestJsonBody(init)).toEqual({
      case_id: "case-1",
      filename: "borrowing-base.pdf",
      content_type: "application/pdf",
      byte_size: 1200,
      sha256: "abc123",
    });
  });

  it("serializes finding status updates with API field names", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "finding-1",
          organization_id: DEFAULT_ORG_ID,
          case_id: "case-1",
          assessment_id: null,
          run_id: null,
          risk_type: "manual_review",
          title: "Finding",
          summary: "Summary",
          rationale: null,
          severity: "medium",
          likelihood: null,
          impact: null,
          confidence: null,
          status: "resolved",
          disposition_reason: "Reviewer accepted mitigation",
          source: "manual",
          rule_id: null,
          rule_version: null,
          score_impact: null,
          details: {},
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await riskApi.updateFinding(tenant, "finding-1", {
      status: "resolved",
      dispositionReason: "Reviewer accepted mitigation",
    });

    const [url, init] = firstFetchCall(fetchMock);
    expect(String(url)).toContain("/findings/finding-1");
    expect(requestJsonBody(init)).toEqual({
      status: "resolved",
      disposition_reason: "Reviewer accepted mitigation",
    });
  });

  it("serializes manual cash-flow create and correction payloads", async () => {
    const responseBody = {
      id: "cash-flow-1",
      organization_id: DEFAULT_ORG_ID,
      case_id: "case-1",
      account_id: null,
      reporting_period_id: null,
      cash_flow_date: "2026-04-15",
      amount: "1000.0000",
      currency: "GHS",
      direction: "inflow",
      category: "customer deposit",
      metadata: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...responseBody, amount: "1250.0000" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    await riskApi.createFinancialCashFlow(tenant, "case-1", {
      amount: "1000",
      cashFlowDate: new Date("2026-04-15T00:00:00Z"),
      currency: "GHS",
      direction: "inflow",
      category: "customer deposit",
    });
    await riskApi.updateFinancialCashFlow(tenant, "case-1", "cash-flow-1", {
      amount: "1250",
      reason: "Reviewer correction",
    });

    const [createUrl, createInit] = fetchMock.mock.calls[0];
    const [updateUrl, updateInit] = fetchMock.mock.calls[1];
    expect(String(createUrl)).toContain("/cases/case-1/financial-workspace/cash-flows");
    expect(requestJsonBody(createInit)).toMatchObject({
      amount: "1000",
      cash_flow_date: "2026-04-15",
      currency: "GHS",
      direction: "inflow",
      category: "customer deposit",
    });
    expect(String(updateUrl)).toContain(
      "/cases/case-1/financial-workspace/cash-flows/cash-flow-1",
    );
    expect(requestJsonBody(updateInit)).toMatchObject({
      amount: "1250",
      reason: "Reviewer correction",
    });
  });

  it("normalizes API error envelopes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "not_found",
            message: "Case not found",
            details: {},
          },
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      apiJson("/cases/missing", tenant, (json) => json),
    ).rejects.toMatchObject({
      statusCode: 404,
      code: "not_found",
      message: "Case not found",
    });
  });

  it("detects normalized API errors", () => {
    expect(isApiError({ statusCode: 409, code: "conflict", message: "Conflict" })).toBe(true);
    expect(isApiError(new Error("Nope"))).toBe(false);
  });
});
