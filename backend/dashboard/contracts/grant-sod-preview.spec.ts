import { expect, test } from "@playwright/test";

import { writeFile } from "node:fs/promises";

import path from "node:path";

import { E2E_API_ORIGIN } from "../playwright.config";

import { E2E_USERS, mintBackendToken } from "../e2e/support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test("live previews are read-only and match allowed, warned and refused creates", async ({
  request,
}) => {
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("admin")}`,
  };
  const payload = (principal: string, role: string) => ({
    principal_user_id: E2E_USERS[principal].id,
    role_bundle: role,
    institution_scope:
      role === "account_admin" ? "organization" : "institution",
    institution_id: role === "account_admin" ? null : "BK-SAMP0001",
    module_scope: role === "account_admin" ? "account" : "reg",
    sensitivity_scope: role === "account_admin" ? "all" : "restricted",
    reason_category: "other",
    reason_detail: "Isolated preview/create policy parity journey",
  });
  const list = async () => {
    const response = await request.get(`${API}/authorization/bindings`, {
      headers,
    });
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const before = await list();
  const cases = [
    { principal: "sod_member", role: "analyst", outcome: "allow" },
    { principal: "sod_approver", role: "analyst", outcome: "warn" },
    { principal: "approver", role: "validator", outcome: "block" },
    { principal: "validator", role: "approver", outcome: "block" },
    { principal: "account_admin", role: "analyst", outcome: "block" },
    { principal: "analyst", role: "account_admin", outcome: "block" },
    { principal: "admin", role: "validator", outcome: "warn" },
  ];
  const previews = [];
  for (const entry of cases) {
    const response = await request.post(
      `${API}/authorization/bindings/preview`,
      {
        headers,
        data: payload(entry.principal, entry.role),
      },
    );
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.authority_sentence).toBeTruthy();
    expect(body.sod_decision.outcome).toBe(entry.outcome);
    expect(body.sod_decision.findings.length).toBe(
      entry.outcome === "allow" ? 0 : 1,
    );
    previews.push({ ...entry, body });
  }
  const after = await list();
  expect(after).toEqual(before);

  const creates = [];
  // The owner exception is previewed but not created: changing the actor's
  // own authority would intentionally end the session driving this journey.
  for (const entry of previews.filter((entry) => entry.principal !== "admin")) {
    const response = await request.post(`${API}/authorization/bindings`, {
      headers,
      data: {
        ...payload(entry.principal, entry.role),
        expected_authority_sentence: entry.body.authority_sentence,
      },
    });
    expect(response.status()).toBe(entry.outcome === "block" ? 409 : 201);
    const body = await response.json();
    const decision =
      entry.outcome === "block"
        ? body.error.details.sod_decision
        : body.sod_decision;
    expect(decision).toEqual(entry.body.sod_decision);
    creates.push({
      principal: entry.principal,
      role: entry.role,
      status: response.status(),
      body,
    });
    if (response.status() === 201) {
      const revoke = await request.post(
        `${API}/authorization/bindings/${body.binding.id}/revoke`,
        {
          headers,
          data: { reason: "Clean up isolated policy parity journey" },
        },
      );
      expect(revoke.ok()).toBeTruthy();
    }
  }
  for (const principal of ["viewer", "approver"]) {
    const session = await request.get(`${API}/auth/me`, {
      headers: {
        Authorization: `Bearer ${await mintBackendToken(principal)}`,
      },
    });
    expect(session.status()).toBe(200);
  }
  const unauthorized = await request.post(
    `${API}/authorization/bindings/preview`,
    {
      headers: { Authorization: `Bearer ${await mintBackendToken("analyst")}` },
      data: payload("approver", "analyst"),
    },
  );
  expect(unauthorized.status()).toBe(403);
  if (evidenceDir) {
    await writeFile(
      path.join(evidenceDir, "grant-preview-create-responses.json"),
      JSON.stringify(
        {
          previews,
          bindingsBeforePreview: before,
          bindingsAfterPreview: after,
          creates,
          unauthorizedPreview: {
            status: unauthorized.status(),
            body: await unauthorized.json(),
          },
        },
        null,
        2,
      ),
    );
  }
});
