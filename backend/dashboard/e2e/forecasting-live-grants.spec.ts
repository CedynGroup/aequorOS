/** Real grant lifecycle against the disposable backend; no response interception. */
import { expect, test } from "@playwright/test";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken, writeStorageState } from "./support/mint";

const evidence = process.env.E2E_EVIDENCE_DIR;
const bank = "/banks/BK-SAMP0001";
const moduleRequest = /\/banks\/[^/]+\/(?:forecast|reverse-stress)(?:\/|$)/;

test("account-only and baseline members retain disabled Forecasting workspaces", async ({ browser }) => {
  test.slow();
  for (const role of ["account_admin", "invite_fresh"] as const) {
    const context = await browser.newContext({ storageState: path.join(E2E_TMP, `${role}.json`) });
    const page = await context.newPage();
    const requests: string[] = [];
    page.on("request", r => { if (moduleRequest.test(r.url())) requests.push(r.url()); });
    for (const route of ["", "/scenario", "/nii", "/optimizer", "/whatif", "/reverse-stress", "/assumptions"]) {
      await page.goto(`/forecasting${route}`);
      await expect(page.getByRole("region", { name: "Forecasting workspace" })).toBeVisible({ timeout: 60000 });
      const button = page.getByRole("button", { name: "View Forecasting" });
      await expect(button).toBeDisabled();
      await button.locator("..").focus();
      const tooltip = page.getByRole("tooltip");
      await expect(tooltip).toContainText(/Requires Forecasting .* View/);
      await expect(tooltip).toContainText("Org Owner");
      await expect(tooltip).toContainText("Settings → Members");
    }
    expect(requests).toEqual([]);
    if (evidence) await page.screenshot({ path: path.join(evidence, `forecasting-${role}.png`), fullPage: true });
    await context.close();
  }
});

test("real Forecasting grants separate summary, confidential reading, and execution", async ({ browser, request }) => {
  test.slow();
  const transcript: unknown[] = [];
  let version = E2E_USERS.grant_member.authv;
  async function api(method: string, route: string, data?: unknown, role = "grant_member", authv = version) {
    const response = await request.fetch(`${E2E_API_ORIGIN}/api/v1${route}`, {
      method, data, headers: { Authorization: `Bearer ${await mintBackendToken(role, role === "admin" ? E2E_USERS.admin.authv : authv)}` },
    });
    const body = await response.json();
    transcript.push({ method, route, role, status: response.status(), body });
    return { status: response.status(), body };
  }
  async function grant(sensitivity: string, bundle = "viewer") {
    const data = { principal_user_id: E2E_USERS.grant_member.id, role_bundle: bundle, institution_scope: "institution", institution_id: "BK-SAMP0001", module_scope: "fcst", sensitivity_scope: sensitivity, reason: "Live Forecasting authorization verification" };
    const preview = await api("POST", "/authorization/bindings/preview", data, "admin");
    expect(preview.status).toBe(200);
    const created = await api("POST", "/authorization/bindings", { ...data, expected_authority_sentence: preview.body.authority_sentence }, "admin");
    expect(created.status).toBe(201);
    version += 1;
  }
  async function readerPage() {
    const state = await writeStorageState("grant_member", E2E_BASE_URL, E2E_TMP);
    // Storage helper defaults to initial authv; use the current version explicitly.
    const { mintSessionCookie } = await import("./support/mint");
    const context = await browser.newContext({ storageState: state });
    await context.addCookies([{ name: "authjs.session-token", value: await mintSessionCookie("grant_member", version), domain: "127.0.0.1", path: "/" }]);
    return { context, page: await context.newPage() };
  }
  await grant("confidential");
  expect((await api("GET", `${bank}/forecast/scenarios`)).status).toBe(403);
  {
    const { context, page } = await readerPage();
    const requests: string[] = [];
    page.on("request", r => { if (moduleRequest.test(r.url())) requests.push(r.url()); });
    for (const route of ["nii", "optimizer", "whatif"]) {
      await page.goto(`/forecasting/${route}`);
      const button = page.getByRole("button", { name: "View Forecasting" });
      await expect(button).toBeDisabled({ timeout: 60000 });
      await button.locator("..").focus();
      await expect(page.getByRole("tooltip")).toContainText("Requires Forecasting · Aggregated · View");
      await expect(page.getByText("No succeeded forecast runs yet")).toHaveCount(0);
    }
    expect(requests).toEqual([]);
    if (evidence) await page.screenshot({ path: path.join(evidence, "forecasting-real-confidential-reader.png"), fullPage: true });
    await context.close();
  }
  await grant("aggregated");
  const periods = await api("GET", `${bank}/reporting-periods`);
  expect(periods.status).toBe(200);
  const period = periods.body.periods[0].id;
  const payloads: [string, object][] = [
    ["forecast/runs", { scenario_code: "base" }], ["forecast/optimizer", {}],
    ["forecast/whatif", { shock_code: "rate_shock_up_400" }], ["reverse-stress/runs", {}],
  ];
  for (const [route, data] of payloads) expect((await api("POST", `${bank}/${route}`, { reporting_period_id: period, ...data })).status).toBe(403);
  {
    const { context, page } = await readerPage();
    await page.goto("/forecasting");
    const run = page.getByRole("button", { name: "Run forecast" }).first();
    await expect(run).toBeDisabled({ timeout: 60000 });
    await run.locator("..").focus();
    await expect(page.getByRole("tooltip")).toContainText("Requires Forecasting · Confidential · Run");
    if (evidence) await page.screenshot({ path: path.join(evidence, "forecasting-real-reader.png"), fullPage: true });
    await context.close();
  }
  const staleVersion = version;
  await grant("confidential", "analyst");
  expect((await api("GET", `${bank}/forecast/runs`, undefined, "grant_member", staleVersion)).status).toBe(401);
  for (const [route, data] of payloads) {
    const result = await api("POST", `${bank}/${route}`, { reporting_period_id: period, ...data });
    expect(result.status).toBe(201);
    if (route === "forecast/runs") {
      expect(result.body.status).toBe("succeeded");
      expect((await api("GET", `${bank}/forecast/runs/${result.body.id}`)).status).toBe(200);
      expect((await api("GET", `/banks/BK-UNKNOWN1/forecast/runs/${result.body.id}`)).status).toBe(404);
    }
  }
  expect((await api("GET", `${bank}/forecast/runs/00000000-0000-4000-8000-000000000001`)).status).toBe(404);
  const summaryGrant = { principal_user_id: E2E_USERS.macro_viewer.id, role_bundle: "viewer", institution_scope: "institution", institution_id: "BK-SAMP0001", module_scope: "fcst", sensitivity_scope: "aggregated", reason: "Live aggregated-only Forecasting verification" };
  const preview = await api("POST", "/authorization/bindings/preview", summaryGrant, "admin");
  expect(preview.status).toBe(200);
  expect((await api("POST", "/authorization/bindings", { ...summaryGrant, expected_authority_sentence: preview.body.authority_sentence }, "admin")).status).toBe(201);
  const summaryVersion = E2E_USERS.macro_viewer.authv + 1;
  const summaries = await api("GET", `${bank}/forecast/runs`, undefined, "macro_viewer", summaryVersion);
  expect(summaries.status).toBe(200);
  expect(summaries.body.runs.length).toBeGreaterThan(0);
  expect((await api("GET", `${bank}/forecast/runs/${summaries.body.runs[0].id}`, undefined, "macro_viewer", summaryVersion)).status).toBe(404);
  const state = await writeStorageState("macro_viewer", E2E_BASE_URL, E2E_TMP);
  const { mintSessionCookie } = await import("./support/mint");
  const context = await browser.newContext({ storageState: state });
  await context.addCookies([{ name: "authjs.session-token", value: await mintSessionCookie("macro_viewer", summaryVersion), domain: "127.0.0.1", path: "/" }]);
  const page = await context.newPage();
  const details: string[] = [];
  page.on("request", r => { if (/\/forecast\/runs\/[^/?]+|\/reverse-stress\/latest/.test(r.url())) details.push(r.url()); });
  await page.goto("/forecasting");
  await expect(page.getByRole("heading", { name: "Balance Sheet Forecast" })).toBeVisible({ timeout: 60000 });
  await expect(page.getByRole("link", { name: "NII Forecast", exact: true })).toHaveAttribute("aria-disabled", "true");
  await page.goto("/forecasting/reverse-stress");
  const disabled = page.getByRole("button", { name: "View Forecasting" });
  await expect(disabled).toBeDisabled({ timeout: 60000 });
  await disabled.locator("..").focus();
  await expect(page.getByRole("tooltip")).toContainText("Requires Forecasting · Confidential · View");
  expect(details).toEqual([]);
  if (evidence) await page.screenshot({ path: path.join(evidence, "forecasting-real-aggregated-reader.png"), fullPage: true });
  await context.close();
  if (evidence) await writeFile(path.join(evidence, "forecasting-live-grant-api.json"), JSON.stringify(transcript, null, 2));
});
