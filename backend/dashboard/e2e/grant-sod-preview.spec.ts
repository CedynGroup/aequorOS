import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;
const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const filingMessage =
  "E2E Fx Member already has the Approver grant (Regulatory Reporting, Sample Bank Ltd). " +
  "Approver and Validator roles must stay with different people, whatever the scope. " +
  "Remove the Approver grant first, or choose someone else.";

test.use({ storageState: path.join(E2E_TMP, "admin.json") });

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

test("reverse account administration conflict shows the server finding at Define", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 1100 });
  await page.goto("/access/members");
  await page
    .locator("li")
    .filter({ hasText: "E2E Approver" })
    .first()
    .getByRole("button", { name: "Add grant" })
    .click();
  const composer = page.getByRole("dialog", {
    name: "Add grant for E2E Approver",
  });
  await composer.getByLabel("Role bundle").selectOption("account_admin");
  await composer.getByLabel("Reason category").selectOption("other");
  await composer
    .getByLabel("Detail")
    .fill("Exercise reverse account administration conflict");
  // The reverse direction names the operational grant the member holds,
  // never account administration they do not have.
  await expect(composer.getByRole("alert")).toContainText(
    "This grant can't be given",
  );
  await expect(composer.getByRole("alert")).toContainText(
    "E2E Approver already has the Approver grant (all modules, every institution). " +
      "Making E2E Approver an Organization Administrator would let one person both do " +
      "operational work and decide who has access to it. Remove the Approver grant first, " +
      "or choose someone else.",
  );
  await expect(
    composer.getByRole("button", { name: "Cannot be granted" }),
  ).toBeDisabled();
  await expect(composer).not.toContainText(
    "revoke this identity's account administration first",
  );
  if (evidenceDir)
    await page.screenshot({
      path: path.join(evidenceDir, "grant-reverse-c9-define.png"),
    });
});

test("a conflicting grant added after Review is refused with the server's finding", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 1100 });
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("admin")}`,
  };
  await page.goto("/access/members");
  await page
    .locator("li")
    .filter({ hasText: "E2E Fx Member" })
    .first()
    .getByRole("button", { name: "Add grant" })
    .click();
  const composer = page.getByRole("dialog", {
    name: "Add grant for E2E Fx Member",
  });
  await composer.getByLabel("Role bundle").selectOption("validator");
  await composer.getByLabel("Module").selectOption("reg");
  await composer.getByLabel("Sensitivity").selectOption("restricted");
  await composer.getByLabel("Reason category").selectOption("other");
  await composer
    .getByLabel("Detail")
    .fill("Filing responsibilities pending independent checker allocation");
  await composer.getByRole("button", { name: "Review grant" }).click();
  await expect(composer.getByRole("alert")).toHaveCount(0);
  const competingPayload = {
    principal_user_id: E2E_USERS.fx_member.id,
    role_bundle: "approver",
    institution_scope: "institution",
    institution_id: "BK-SAMP0001",
    module_scope: "reg",
    sensitivity_scope: "restricted",
    reason_category: "other",
    reason_detail: "Concurrent checker allocation in isolated race journey",
  };
  const preview = await page.request.post(
    `${API}/authorization/bindings/preview`,
    { headers, data: competingPayload },
  );
  expect(preview.ok()).toBeTruthy();
  const competing = await page.request.post(`${API}/authorization/bindings`, {
    headers,
    data: {
      ...competingPayload,
      expected_authority_sentence: (await preview.json()).authority_sentence,
    },
  });
  expect(competing.status()).toBe(201);
  const competingBody = await competing.json();
  let viewerBindingId: string | undefined;
  try {
    const refused = page.waitForResponse(
      (response) =>
        response.url() === `${API}/authorization/bindings` &&
        response.request().method() === "POST",
    );
    await composer.getByRole("button", { name: "Grant access" }).click();
    const response = await refused;
    expect(response.status()).toBe(409);
    // The refused create's own findings replace the preview's, in the same
    // single notice; no second, generic refusal is shown beside it.
    await expect(composer.getByRole("alert")).toHaveCount(1);
    await expect(composer.getByRole("alert")).toContainText(
      "This grant can't be given",
    );
    await expect(composer.getByRole("alert")).toContainText(filingMessage);
    await expect(
      composer.getByRole("button", { name: "Grant access" }),
    ).toBeDisabled();
    await expect(
      composer.getByText("Grant created", { exact: true }),
    ).toHaveCount(0);
    const listed = await page.request.get(
      `${API}/authorization/bindings?principal_user_id=${E2E_USERS.fx_member.id}`,
      { headers },
    );
    expect(listed.ok()).toBeTruthy();
    expect(
      (await listed.json()).bindings.some(
        (binding: { role_bundle: string }) =>
          binding.role_bundle === "validator",
      ),
    ).toBeFalsy();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "grant-create-race-refusal.png"),
      });
      await writeFile(
        path.join(evidenceDir, "grant-create-race-response.json"),
        JSON.stringify(
          {
            status: response.status(),
            body: await response.json(),
            validatorBindingCreated: false,
          },
          null,
          2,
        ),
      );
    }
    await composer.getByRole("button", { name: "Back", exact: true }).click();
    await expect(
      composer.getByRole("button", { name: "Cannot be granted" }),
    ).toBeDisabled();
    await composer.getByLabel("Role bundle").selectOption("viewer");
    await composer.getByRole("button", { name: "Review grant" }).click();
    await expect(composer.getByRole("alert")).toHaveCount(0);
    await expect(composer).not.toContainText(filingMessage);
    const allowed = page.waitForResponse(
      (response) =>
        response.url() === `${API}/authorization/bindings` &&
        response.request().method() === "POST",
    );
    await composer.getByRole("button", { name: "Grant access" }).click();
    const allowedResponse = await allowed;
    expect(allowedResponse.status()).toBe(201);
    viewerBindingId = (await allowedResponse.json()).binding.id;
    await expect(
      composer.getByText("Grant created", { exact: true }),
    ).toBeVisible();
    await composer.getByRole("button", { name: "Add another grant" }).click();
    await composer.getByLabel("Role bundle").selectOption("viewer");
    await composer.getByLabel("Reason category").selectOption("other");
    await composer.getByLabel("Detail").fill("New independent draft after refusal");
    await composer.getByRole("button", { name: "Review grant" }).click();
    await expect(composer.getByRole("alert")).toHaveCount(0);
    await expect(composer).not.toContainText(filingMessage);
  } finally {
    if (viewerBindingId) {
      const revokedViewer = await page.request.post(
        `${API}/authorization/bindings/${viewerBindingId}/revoke`,
        {
          headers,
          data: { reason: "Clean up replacement draft journey" },
        },
      );
      expect(revokedViewer.ok()).toBeTruthy();
    }
    const revoked = await page.request.post(
      `${API}/authorization/bindings/${competingBody.binding.id}/revoke`,
      {
        headers,
        data: { reason: "Clean up isolated race journey" },
      },
    );
    expect(revoked.ok()).toBeTruthy();
  }
});
