import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { liquidityReviewClient } from "./liquidity-client";

const tenant = { orgId: DEFAULT_ORG_ID, userId: DEFAULT_USER_ID };

describe("liquidityReviewClient", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses generated liquidity contracts and tenant headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          case_id: "case-1",
          scenario_id: null,
          calculation_run_id: null,
          calculation_input_hash: null,
          status: "not_calculated",
          currency: null,
          as_of_date: null,
          metrics: [],
          findings: [],
          generated_at: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const summary = await liquidityReviewClient.summary(tenant, "case-1");

    expect(summary.status).toBe("not_calculated");
    const [url, request] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/cases/case-1/liquidity/summary");
    expect(new Headers(request?.headers).get("X-Org-Id")).toBe(DEFAULT_ORG_ID);
    expect(new Headers(request?.headers).get("X-User-Id")).toBe(
      DEFAULT_USER_ID,
    );
  });

  it("sends review actions through the generated mutation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "finding-1",
          calculation_run_id: "run-1",
          rule_id: "liquidity.sources_coverage",
          rule_version: "liquidity-v1.0.0",
          title: "Thin coverage",
          summary: "Coverage is thin.",
          rationale: "Sources are below uses.",
          severity: "high",
          status: "acknowledged",
          disposition_reason: null,
          evidence: [],
          created_at: "2026-07-13T12:00:00Z",
          updated_at: "2026-07-13T12:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await liquidityReviewClient.review(tenant, "case-1", "finding-1", {
      action: "acknowledge",
    });

    const [url, request] = fetchMock.mock.calls[0];
    expect(String(url)).toContain(
      "/api/v1/cases/case-1/liquidity/findings/finding-1/review",
    );
    expect(request?.method).toBe("POST");
    expect(request?.body).toBe(JSON.stringify({ action: "acknowledge" }));
  });
});
