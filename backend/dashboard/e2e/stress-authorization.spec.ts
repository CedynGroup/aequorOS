/** Real grant lifecycle against the disposable backend; no response interception. */
import { expect } from "@playwright/test";
import { test } from "./support/story-identities";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken, writeStorageState } from "./support/mint";

const evidence = process.env.E2E_EVIDENCE_DIR;
const bank = "/banks/BK-SAMP0001";
const stressRequest =
  /\/(?:(?:macro-scenarios|management-action-plans)(?:\/|\?|$)|banks\/[^/]+\/enterprise-stress(?:\/|\?|$))/;
// Revoke every grant this journey creates. Revoked rows stay in a member's
// history, so the stress member is an identity no other journey grants or counts.
const createdBindings: string[] = [];
test.use({
  storyConfig: {
    roles: ["macro_viewer"],
    bindings: [
      {
        role: "macro_viewer",
        bundle: "viewer",
        module: "data",
        sensitivity: "confidential",
      },
      {
        role: "macro_viewer",
        bundle: "viewer",
        module: "irrbb",
        sensitivity: "confidential",
      },
    ],
  },
});

test.afterEach(async ({ request }) => {
  const token = await mintBackendToken("admin");
  for (const id of createdBindings.splice(0).reverse()) {
    const revoked = await request.post(
      `${E2E_API_ORIGIN}/api/v1/authorization/bindings/${id}/revoke`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { reason: "Remove stress verification grant" },
      },
    );
    expect(revoked.status(), await revoked.text()).toBe(200);
  }
});

test("a member without risk bindings keeps the stress workbench dark", async ({
  browser,
}) => {
  const state = await writeStorageState("macro_viewer", E2E_BASE_URL, E2E_TMP);
  const context = await browser.newContext({ storageState: state });
  const page = await context.newPage();
  const requests: string[] = [];
  page.on("request", (r) => {
    if (stressRequest.test(r.url())) requests.push(r.url());
  });

  await page.goto("/irr/enterprise-stress");
  await expect(
    page.getByText("Stress access required", { exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await expect(
    page.getByText(
      "Requires Risk · Confidential · view. Ask your organization owner or admin for access.",
      { exact: true },
    ),
  ).toBeVisible();
  expect(requests).toEqual([]);
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "stress-unbound.png"),
      fullPage: true,
    });
  await context.close();
});

test("real Risk grants separate confidential reading from governed mutation and run", async ({
  browser,
  request,
}) => {
  test.slow();
  async function api(
    method: string,
    route: string,
    data?: unknown,
    role = "macro_viewer",
  ) {
    const response = await request.fetch(`${E2E_API_ORIGIN}/api/v1${route}`, {
      method,
      data,
      headers: {
        Authorization: `Bearer ${await mintBackendToken(role)}`,
      },
    });
    const body = await response.json();
    return { status: response.status(), body };
  }
  async function grant(
    bundle: "viewer" | "analyst",
    scope: "institution" | "organization" = "institution",
    module: "risk" | "irrbb" | "fx" = "risk",
  ) {
    const data = {
      principal_user_id: E2E_USERS.macro_viewer.id,
      role_bundle: bundle,
      institution_scope: scope,
      institution_id: scope === "institution" ? "BK-SAMP0001" : null,
      module_scope: module,
      sensitivity_scope: "confidential",
      reason_category: "other",
      reason_detail: "Live stress authorization verification",
    };
    const preview = await api(
      "POST",
      "/authorization/bindings/preview",
      data,
      "admin",
    );
    expect(preview.status, JSON.stringify(preview.body)).toBe(200);
    const created = await api(
      "POST",
      "/authorization/bindings",
      { ...data, expected_authority_sentence: preview.body.authority_sentence },
      "admin",
    );
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    createdBindings.push(created.body.binding.id);
  }
  async function readerPage() {
    const state = await writeStorageState(
      "macro_viewer",
      E2E_BASE_URL,
      E2E_TMP,
    );
    const context = await browser.newContext({ storageState: state });
    return { context, page: await context.newPage() };
  }

  // Scalar membership is not authority: the stress run route refuses before
  // touching any row, even with a syntactically valid request body (#178).
  const refusedBody = {
    scenario_id: "00000000-0000-4000-8000-000000000000",
    reporting_period_id: "00000000-0000-4000-8000-000000000001",
    reason: "Authorization boundary probe",
  };
  expect(
    (await api("POST", `${bank}/enterprise-stress/runs`, refusedBody)).status,
  ).toBe(403);

  await grant("viewer");
  // A bank binding reads that bank's rows, never the organization catalogue.
  const listing = await api("GET", "/macro-scenarios");
  expect(listing.status).toBe(200);
  expect(
    (listing.body.scenarios as { bank_id: string | null }[]).every(
      (scenario) => scenario.bank_id === "BK-SAMP0001",
    ),
  ).toBe(true);
  // Execution stays denied for a viewer bundle.
  expect(
    (await api("POST", `${bank}/enterprise-stress/runs`, refusedBody)).status,
  ).toBe(403);
  {
    const { context, page } = await readerPage();
    await page.goto("/irr/enterprise-stress");
    await expect(
      page.getByText("Stress access required", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Scenario library" }),
    ).toBeVisible({ timeout: 60000 });
    const create = page.getByRole("button", { name: "New scenario" });
    await expect(create).toBeDisabled();
    await create.locator("..").focus();
    await expect(create).toHaveAccessibleDescription(
      "Requires Risk · Confidential · create. Ask your organization owner or admin for access.",
    );
    const run = page.getByRole("button", { name: "Run enterprise stress" });
    await expect(run).toBeDisabled();
    await run.locator("..").focus();
    await expect(run).toHaveAccessibleDescription(
      "Requires Risk · Confidential · run. Ask your organization owner or admin for access.",
    );
    if (evidence)
      await page.screenshot({
        path: path.join(evidence, "stress-viewer-bound.png"),
        fullPage: true,
      });
    await context.close();
  }

  await grant("viewer", "organization");
  const catalogue = await api("GET", "/macro-scenarios");
  expect(catalogue.status).toBe(200);
  const runnable = (
    catalogue.body.scenarios as {
      id: string;
      owner: string;
      is_runnable: boolean;
    }[]
  ).find((scenario) => scenario.owner === "system" && scenario.is_runnable);
  expect(runnable).toBeDefined();
  expect(
    (
      await api("POST", `/macro-scenarios/${runnable!.id}/clone`, {
        reason: "Clone boundary probe",
      })
    ).status,
  ).toBe(403);

  await grant("analyst", "organization");
  await grant("analyst", "institution", "irrbb");
  await grant("analyst", "institution", "fx");
  // The analyst bundle unlocks governed execution through the same route.
  const periods = await api(
    "GET",
    `${bank}/reporting-periods`,
    undefined,
    "admin",
  );
  expect(periods.status).toBe(200);
  const run = await api("POST", `${bank}/enterprise-stress/runs`, {
    scenario_id: runnable!.id,
    reporting_period_id: periods.body.periods[0].id,
    reason: "Live stress authorization verification run",
  });
  expect(run.status, JSON.stringify(run.body)).toBe(201);
  {
    const { context, page } = await readerPage();
    await page.goto("/irr/enterprise-stress");
    await expect(
      page.getByRole("button", { name: "New scenario" }),
    ).toBeEnabled({ timeout: 60000 });
    if (evidence)
      await page.screenshot({
        path: path.join(evidence, "stress-analyst-bound.png"),
        fullPage: true,
      });
    await context.close();
  }
});
