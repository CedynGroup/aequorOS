import { expect, test } from "@playwright/test";

import path from "path";

import { mkdirSync, writeFileSync } from "fs";

import { E2E_API_ORIGIN } from "../playwright.config";

import { E2E_USERS, mintBackendToken } from "../e2e/support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;

const evidence = process.env.E2E_EVIDENCE_DIR;

test("request expiry and route guards reject malformed authority; equivalent requests resolve once", async ({
  request,
}) => {
  const memberHeaders = {
    Authorization: `Bearer ${await mintBackendToken("access_request_member")}`,
  };
  const ownerHeaders = {
    Authorization: `Bearer ${await mintBackendToken("admin")}`,
  };
  const payload = {
    route: "/liquidity/forecast",
    institution_id: "BK-SAMP0001",
    module_scope: "liq",
    sensitivity_scope: "confidential",
    permission: "view",
    reason_category: "role_change",
  };
  const observations: unknown[] = [];
  for (const [patch, status] of [
    [{ reason_category: "temporary_cover" }, 422],
    [{ reason_category: "incident_break_glass" }, 422],
    [{ reason_category: "invented" }, 422],
    [{ route: "/fx/trades/secret" }, 404],
    [{ module_scope: "risk" }, 404],
    [{ institution_id: "BK-NONE0001" }, 404],
  ] as const) {
    const response = await request.post(
      `${API}/authorization/access-requests`,
      { headers: memberHeaders, data: { ...payload, ...patch } },
    );
    expect(response.status()).toBe(status);
    observations.push({
      patch,
      status: response.status(),
      body: await response.json(),
    });
  }
  const ids: string[] = [];
  for (const route of ["/liquidity/forecast", "/liquidity/monitoring"]) {
    const response = await request.post(
      `${API}/authorization/access-requests`,
      { headers: memberHeaders, data: { ...payload, route } },
    );
    expect(response.status()).toBe(201);
    ids.push((await response.json()).id);
  }
  const grant = {
    principal_user_id: E2E_USERS.access_request_member.id,
    role_bundle: "viewer",
    institution_scope: "institution",
    institution_id: "BK-SAMP0001",
    module_scope: "liq",
    sensitivity_scope: "confidential",
    reason_category: "role_change",
  };
  const preview = await request.post(`${API}/authorization/bindings/preview`, {
    headers: ownerHeaders,
    data: grant,
  });
  expect(preview.status()).toBe(200);
  const { principal_user_id, ...approval } = grant;
  const bindingIds: string[] = [];
  for (const id of ids) {
    const response = await request.post(
      `${API}/authorization/access-requests/${id}/approve`,
      {
        headers: ownerHeaders,
        data: {
          ...approval,
          expected_authority_sentence: (await preview.json())
            .authority_sentence,
        },
      },
    );
    expect(response.status()).toBe(200);
    const body = await response.json();
    bindingIds.push(body.binding.id);
    observations.push({ requestId: id, status: response.status(), body });
  }
  expect(bindingIds[0]).toBe(bindingIds[1]);
  const pending = await request.get(`${API}/authorization/access-requests`, {
    headers: ownerHeaders,
  });
  expect(pending.status()).toBe(200);
  expect(
    (await pending.json()).requests.filter((r: { id: string }) =>
      ids.includes(r.id),
    ),
  ).toEqual([]);
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    writeFileSync(
      path.join(evidence, "access-request-api-regressions.json"),
      JSON.stringify(observations, null, 2),
    );
  }
});
