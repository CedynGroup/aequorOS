import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_PASSWORD, E2E_USERS, mintBackendToken } from "./support/mint";

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

for (const missingAfterRefresh of [false, true]) {
  test(`a concurrent conflict ${missingAfterRefresh ? "keeps the administrator fallback when missing" : "links to the refreshed grant"}`, async ({
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
    let memberRefreshes = 0;
    let removedFromFixture = false;
    await page.route(`${API}/organization/members`, async (route) => {
      memberRefreshes += 1;
      if (missingAfterRefresh && !removedFromFixture) {
        // The refusal has already named this grant. Make the backing row
        // unavailable in the disposable SQLite fixture before the refresh,
        // then let the real API answer: no response payload is mocked.
        const db = new DatabaseSync(path.join(E2E_TMP, "e2e.db"));
        try {
          db.exec("PRAGMA foreign_keys = OFF");
          const deleted = db
            .prepare("DELETE FROM authorization_bindings WHERE id = ?")
            .run(competingBody.binding.id.replaceAll("-", ""));
          expect(deleted.changes).toBe(1);
          removedFromFixture = true;
        } finally {
          db.close();
        }
      }
      await route.continue();
    });
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
      expect(memberRefreshes).toBeGreaterThan(0);
      const conflictLink = composer.getByRole("button", {
        name: "View E2E Fx Member's Approver grant",
      });
      if (missingAfterRefresh) {
        await expect(conflictLink).toHaveCount(0);
        await expect(composer.getByRole("alert")).toContainText(
          "Ask an account administrator.",
        );
      } else {
        await expect(conflictLink).toBeVisible();
        await expect(composer.getByRole("alert")).not.toContainText(
          "Ask an account administrator.",
        );
      }
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
          path: path.join(
            evidenceDir,
            missingAfterRefresh
              ? "grant-create-race-fallback.png"
              : "grant-create-race-refusal.png",
          ),
        });
        await writeFile(
          path.join(
            evidenceDir,
            missingAfterRefresh
              ? "grant-create-race-fallback-response.json"
              : "grant-create-race-response.json",
          ),
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
      if (missingAfterRefresh) {
        await composer
          .getByRole("button", { name: "Back", exact: true })
          .click();
      } else {
        await conflictLink.click();
        const detail = page.getByRole("dialog", { name: "E2E Fx Member" });
        await expect(detail.getByTestId("focused-grant")).toContainText(
          "Approver in Regulatory Reporting for Sample Bank Ltd",
        );
        await expect(detail.getByTestId("focused-grant")).toContainText(
          "Granted by",
        );
        await expect(
          detail
            .getByTestId("focused-grant")
            .getByRole("button", { name: "Revoke this access" }),
        ).toBeVisible();
        await detail
          .getByRole("button", { name: "Back to your draft grant" })
          .click();
      }
      if (!missingAfterRefresh)
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
      await composer
        .getByLabel("Detail")
        .fill("New independent draft after refusal");
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
      if (!removedFromFixture) {
        const revoked = await page.request.post(
          `${API}/authorization/bindings/${competingBody.binding.id}/revoke`,
          {
            headers,
            data: { reason: "Clean up isolated race journey" },
          },
        );
        expect(revoked.ok()).toBeTruthy();
      }
    }
  });
}

test("the notice links to the conflicting grant and the draft survives revoking it", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 1100 });
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("admin")}`,
  };
  const member = E2E_USERS.sod_member;
  const approverPayload = {
    principal_user_id: member.id,
    role_bundle: "approver",
    institution_scope: "institution",
    institution_id: "BK-SAMP0001",
    module_scope: "reg",
    sensitivity_scope: "restricted",
    reason_category: "other",
    reason_detail: "Isolated conflicting-grant link journey",
  };
  const preview = await page.request.post(
    `${API}/authorization/bindings/preview`,
    { headers, data: approverPayload },
  );
  expect(preview.ok()).toBeTruthy();
  const created = await page.request.post(`${API}/authorization/bindings`, {
    headers,
    data: {
      ...approverPayload,
      expected_authority_sentence: (await preview.json()).authority_sentence,
    },
  });
  expect(created.status()).toBe(201);
  const approverId = (await created.json()).binding.id as string;
  try {
    await page.goto("/access/members");
    await page
      .locator("li")
      .filter({ hasText: "E2E Sod Member" })
      .first()
      .getByRole("button", { name: "Add grant" })
      .click();
    const composer = page.getByRole("dialog", {
      name: "Add grant for E2E Sod Member",
    });
    await composer.getByLabel("Role bundle").selectOption("validator");
    await composer.getByLabel("Module").selectOption("reg");
    await composer.getByLabel("Sensitivity").selectOption("restricted");
    await composer.getByLabel("Reason category").selectOption("other");
    await composer.getByLabel("Detail").fill("Files this quarter's returns");

    const notice = composer.getByRole("alert");
    await expect(notice).toContainText("This grant can't be given");
    const link = notice.getByRole("button", {
      name: "View E2E Sod Member's Approver grant",
    });
    await expect(link).toBeVisible();
    // No revoke inside the notice: it only opens the grant.
    await expect(notice.getByRole("button", { name: /revoke/i })).toHaveCount(
      0,
    );
    if (evidenceDir)
      await page.screenshot({
        path: path.join(evidenceDir, "grant-notice-link.png"),
      });

    // The link opens the existing grant in the member's detail, with its
    // scope, grantor and date, and leaves the draft waiting.
    await link.click();
    const detail = page.getByRole("dialog", { name: "E2E Sod Member" });
    await expect(composer).toBeHidden();
    const focused = detail.getByTestId("focused-grant");
    await expect(focused).toContainText(
      "E2E Sod Member is an Approver in Regulatory Reporting for Sample Bank Ltd",
    );
    await expect(focused).toContainText("Granted by");
    await expect(focused).toContainText("Granted");
    if (evidenceDir)
      await page.screenshot({
        path: path.join(evidenceDir, "grant-notice-linked-grant.png"),
      });

    // Back without revoking: the unfinished grant is exactly as it was.
    await detail
      .getByRole("button", { name: "Back to your draft grant" })
      .click();
    await expect(composer.getByLabel("Role bundle")).toHaveValue("validator");
    await expect(composer.getByLabel("Detail")).toHaveValue(
      "Files this quarter's returns",
    );
    await expect(notice).toContainText("This grant can't be given");

    // Revoke through the normal flow, then retry the same draft.
    await link.click();
    await detail
      .getByTestId("focused-grant")
      .getByRole("button", { name: "Revoke this access" })
      .click();
    const revoke = page.getByRole("dialog", { name: "Revoke access" });
    await revoke
      .getByLabel("Reason")
      .fill("Moving this person from approving to filing");
    if (evidenceDir)
      await page.screenshot({
        path: path.join(evidenceDir, "grant-linked-revoke-confirmation.png"),
      });
    await revoke.getByRole("button", { name: "Revoke access" }).click();
    await expect(composer).toBeVisible();
    await expect(composer.getByLabel("Role bundle")).toHaveValue("validator");
    await expect(composer.getByLabel("Detail")).toHaveValue(
      "Files this quarter's returns",
    );
    await expect(composer.getByRole("alert")).toHaveCount(0);
    await expect(
      composer.getByRole("button", { name: "Review grant" }),
    ).toBeEnabled();
    const db = new DatabaseSync(path.join(E2E_TMP, "e2e.db"), {
      readOnly: true,
    });
    try {
      const audit = db
        .prepare(
          "SELECT actor_user_id, event_type, entity_id, details FROM audit_events " +
            "WHERE event_type = 'authorization.binding_revoked' AND entity_id = ?",
        )
        .get(approverId);
      expect(audit?.actor_user_id).toBe(E2E_USERS.admin.id.replaceAll("-", ""));
      if (typeof audit?.details !== "string")
        throw new Error(
          "The linked revocation did not persist its audit details",
        );
      const details: unknown = JSON.parse(audit.details);
      expect(details).toMatchObject({
        grantee_user_id: member.id,
        role_bundle: "approver",
        scope: { module_scope: "reg", institution_id: "BK-SAMP0001" },
        reason: "Moving this person from approving to filing",
      });
      if (evidenceDir)
        await writeFile(
          path.join(evidenceDir, "grant-linked-revoke-audit.json"),
          JSON.stringify({ ...audit, details }, null, 2),
        );
    } finally {
      db.close();
    }
    await composer.getByRole("button", { name: "Cancel" }).click();

    // The revoked grant's history reads every timestamp as a local date and
    // time; the nullable ones used to show as raw ISO text.
    await page.getByRole("button", { name: "View E2E Sod Member" }).click();
    const revokedGrant = page
      .getByRole("dialog", { name: "E2E Sod Member" })
      .locator("li")
      .filter({ hasText: "Moving this person from approving to filing" });
    await expect(revokedGrant).toContainText("Revoked");
    await expect(revokedGrant).not.toContainText(/\d{4}-\d{2}-\d{2}T\d{2}:/);
    if (evidenceDir) {
      await revokedGrant.scrollIntoViewIfNeeded();
      await page.screenshot({
        path: path.join(evidenceDir, "grant-history-revoked.png"),
      });
    }
  } finally {
    const listed = await page.request.get(
      `${API}/authorization/bindings?principal_user_id=${member.id}`,
      { headers },
    );
    const stillActive = (
      (await listed.json()).bindings as {
        id: string;
        status: string;
      }[]
    ).some(
      (binding) => binding.id === approverId && binding.status === "active",
    );
    if (stillActive) {
      const cleanup = await page.request.post(
        `${API}/authorization/bindings/${approverId}/revoke`,
        { headers, data: { reason: "Clean up isolated link journey" } },
      );
      expect(cleanup.ok()).toBeTruthy();
    }
  }
});

test("a self-revocation draft survives reauthentication and clears on submit or cancel", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 1280, height: 1100 });
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("sod_owner")}`,
  };
  const payload = {
    principal_user_id: E2E_USERS.sod_owner.id,
    role_bundle: "account_admin",
    institution_scope: "organization",
    module_scope: "account",
    sensitivity_scope: "all",
    reason_category: "other",
    reason_detail: "Isolated self-revocation draft journey",
  };
  const preview = await page.request.post(
    `${API}/authorization/bindings/preview`,
    { headers, data: payload },
  );
  expect(preview.ok()).toBeTruthy();
  const created = await page.request.post(`${API}/authorization/bindings`, {
    headers,
    data: {
      ...payload,
      expected_authority_sentence: (await preview.json()).authority_sentence,
    },
  });
  expect(created.status()).toBe(201);
  const adminId = (await created.json()).binding.id as string;
  let revoked = false;
  let analystId: string | undefined;
  const signIn = async () => {
    await page.goto("/login?reason=access_changed");
    await page
      .getByLabel("Email", { exact: true })
      .fill("e2e.sod_owner@aequoros.example");
    await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto("/access/members");
  };
  try {
    await context.clearCookies();
    await signIn();
    await page
      .locator("li")
      .filter({ hasText: "E2E Sod Owner" })
      .first()
      .getByRole("button", { name: "Add grant" })
      .click();
    const composer = page.getByRole("dialog", {
      name: "Add grant for E2E Sod Owner",
    });
    await composer.getByLabel("Role bundle").selectOption("analyst");
    await composer.getByLabel("Module").selectOption("reg");
    await composer.getByLabel("Sensitivity").selectOption("restricted");
    await composer.getByLabel("Reason category").selectOption("other");
    await composer
      .getByLabel("Detail")
      .fill("Keep this draft while removing delegated administration");
    await composer.getByLabel("Reference").fill("SOD-SELF-1");
    await composer
      .getByRole("button", {
        name: "View E2E Sod Owner's Organization Administrator grant",
      })
      .click();
    await page
      .getByRole("dialog", { name: "E2E Sod Owner", exact: true })
      .getByTestId("focused-grant")
      .getByRole("button", { name: "Revoke this access" })
      .click();
    const revokeDialog = page.getByRole("dialog", { name: "Revoke access" });
    await revokeDialog
      .getByLabel("Reason")
      .fill(
        "Remove my delegated administration before taking operational work",
      );
    const revokeResponse = page.waitForResponse(
      (response) =>
        response.url() === `${API}/authorization/bindings/${adminId}/revoke` &&
        response.request().method() === "POST",
    );
    const staleMembers = page.waitForResponse(
      (response) =>
        response.url() === `${API}/organization/members` &&
        response.status() === 401,
    );
    await revokeDialog
      .getByRole("button", { name: "Revoke access", exact: true })
      .click();
    expect((await revokeResponse).status()).toBe(200);
    revoked = true;
    await staleMembers;
    await expect(composer).toHaveCount(0);
    await signIn();
    await expect(composer).toBeVisible();
    await expect(composer.getByLabel("Role bundle")).toHaveValue("analyst");
    await expect(composer.getByLabel("Module")).toHaveValue("reg");
    await expect(composer.getByLabel("Sensitivity")).toHaveValue("restricted");
    await expect(composer.getByLabel("Detail")).toHaveValue(
      "Keep this draft while removing delegated administration",
    );
    await expect(composer.getByLabel("Reference")).toHaveValue("SOD-SELF-1");
    await expect(composer.getByRole("alert")).toHaveCount(0);
    if (evidenceDir)
      await page.screenshot({
        path: path.join(
          evidenceDir,
          "grant-self-revocation-restored-draft.png",
        ),
      });
    await composer.getByRole("button", { name: "Review grant" }).click();
    const submitted = page.waitForResponse(
      (response) =>
        response.url() === `${API}/authorization/bindings` &&
        response.request().method() === "POST",
    );
    await composer.getByRole("button", { name: "Grant access" }).click();
    const submittedResponse = await submitted;
    expect(submittedResponse.status()).toBe(201);
    analystId = (await submittedResponse.json()).binding.id;
    if (evidenceDir)
      await writeFile(
        path.join(evidenceDir, "grant-self-revocation-submitted.json"),
        JSON.stringify(
          {
            status: submittedResponse.status(),
            body: await submittedResponse.json(),
          },
          null,
          2,
        ),
      );
    await signIn();
    await expect(composer).toHaveCount(0);
    await page
      .locator("li")
      .filter({ hasText: "E2E Sod Owner" })
      .first()
      .getByRole("button", { name: "Add grant" })
      .click();
    await composer
      .getByLabel("Detail")
      .fill("A cancelled draft should not return");
    await composer.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Add grant", exact: true }),
    ).toBeVisible();
    await expect(composer).toHaveCount(0);
  } finally {
    const currentHeaders = {
      Authorization: `Bearer ${await mintBackendToken("sod_owner", revoked ? (analystId ? 7 : 6) : 5)}`,
    };
    for (const id of [analystId, revoked ? undefined : adminId]) {
      if (id) {
        const cleanup = await page.request.post(
          `${API}/authorization/bindings/${id}/revoke`,
          {
            headers: currentHeaders,
            data: { reason: "Clean up isolated self-revocation journey" },
          },
        );
        expect(cleanup.ok()).toBeTruthy();
      }
    }
  }
});
