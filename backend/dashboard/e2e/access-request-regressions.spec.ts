import { expect, test } from "@playwright/test";
import path from "path";
import { writeFileSync } from "fs";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;
const evidence = process.env.E2E_EVIDENCE_DIR;

test("request expiry and route guards reject malformed authority; equivalent requests resolve once", async ({ request }) => {
  const memberHeaders = { Authorization: `Bearer ${await mintBackendToken("access_request_member")}` };
  const ownerHeaders = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const payload = {
    route: "/liquidity/forecast", institution_id: "BK-SAMP0001",
    module_scope: "liq", sensitivity_scope: "confidential", permission: "view",
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
    const response = await request.post(`${API}/authorization/access-requests`, { headers: memberHeaders, data: { ...payload, ...patch } });
    expect(response.status()).toBe(status);
    observations.push({ patch, status: response.status(), body: await response.json() });
  }
  const ids: string[] = [];
  for (const route of ["/liquidity/forecast", "/liquidity/monitoring"]) {
    const response = await request.post(`${API}/authorization/access-requests`, { headers: memberHeaders, data: { ...payload, route } });
    expect(response.status()).toBe(201);
    ids.push((await response.json()).id);
  }
  const grant = {
    principal_user_id: E2E_USERS.access_request_member.id, role_bundle: "viewer",
    institution_scope: "institution", institution_id: "BK-SAMP0001",
    module_scope: "liq", sensitivity_scope: "confidential", reason_category: "role_change",
  };
  const preview = await request.post(`${API}/authorization/bindings/preview`, { headers: ownerHeaders, data: grant });
  expect(preview.status()).toBe(200);
  const { principal_user_id, ...approval } = grant;
  const bindingIds: string[] = [];
  for (const id of ids) {
    const response = await request.post(`${API}/authorization/access-requests/${id}/approve`, { headers: ownerHeaders, data: { ...approval, expected_authority_sentence: (await preview.json()).authority_sentence } });
    expect(response.status()).toBe(200);
    const body = await response.json();
    bindingIds.push(body.binding.id);
    observations.push({ requestId: id, status: response.status(), body });
  }
  expect(bindingIds[0]).toBe(bindingIds[1]);
  const pending = await request.get(`${API}/authorization/access-requests`, { headers: ownerHeaders });
  expect(pending.status()).toBe(200);
  expect((await pending.json()).requests.filter((r: { id: string }) => ids.includes(r.id))).toEqual([]);
  if (evidence) writeFileSync(path.join(evidence, "access-request-api-regressions.json"), JSON.stringify(observations, null, 2));
});

test("Settings loads institutions and grant reason picker requires expiry", async ({ browser, request }) => {
  test.setTimeout(180_000);
  const context = await browser.newContext({ storageState: path.join(E2E_TMP, "admin.json") });
  const page = await context.newPage();
  await page.goto("/settings");
  await expect(page.getByText("Sample Bank Ltd", { exact: true }).first()).toBeVisible();
  if (evidence) await page.screenshot({ path: path.join(evidence, "owner-settings-institution.png"), fullPage: true });
  await page.goto("/settings/profile");
  await expect(page.getByRole("navigation", { name: /breadcrumb/i })).toHaveCount(0);
  await page.goto("/icaap/settings/workflow");
  await expect(page.getByRole("heading", { name: "ICAAP review chain" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: /breadcrumb/i })).toHaveCount(0);
  if (evidence) await page.screenshot({ path: path.join(evidence, "icaap-workflow-no-breadcrumb.png"), fullPage: true });
  const pending = await request.post(`${API}/authorization/access-requests`, {
    headers: { Authorization: `Bearer ${await mintBackendToken("access_extra_member")}` },
    data: { route: "/fx", institution_id: "BK-SAMP0001", module_scope: "fx", sensitivity_scope: "aggregated", permission: "view", reason_category: "role_change" },
  });
  expect(pending.status()).toBe(201);
  await page.goto("/access/members");
  await page.getByRole("button", { name: "Review request" }).click();
  await page.getByRole("button", { name: "Review grant" }).click();
  await page.getByRole("button", { name: "Grant access" }).click();
  await expect(page.getByText("Grant created", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add another grant" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Role bundle").selectOption("viewer");
  await dialog.getByLabel("Institution coverage").selectOption("BK-SAMP0001");
  await dialog.getByLabel("Module").selectOption("liq");
  await dialog.getByLabel("Sensitivity").selectOption("confidential");
  await expect(dialog.getByLabel("Reason category").locator("option")).toHaveCount(7);
  await dialog.getByLabel("Reason category").selectOption("temporary_cover");
  await expect(dialog.locator('input[type="datetime-local"]')).not.toHaveValue("");
  await dialog.locator('input[type="datetime-local"]').fill("");
  await expect(dialog.locator('button[type="submit"]')).toBeDisabled();
  await dialog.getByLabel("Reason category").selectOption("incident_break_glass");
  await expect(dialog.locator('input[type="datetime-local"]')).not.toHaveValue("");
  if (evidence) await page.screenshot({ path: path.join(evidence, "grant-reason-expiry.png"), fullPage: true });
  await dialog.getByRole("button", { name: "Review grant" }).click();
  await dialog.getByRole("button", { name: "Grant access" }).click();
  await expect(dialog.getByText("Grant created", { exact: true })).toBeVisible();
  if (evidence) await page.screenshot({ path: path.join(evidence, "additional-grant-after-request.png"), fullPage: true });
  await context.close();
});
