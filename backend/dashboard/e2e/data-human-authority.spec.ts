/** Live scoped DATA journeys. Real API, database, sessions and rendered dashboard. */
import { expect, type Page } from "@playwright/test";
import { test } from "./support/story-identities";
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken, writeStorageState } from "./support/mint";
import { DatabaseSync } from "node:sqlite";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { SignJWT } from "jose";
import { randomUUID } from "node:crypto";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const apiEvidenceName = process.env.E2E_AUTHORITY_EVIDENCE_NAME ?? "data-authority-api.json";
const transcript: unknown[] = [];
async function api(role: string, route: string, method = "GET", data?: object) {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001/${route}`, {
    method,
    headers: { Authorization: `Bearer ${await mintBackendToken(role)}`, ...(data ? { "Content-Type": "application/json" } : {}) },
    body: data ? JSON.stringify(data) : undefined,
  });
  const body = await response.json();
  transcript.push({ role, method, route, status: response.status, body });
  if (evidenceDir) writeFileSync(path.join(evidenceDir, apiEvidenceName), JSON.stringify(transcript, null, 2));
  return { status: response.status, body };
}
function counts() {
  const db = new DatabaseSync(path.join(E2E_TMP, "e2e.db"), { readOnly: true });
  try {
    return ["audit_events", "jobs", "ingestion_batches", "reconciliation_exceptions"].map(table => db.prepare(`SELECT count(*) AS n FROM ${table}`).get()?.n);
  } finally { db.close(); }
}
async function shot(page: Page, name: string) {
  if (evidenceDir) await page.screenshot({ path: path.join(evidenceDir, `${name}.png`), fullPage: true });
}

test.describe("exact human DATA grants", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer", "grant_member", "invite_fresh", "macro_viewer"],
      bindings: [
        { role: "viewer", bundle: "viewer", module: "data", sensitivity: "aggregated" },
        { role: "grant_member", bundle: "viewer", module: "data", sensitivity: "confidential" },
        { role: "grant_member", bundle: "viewer", module: "risk", sensitivity: "confidential" },
        { role: "invite_fresh", bundle: "analyst", module: "data", sensitivity: "restricted" },
        { role: "macro_viewer", bundle: "analyst", module: "data", sensitivity: "confidential" },
        { role: "macro_viewer", bundle: "analyst", module: "credit", sensitivity: "restricted" },
      ],
    },
  });

  test("each sensitivity reads only its own surfaces and partial bindings cannot authorize effects", async ({ page, browser }) => {
    const routes: [string, string][] = [
      ["ingestion-summary", "aggregated"], ["data-activations", "aggregated"],
      ["ingestion-batches", "confidential"], ["mapping-configs", "confidential"],
      ["reporting-periods", "confidential"], ["canonical-withdrawals", "confidential"],
      ["system-of-record", "confidential"], ["reconciliation/exceptions", "confidential"],
      ["canonical-positions?limit=1&offset=1", "restricted"], ["canonical-positions/facets", "restricted"],
      ["database-direct/connections", "restricted"], ["temenos/connections", "restricted"], ["temenos/domains", "restricted"],
    ];
    for (const [role, sensitivity] of [["viewer", "aggregated"], ["grant_member", "confidential"], ["invite_fresh", "restricted"]]) {
      for (const [route, required] of routes) {
        const response = await api(role, route);
        expect(response.status, `${role}: ${route}`).toBe(sensitivity === required ? 200 : 403);
        if (sensitivity !== required) expect(response.body).not.toHaveProperty("total");
      }
    }
    const before = counts();
    for (const role of ["viewer", "grant_member", "macro_viewer"]) {
      const upload = await fetch(`${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001/ingestion-uploads`, {
        method: "POST", headers: { Authorization: `Bearer ${await mintBackendToken(role)}` },
        body: (() => { const form = new FormData(); form.set("file", new Blob(["x\n1\n"]), "denied.csv"); return form; })(),
      });
      const body = await upload.json();
      transcript.push({ role, route: "ingestion-uploads", status: upload.status, body });
      expect(upload.status).toBe(403);
      expect((await api(role, "ingestion-batches", "POST", { source_system: "EXCEL_CSV", location: "temp://denied", as_of_date: "2026-03-15", reason: "Adversarial partial-binding attempt" })).status).toBe(403);
      expect((await api(role, "data-activations", "POST", { as_of_date: "2026-03-15", reason: "Adversarial denied activation", run_calculations: false })).status).toBe(403);
      expect((await api(role, "position-snapshots/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/override", "POST", { field: "balance", value: "1", reason: "Adversarial opaque ID" })).status).toBe(404);
    }
    expect(counts()).toEqual(before);
    transcript.push({ effectCheck: "Denied writes leave audit, jobs, batches and exceptions unchanged", before, after: counts() });
    const requests: string[] = [];
    page.on("request", req => { if (req.url().includes("/api/v1/banks/")) requests.push(req.url()); });
    await page.goto("/data-engine");
    await expect(page.getByRole("heading", { name: "Data Engine", exact: true })).toBeVisible();
    await expect(page.getByText("DATA · Confidential · View", { exact: false }).first()).toBeVisible();
    expect(requests.some(url => /ingestion-batches|mapping-configs|reconciliation\/exceptions|database-direct\/connections/.test(url))).toBe(false);
    await shot(page, "data-aggregated-only-overview");

    const restricted = await browser.newContext({ storageState: path.join(E2E_TMP, "invite_fresh.json") });
    const restrictedPage = await restricted.newPage();
    await restrictedPage.goto("/data-engine/excel-csv");
    await restrictedPage.getByRole("button", { name: "Templates", exact: true }).click();
    await expect(restrictedPage.getByText("DATA · Confidential · View to read mapping status", { exact: false })).toBeVisible();
    await expect(restrictedPage.getByRole("button", { name: "Activate it", exact: true })).toHaveCount(0);
    await shot(restrictedPage, "data-restricted-mapping-unavailable");
    await restrictedPage.goto("/data-engine");
    await restrictedPage.getByRole("button", { name: "Advanced actions" }).click();
    await expect(restrictedPage.getByText("Enter an as-of date manually. The latest accepted upload date is unavailable.")).toBeVisible();
    await expect(restrictedPage.getByLabel("As-of date", { exact: true })).toHaveValue("");
    await shot(restrictedPage, "data-restricted-upload-dates-unavailable");
    await restricted.close();

    const confidential = await browser.newContext({ storageState: path.join(E2E_TMP, "grant_member.json") });
    const confidentialPage = await confidential.newPage();
    const deniedRequests: string[] = [];
    confidentialPage.on("request", req => { if (/\/facts(?:\?|$)|canonical-positions/.test(req.url())) deniedRequests.push(req.url()); });
    await confidentialPage.goto("/");
    await expect(confidentialPage.getByText(/DATA · Restricted · View/).first()).toBeVisible();
    await expect(confidentialPage.locator("main")).not.toContainText("No computed data yet");
    expect(deniedRequests).toEqual([]);
    await shot(confidentialPage, "data-confidential-home-unavailable-facts");
    await confidential.close();
  });

  test("verified examiners read covered periods and facts while foreign, expired and invalid sessions fail closed", async () => {
    async function examiner(org: string, expired = false) {
      const now = Math.floor(Date.now() / 1000);
      return new SignJWT({ typ: "impersonation", org, act_operator: "live-examiner@example.test", session_id: randomUUID(), roles: ["examiner"] })
        .setProtectedHeader({ alg: "HS256" }).setIssuedAt(now - 1200).setExpirationTime(now + (expired ? -60 : 900))
        .sign(new TextEncoder().encode("e2e-impersonation-secret-not-production-000"));
    }
    async function read(token: string, route: string) {
      const response = await fetch(`${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001/${route}`, { headers: { Authorization: `Bearer ${token}` } });
      const body = await response.json();
      transcript.push({ actor: "examiner", route, status: response.status, body });
      if (evidenceDir) writeFileSync(path.join(evidenceDir, apiEvidenceName), JSON.stringify(transcript, null, 2));
      return { status: response.status, body };
    }
    const token = await examiner("OR-DEM00001");
    const periods = await read(token, "reporting-periods");
    expect(periods.status).toBe(200);
    const factRoute = `reporting-periods/${periods.body.periods[0].id}/facts`;
    const facts = await read(token, factRoute);
    expect(facts.status).toBe(200);
    expect(facts.body.balance_sheet.length).toBeGreaterThan(0);
    for (const route of ["reporting-periods", factRoute]) {
      expect((await read(await examiner("OR-1S000002"), route)).status).toBe(404);
      expect((await read(await examiner("OR-DEM00001", true), route)).status).toBe(401);
      expect((await read("invalid", route)).status).toBe(401);
    }
    expect((await read(token, "ingestion-batches")).status).toBe(403);
    expect((await api("grant_member", factRoute)).status).toBe(404);
    expect((await api("invite_fresh", factRoute)).status).toBe(200);
  });

  test("restricted RUN activates validated data while missing engine grants and missing data refuse without writes", async () => {
    const periods = await api("grant_member", "reporting-periods");
    const asOf = periods.body.periods[0].period_end;
    const before = counts();
    const missingEngine = await api("invite_fresh", "data-activations", "POST", {
      as_of_date: asOf, reason: "Missing per-engine grants must fail before derivation", run_calculations: true,
    });
    expect(missingEngine.status).toBe(403);
    expect(counts()).toEqual(before);
    const missingData = await api("invite_fresh", "data-activations", "POST", {
      as_of_date: "2099-01-01", reason: "Missing successful data must refuse", run_calculations: false,
    });
    expect(missingData.status).toBe(409);
    expect(counts()).toEqual(before);
    const activated = await api("invite_fresh", "data-activations", "POST", {
      as_of_date: asOf, reason: "Activate only the disposable accepted book", run_calculations: false,
    });
    expect(activated.status).toBe(201);
    expect(activated.body.facts_created).toBeGreaterThan(0);
    expect(activated.body.runs).toEqual([]);
  });

  test("a confidential batch reader sees withheld diagnostics rather than false validation success", async ({ browser }) => {
    const mapping = await api("admin", "mapping-configs", "POST", {
      source_system: "EXCEL_CSV", name: "Disposable diagnostic journey mapping", activate: true, reason: "Live confidentiality check",
      config: { field_mappings: { position: { source_table: "positions", fields: {
        source_reference: "source_reference", position_type: "position_type", currency: "currency", balance: "balance", interest_rate: "interest_rate",
      } } }, product_mappings: {} },
    });
    expect(mapping.status).toBe(200);
    const form = new FormData();
    form.set("file", new Blob(["source_reference,position_type,currency,balance,interest_rate\nLIVE-VALID,LOAN,GHS,100,0.1\nLIVE-INVALID,LOAN,GHS,100,-0.19\nLIVE-UNTRANSLATABLE,LOAN,GHS,not-money,0.1\n"]), "positions.csv");
    const upload = await fetch(`${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001/ingestion-uploads`, {
      method: "POST", headers: { Authorization: `Bearer ${await mintBackendToken("invite_fresh")}` }, body: form,
    });
    expect(upload.status).toBe(201);
    const staged = await upload.json();
    const ingested = await api("invite_fresh", "ingestion-batches", "POST", {
      source_system: "EXCEL_CSV", mapping_config_id: mapping.body.id, location: staged.location, as_of_date: "2026-03-15", reason: "Exercise real suppressed diagnostics",
    });
    expect(ingested.status).toBe(201);
    const batch = ingested.body.batch;
    expect(batch.records_error).toBeGreaterThan(0);
    const metadata = await api("grant_member", `ingestion-batches/${batch.id}`);
    expect(metadata.status).toBe(200);
    expect(metadata.body.raw_artifact_path).toBeNull();
    expect(metadata.body.validation_report).toEqual({});
    expect((await api("grant_member", `ingestion-batches/${batch.id}/translation-failures`)).status).toBe(404);
    const raw = await api("invite_fresh", `ingestion-batches/${batch.id}/translation-failures`);
    expect(raw.status).toBe(200);
    expect(raw.body.failures.length).toBeGreaterThan(0);
    const reader = await browser.newContext({ storageState: path.join(E2E_TMP, "grant_member.json") });
    const page = await reader.newPage();
    const requests: string[] = [];
    page.on("request", req => { if (/translation-failures/.test(req.url())) requests.push(req.url()); });
    await page.goto(`/data-engine/batches/${batch.id}`);
    await expect(page.getByRole("heading", { name: /Ingestion batch/ })).toBeVisible();
    await expect(page.getByText(/DATA · Restricted · View for validation findings/)).toBeVisible();
    await expect(page.getByText(/DATA · Restricted · View for untranslatable rows/)).toBeVisible();
    await expect(page.locator("main")).not.toContainText("Every record in this batch passed");
    await expect(page.locator("main")).not.toContainText("Every extracted row translated");
    expect(requests).toEqual([]);
    await shot(page, "batch-confidential-diagnostics-unavailable");
    await reader.close();
  });
});

test.describe("authenticated reconciliation decisions", () => {
  test.use({
    storageState: path.join(E2E_TMP, "forecast_member.json"),
    storyConfig: {
      roles: ["forecast_member", "forecast_summary_member", "viewer"],
      bindings: [
        { role: "forecast_member", bundle: "analyst", module: "data", sensitivity: "confidential" },
        { role: "forecast_member", bundle: "approver", module: "data", sensitivity: "confidential" },
        { role: "forecast_summary_member", bundle: "approver", module: "data", sensitivity: "confidential" },
        { role: "viewer", bundle: "viewer", module: "data", sensitivity: "confidential" },
      ],
    },
  });
  test("maker proposes exact stored ceilings; reader sees access request; separate checker approves and rejects", async ({ page, browser }) => {
    const route = "reconciliation/exceptions";
    const payload = { reason: "Live bounded custody-feed exception", max_gap_fraction: "0.050000", effective_from: "2026-01-01", effective_to: "2026-12-31" };
    const before = counts();
    for (const max_gap_fraction of ["0.0000001", "0.0500006"]) {
      expect((await api("forecast_member", route, "POST", { ...payload, max_gap_fraction })).status).toBe(422);
    }
    expect(counts()).toEqual(before);
    await page.goto("/data-engine");
    const panel = page.getByRole("region", { name: "Reconciliation exceptions" });
    await panel.getByLabel("Reason", { exact: true }).fill(payload.reason);
    await panel.getByLabel("Effective from", { exact: true }).fill(payload.effective_from);
    await panel.getByLabel("Effective to (optional)").fill(payload.effective_to);
    for (const value of ["0.0000001", "0.0500006"]) {
      await panel.getByLabel("Maximum gap fraction (0–1)").fill(value);
      await panel.getByRole("button", { name: "Record proposal" }).click();
      expect(await panel.getByLabel("Maximum gap fraction (0–1)").evaluate((input: HTMLInputElement) => input.validity.valid)).toBe(false);
      expect(counts()).toEqual(before);
    }
    await panel.getByLabel("Maximum gap fraction (0–1)").fill("0.050000");
    await panel.getByRole("button", { name: "Record proposal" }).click();
    await expect(panel.getByText("Proposal recorded. A different officer must approve it.")).toBeVisible();
    const proposed = (await api("forecast_member", route)).body.exceptions.find((row: { reason: string }) => row.reason === payload.reason);
    expect(proposed.status).toBe("pending");
    expect(proposed.requested_by).toBe(E2E_USERS.forecast_member.id);
    expect(proposed.approved_by_user_id).toBeNull();
    expect(counts()[1]).toBe(before[1]);
    await expect(panel.getByText("You cannot approve your own proposal.")).toBeVisible();
    await expect(panel.getByRole("button", { name: "Approve exception" })).toHaveCount(0);
    await shot(page, "reconciliation-maker-pending");
    const afterProposal = counts();
    expect((await api("forecast_member", `${route}/${proposed.id}/approve`, "POST", { approved_by: "Different officer label" })).status).toBe(404);
    expect((await api("viewer", `${route}/${proposed.id}/approve`, "POST", { approved_by: "Spoofed officer" })).status).toBe(404);
    expect((await api("forecast_member", `${route}/${proposed.id}/approve`, "POST", { approved_by: "Spoof", approved_by_user_id: E2E_USERS.forecast_summary_member.id })).status).toBe(422);
    expect(counts()).toEqual(afterProposal);

    const reader = await browser.newContext({ storageState: path.join(E2E_TMP, "viewer.json") });
    const readerPage = await reader.newPage();
    await readerPage.goto("/data-engine");
    const readerPanel = readerPage.getByRole("region", { name: "Reconciliation exceptions" });
    await expect(readerPanel.getByText(/Awaiting approval by a different officer with Confidential · Approve/)).toBeVisible();
    await expect(readerPanel.getByRole("link", { name: "Request access" })).toHaveAttribute("href", "/access");
    await expect(readerPanel.getByRole("button", { name: "Approve exception" })).toHaveCount(0);
    await shot(readerPage, "reconciliation-reader-action-card");
    await reader.close();

    const checker = await browser.newContext({ storageState: path.join(E2E_TMP, "forecast_summary_member.json") });
    const checkerPage = await checker.newPage();
    await checkerPage.goto("/data-engine");
    const checkerPanel = checkerPage.getByRole("region", { name: "Reconciliation exceptions" });
    await checkerPanel.getByLabel("Your approval or rejection label").fill("Live separate checker");
    await expect(checkerPanel.getByRole("button", { name: "Approve exception" })).toBeEnabled();
    await expect(checkerPanel.getByRole("button", { name: "Reject", exact: true })).toBeDisabled();
    await shot(checkerPage, "reconciliation-checker-actions");
    await checkerPanel.getByRole("button", { name: "Approve exception" }).click();
    await expect(checkerPanel.getByText("Approved by Live separate checker")).toBeVisible();
    await expect(checkerPanel.getByRole("button", { name: "Approve exception" })).toHaveCount(0);
    const approved = (await api("viewer", route)).body.exceptions.find((row: { id: string }) => row.id === proposed.id);
    expect(approved.approved_by_user_id).toBe(E2E_USERS.forecast_summary_member.id);
    expect(approved.requested_by).toBe(E2E_USERS.forecast_member.id);
    expect(approved.approval_timestamp).toBeTruthy();
    await shot(checkerPage, "reconciliation-approved-provenance");
    const terminal = counts();
    expect((await api("forecast_summary_member", `${route}/${proposed.id}/approve`, "POST", { approved_by: "Repeat" })).status).toBe(409);
    expect(counts()).toEqual(terminal);
    const second = await api("forecast_member", route, "POST", { ...payload, reason: "Live rejectable boundary exception", max_gap_fraction: "0.0000010" });
    expect(second.status).toBe(201);
    await checkerPage.reload();
    await checkerPanel.getByLabel("Your approval or rejection label").fill("Live separate checker");
    await checkerPanel.getByLabel("Reason for rejection").fill("Source feed repaired; exception unnecessary.");
    await checkerPanel.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(checkerPanel.getByText("Revoked", { exact: true })).toBeVisible();
    await expect(checkerPanel.getByRole("button", { name: "Reject", exact: true })).toHaveCount(0);
    await shot(checkerPage, "reconciliation-revoked-terminal-card");
    const revoked = (await api("viewer", route)).body.exceptions.find((row: { id: string }) => row.id === second.body.id);
    expect(revoked.revoked_at).toBeTruthy();
    expect(revoked.revoked_by).toBe("Live separate checker");
    await checker.close();
  });

  test("declarations and withdrawals require a separate checker and reversal restores the canonical book", async () => {
    const maker = "forecast_member";
    const checker = "forecast_summary_member";
    const declaration = await api(maker, "system-of-record", "POST", {
      position_type: "LOAN", source_system: "EXCEL_CSV", effective_from: "2026-01-01",
      source_citation: "Disposable live sign-off", rationale: "Verify scoped four-eyes governance", proposed_by: "Maker audit label",
    });
    expect(declaration.status).toBe(201);
    expect(declaration.body.status).toBe("draft");
    const declarationRoute = `system-of-record/${declaration.body.id}`;
    expect((await api(maker, `${declarationRoute}/approve`, "POST", { approved_by: "Pretend checker" })).status).toBe(404);
    expect((await api("viewer", `${declarationRoute}/approve`, "POST", { approved_by: "Pretend checker" })).status).toBe(404);
    const approved = await api(checker, `${declarationRoute}/approve`, "POST", { approved_by: "Separate authenticated checker" });
    expect(approved.status).toBe(200);
    expect(approved.body.status).toBe("approved");

    const book = await api("admin", "canonical-positions?limit=100");
    expect(book.status).toBe(200);
    const loan = book.body.positions.find((row: { position_type: string }) => row.position_type === "LOAN");
    expect(loan).toBeTruthy();
    const datedBookRoute = `canonical-positions?limit=100&as_of_date=${loan.as_of_date}`;
    const datedBook = await api("admin", datedBookRoute);
    const request = await api(maker, "canonical-withdrawals", "POST", {
      entity: "position", source_system: loan.source_system, as_of_date: loan.as_of_date,
      position_type: "LOAN", declaration_id: declaration.body.id,
      reason: "Disposable verification of withdrawal and reversal", requested_by: "Maker audit label",
    });
    expect(request.status).toBe(201);
    expect(request.body.status).toBe("pending");
    expect(request.body.rows_withdrawn).toBe(0);
    const withdrawalRoute = `canonical-withdrawals/${request.body.id}`;
    const before = counts();
    expect((await api(maker, `${withdrawalRoute}/approve`, "POST", { approved_by: "Another label cannot change identity" })).status).toBe(404);
    expect((await api("viewer", `${withdrawalRoute}/approve`, "POST", { approved_by: "Unprivileged reader" })).status).toBe(404);
    expect(counts()).toEqual(before);
    const applied = await api(checker, `${withdrawalRoute}/approve`, "POST", { approved_by: "Separate authenticated checker" });
    expect(applied.status).toBe(200);
    expect(applied.body.status).toBe("applied");
    expect(applied.body.rows_withdrawn).toBeGreaterThan(0);
    expect((await api("admin", datedBookRoute)).body.total).toBe(datedBook.body.total - applied.body.rows_withdrawn);
    expect((await api(maker, `${withdrawalRoute}/reverse`, "POST", { reversed_by: "Maker", reason: "Self reversal" })).status).toBe(404);
    const reversed = await api(checker, `${withdrawalRoute}/reverse`, "POST", { reversed_by: "Separate authenticated checker", reason: "Restore disposable book after the check" });
    expect(reversed.status).toBe(200);
    expect(reversed.body.status).toBe("reversed");
    expect(reversed.body.rows_restored).toBe(applied.body.rows_withdrawn);
    expect((await api("admin", datedBookRoute)).body.total).toBe(datedBook.body.total);
    const revoked = await api(checker, `${declarationRoute}/revoke`, "POST", { revoked_by: "Separate authenticated checker", reason: "Close disposable declaration" });
    expect(revoked.status).toBe(200);
    expect(revoked.body.revoked_at).toBeTruthy();
  });
});

test.describe("credential onboarding without live-test authority", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: { roles: ["viewer"], bindings: [{ bundle: "viewer", module: "data", sensitivity: "restricted" }] },
  });
  test.beforeEach(async ({ storyIdentity }) => {
    // Model the pre-cutover credential operator: legacy Analyst role plus exact
    // restricted VIEW, with no operational Analyst binding and no VALIDATE.
    const db = new DatabaseSync(path.join(E2E_TMP, "e2e.db"));
    try {
      db.prepare("UPDATE users SET role='analyst', authorization_version=authorization_version+1 WHERE id=?")
        .run(storyIdentity.actors[0].user_id.replaceAll("-", ""));
    } finally { db.close(); }
    E2E_USERS.viewer.roles = ["analyst"];
    await writeStorageState("viewer", E2E_BASE_URL, E2E_TMP);
  });
  test("saving database credentials succeeds without a test; disabled sibling controls explain Validate and opaque calls refuse", async ({ page }) => {
    const followups: string[] = [];
    page.on("request", req => { if (/\/database-direct\/connections\/[^/]+\/(test|schema)/.test(req.url())) followups.push(req.url()); });
    await page.goto("/data-engine/database");
    await page.getByRole("button", { name: "Connect a database", exact: true }).first().click();
    await page.getByLabel("Display name", { exact: true }).fill("Live saved credentials without Validate");
    await page.getByLabel("Host", { exact: true }).fill("replica.example.test");
    await page.getByLabel("Service user", { exact: true }).fill("DISPOSABLE_READONLY");
    await page.getByLabel("Password", { exact: true }).fill("disposable-fixture-password");
    await page.getByRole("button", { name: "Create connection", exact: true }).click();
    await expect(page.getByText("Connection saved. Live test omitted: requires DATA · Restricted · Validate.")).toBeVisible();
    expect(followups).toEqual([]);
    await shot(page, "database-credential-save-without-validate");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Live saved credentials without Validate", exact: true }) });
    await expect(card.getByRole("button", { name: "Test", exact: true })).toBeDisabled();
    await expect(card.getByRole("button", { name: "Discover schema", exact: true })).toBeDisabled();
    await expect(card.getByRole("button", { name: "Test", exact: true })).toHaveAccessibleDescription(/Requires DATA · Restricted · Validate/);
    await expect(card.getByText(/Requires DATA · Restricted · Validate to test this connection/)).toBeVisible();
    await shot(page, "database-controls-visible-validate-requirement");
    const connection = (await api("viewer", "database-direct/connections")).body.connections.find((row: { display_name: string }) => row.display_name === "Live saved credentials without Validate");
    const before = counts();
    expect((await api("viewer", `database-direct/connections/${connection.id}/test`, "POST", {})).status).toBe(404);
    expect((await api("viewer", `database-direct/connections/${connection.id}/schema`)).status).toBe(404);
    expect((await api("viewer", `database-direct/connections/${connection.id}/sync`, "POST", {})).status).toBe(404);
    expect(counts()).toEqual(before);
    expect(followups).toEqual([]);
  });

  test("Temenos credential save omits live testing and the availability control names Validate", async ({ page }) => {
    const followups: string[] = [];
    page.on("request", req => { if (/\/temenos\/connections\/[^/]+\/test/.test(req.url())) followups.push(req.url()); });
    await page.goto("/data-engine/t24");
    await page.getByRole("button", { name: "Configure a core", exact: true }).first().click();
    await page.getByRole("button", { name: /^OFS Open Financial Service/ }).click();
    await page.getByLabel("Display name", { exact: true }).fill("Live OFS without Validate");
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByLabel("Endpoint", { exact: true }).fill("ofs://replica.example.test");
    await page.getByLabel("Default currency", { exact: true }).fill("GHS");
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByLabel("Service user", { exact: true }).fill("DISPOSABLE_READONLY");
    await page.getByLabel("Service user password", { exact: true }).fill("disposable-fixture-password");
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByRole("button", { name: "Save configuration", exact: true }).click();
    await expect(page.getByText("Configuration saved. Live test omitted: requires DATA · Restricted · Validate.")).toBeVisible();
    expect(followups).toEqual([]);
    await shot(page, "temenos-credential-save-without-validate");
    await page.getByRole("button", { name: "Done", exact: true }).click();
    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Live OFS without Validate", exact: true }) });
    await expect(card.getByRole("button", { name: "Check availability", exact: true })).toBeDisabled();
    await expect(card.getByRole("button", { name: "Check availability", exact: true })).toHaveAccessibleDescription(/Requires DATA · Restricted · Validate/);
    await expect(card.getByText("Requires DATA · Restricted · Validate to check availability.")).toBeVisible();
    await shot(page, "temenos-visible-validate-requirement");
    const connection = (await api("viewer", "temenos/connections")).body.connections.find((row: { display_name: string }) => row.display_name === "Live OFS without Validate");
    const before = counts();
    expect((await api("viewer", `temenos/connections/${connection.id}/test`, "POST", {})).status).toBe(404);
    expect((await api("viewer", `temenos/connections/${connection.id}/pull`, "POST", {})).status).toBe(404);
    expect((await api("viewer", `temenos/connections/${connection.id}/backfill`, "POST", { start_date: "2026-01-01", end_date: "2026-01-02" })).status).toBe(404);
    expect(counts()).toEqual(before);
    expect(followups).toEqual([]);
  });
});
