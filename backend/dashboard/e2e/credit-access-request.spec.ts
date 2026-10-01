import { expect, test } from "@playwright/test";
import path from "node:path";
import { writeFileSync } from "node:fs";
import { E2E_API_ORIGIN, E2E_BASE_URL } from "../playwright.config";
import { E2E_USERS, mintBackendToken, mintSessionCookie } from "./support/mint";

test.use({ screenshot: "on" });

test("partial Credit grants expose exact missing requirements and approval unlocks the loan book", async ({ browser, request }) => {
  test.setTimeout(180_000);
  const api = `${E2E_API_ORIGIN}/api/v1`;
  const owner = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const draft = {
    principal_user_id: E2E_USERS.viewer.id,
    role_bundle: "viewer",
    institution_scope: "institution",
    institution_id: "BK-SAMP0001",
    module_scope: "credit",
    sensitivity_scope: "aggregated",
    reason_category: "role_change",
  };
  const preview = await request.post(`${api}/authorization/bindings/preview`, { headers: owner, data: draft });
  expect(preview.status()).toBe(200);
  const created = await request.post(`${api}/authorization/bindings`, { headers: owner, data: { ...draft, expected_authority_sentence: (await preview.json()).authority_sentence } });
  expect(created.status()).toBe(201);
  const member = { Authorization: `Bearer ${await mintBackendToken("viewer", 3)}` };
  const context = await browser.newContext();
  await context.addCookies([{ name: "authjs.session-token", value: await mintSessionCookie("viewer", 3), url: E2E_BASE_URL }]);
  const page = await context.newPage();
  const observations = [];
  for (const [route, sensitivity, label] of [
    ["/credit/book", "restricted", "Restricted"],
    ["/credit/concentration", "restricted", "Restricted"],
    ["/credit/activity", "confidential", "Confidential"],
  ]) {
    await page.goto(route);
    await expect(page.getByRole("heading", { name: "Access required" })).toBeVisible();
    await expect(page.getByText(`Credit · ${label} · View`, { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Request access", exact: true })).toBeEnabled();
    if (process.env.E2E_EVIDENCE_DIR) await page.screenshot({ path: path.join(process.env.E2E_EVIDENCE_DIR, `credit-${route.split("/").at(-1)}-denied.png`), fullPage: true });
    const wrong = await request.post(`${api}/authorization/access-requests`, { headers: member, data: { route, institution_id: draft.institution_id, module_scope: "fx", sensitivity_scope: "aggregated", permission: "view", reason_category: "role_change" } });
    expect(wrong.status()).toBe(404);
    const wanted = await request.post(`${api}/authorization/access-requests`, { headers: member, data: { route, institution_id: draft.institution_id, module_scope: "credit", sensitivity_scope: sensitivity, permission: "view", reason_category: "role_change" } });
    expect(wanted.status()).toBe(201);
    observations.push(await wanted.json());
  }
  const restricted = { ...draft, sensitivity_scope: "restricted" };
  const reviewed = await request.post(`${api}/authorization/bindings/preview`, { headers: owner, data: restricted });
  expect(reviewed.status()).toBe(200);
  const { principal_user_id, ...approval } = restricted;
  const approved = await request.post(`${api}/authorization/access-requests/${observations[0].id}/approve`, { headers: owner, data: { ...approval, expected_authority_sentence: (await reviewed.json()).authority_sentence } });
  expect(approved.status()).toBe(200);
  observations.push(await approved.json());
  await context.addCookies([{ name: "authjs.session-token", value: await mintSessionCookie("viewer", 4), url: E2E_BASE_URL }]);
  const responsePromise = page.waitForResponse(r => r.url().includes("/credit/loans?") && r.request().method() === "GET");
  await page.goto("/credit/book");
  const loans = await responsePromise;
  expect(loans.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Access required" })).toHaveCount(0);
  observations.push({ loanStatus: loans.status(), body: await loans.json() });
  if (process.env.E2E_EVIDENCE_DIR) {
    await page.screenshot({ path: path.join(process.env.E2E_EVIDENCE_DIR, "credit-book-approved.png"), fullPage: true });
    writeFileSync(path.join(process.env.E2E_EVIDENCE_DIR, "credit-request-results.json"), JSON.stringify(observations, null, 2));
  }
  await context.close();
});

test("branch-only Capital authority keeps access request available and cannot approve whole-institution access", async ({ browser, request }) => {
  test.setTimeout(120_000);
  const api = `${E2E_API_ORIGIN}/api/v1`;
  const owner = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const member = { Authorization: `Bearer ${await mintBackendToken("access_extra_member")}` };
  const wanted = await request.post(`${api}/authorization/access-requests`, { headers: member, data: { route: "/basel", institution_id: "BK-SAMP0001", module_scope: "cap", sensitivity_scope: "aggregated", permission: "view", reason_category: "role_change" } });
  expect(wanted.status()).toBe(201);
  const id = (await wanted.json()).id;
  const draft = { principal_user_id: E2E_USERS.access_extra_member.id, role_bundle: "viewer", institution_scope: "institution", institution_id: "BK-SAMP0001", module_scope: "cap", sensitivity_scope: "aggregated", reason_category: "role_change", data_scope_kind: "branch", data_scope_values: ["ACC"] };
  const preview = await request.post(`${api}/authorization/bindings/preview`, { headers: owner, data: draft });
  expect(preview.status()).toBe(200);
  const reviewed = { ...draft, expected_authority_sentence: (await preview.json()).authority_sentence };
  const { principal_user_id, ...approval } = reviewed;
  const denied = await request.post(`${api}/authorization/access-requests/${id}/approve`, { headers: owner, data: approval });
  expect(denied.status()).toBe(409);
  const granted = await request.post(`${api}/authorization/bindings`, { headers: owner, data: reviewed });
  expect(granted.status()).toBe(201);
  const pending = await request.get(`${api}/authorization/access-requests`, { headers: owner });
  expect((await pending.json()).requests).toEqual(expect.arrayContaining([expect.objectContaining({ id, status: "pending" })]));
  const context = await browser.newContext();
  await context.addCookies([{ name: "authjs.session-token", value: await mintSessionCookie("access_extra_member", 3), url: E2E_BASE_URL }]);
  const page = await context.newPage();
  if (process.env.E2E_EVIDENCE_DIR) writeFileSync(path.join(process.env.E2E_EVIDENCE_DIR, "capital-branch-refusal.json"), JSON.stringify({ approvalStatus: denied.status(), refusal: await denied.json(), pending: await pending.json() }, null, 2));
  await page.goto("/basel");
  await expect(page.getByRole("heading", { name: "Access required" })).toBeVisible();
  await expect(page.getByText("Basel Capital · Aggregated · View", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("waiting for an organization owner");
  if (process.env.E2E_EVIDENCE_DIR) {
    await page.screenshot({ path: path.join(process.env.E2E_EVIDENCE_DIR, "capital-branch-access-required.png"), fullPage: true });
    writeFileSync(path.join(process.env.E2E_EVIDENCE_DIR, "capital-branch-refusal.json"), JSON.stringify({ approvalStatus: denied.status(), refusal: await denied.json(), pending: await pending.json() }, null, 2));
  }
  await page.goto("/basel/rwa");
  await expect(page.getByRole("heading", { name: "Access required" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Request access", exact: true })).toBeEnabled();
  await context.close();
});
