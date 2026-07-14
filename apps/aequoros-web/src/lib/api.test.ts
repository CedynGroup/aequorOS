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
      new Response(
        JSON.stringify({
          items: [],
          total: 0,
          limit: 12,
          offset: 0,
          page: 1,
          pages: 0,
          has_more: false,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
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

  it("uses case-scoped scenario endpoints and serializes assumption updates", async () => {
    const responseBody = {
      scenario: {
        id: "scenario-1",
        organization_id: DEFAULT_ORG_ID,
        case_id: "case-1",
        name: "Baseline",
        description: null,
        scenario_type: "baseline",
        copied_from_scenario_id: null,
        created_by: DEFAULT_USER_ID,
        archived_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        assumptions: [],
      },
      validation: {
        scenario_id: "scenario-1",
        complete: false,
        issue_count: 1,
        issues: [],
      },
      readiness: {
        case_id: "case-1",
        ready: false,
        scenario_count: 1,
        complete_scenario_count: 0,
        incomplete_scenario_ids: ["scenario-1"],
      },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await riskApi.updateAssumption(
      tenant,
      "case-1",
      "scenario-1",
      "assumption-1",
      { value: 0.05, reason: "Reviewer update" },
    );
    await riskApi.updateScenario(tenant, "case-1", "scenario-1", {
      name: "Operating plan",
      description: "Approved management case",
      reason: "Reviewer update",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain(
      "/cases/case-1/scenarios/scenario-1/assumptions/assumption-1",
    );
    expect(init?.method).toBe("PATCH");
    expect(requestJsonBody(init)).toEqual({
      reason: "Reviewer update",
      value: 0.05,
    });
    const [scenarioUrl, scenarioInit] = fetchMock.mock.calls[1];
    expect(String(scenarioUrl)).toContain("/cases/case-1/scenarios/scenario-1");
    expect(scenarioInit?.method).toBe("PATCH");
    expect(requestJsonBody(scenarioInit)).toEqual({
      description: "Approved management case",
      name: "Operating plan",
      reason: "Reviewer update",
    });
  });

  it("uses case-scoped calculation run and rerun contracts", async () => {
    const responseBody = {
      id: "run-1",
      organization_id: DEFAULT_ORG_ID,
      case_id: "case-1",
      scenario_id: "scenario-1",
      rerun_of_run_id: null,
      status: "failed",
      engine_version: "balance-sheet-v1.0.0",
      input_schema_version: "calculation-input-v1",
      output_schema_version: "balance-sheet-output-v1",
      input_hash: "a".repeat(64),
      inputs: {},
      forecast_periods: 3,
      as_of_date: "2026-06-30",
      started_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      error: { code: "scenario_not_ready", message: "Review inputs" },
      outputs: [],
      created_by: DEFAULT_USER_ID,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(responseBody), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const started = await riskApi.startCalculation(tenant, "case-1", {
      scenarioId: "scenario-1",
      forecastPeriods: 3,
    });
    await riskApi.rerunCalculation(tenant, "case-1", "run-1");

    expect(started.error?.code).toBe("scenario_not_ready");
    const [startUrl, startInit] = fetchMock.mock.calls[0];
    expect(String(startUrl)).toContain("/cases/case-1/calculation-runs");
    expect(requestJsonBody(startInit)).toEqual({
      forecast_periods: 3,
      scenario_id: "scenario-1",
    });
    const [rerunUrl, rerunInit] = fetchMock.mock.calls[1];
    expect(String(rerunUrl)).toContain(
      "/cases/case-1/calculation-runs/run-1/rerun",
    );
    expect(requestJsonBody(rerunInit)).toEqual({});
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
    expect(
      isApiError({ statusCode: 409, code: "conflict", message: "Conflict" }),
    ).toBe(true);
    expect(isApiError(new Error("Nope"))).toBe(false);
  });
});
