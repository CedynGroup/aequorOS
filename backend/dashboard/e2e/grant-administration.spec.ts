import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;
const ownerState = path.join(E2E_TMP, "admin.json");
const member = E2E_USERS.grant_member;
const targetSentence =
  "E2E Grant Member is an Analyst in Liquidity Monitoring for Sample Bank Ltd, covering Confidential data.";

test.describe("scoped grant administration", () => {
  test.use({ storageState: ownerState });

  test("an owner composes one exact grant and revokes only that access", async ({
    page,
  }) => {
    const ownerToken = await mintBackendToken("admin");
    const ownerHeaders = { Authorization: `Bearer ${ownerToken}` };

    // Establish one unrelated row so the browser journey proves revocation is
    // one-binding-only rather than merely observing an empty end state.
    const unrelatedPayload = {
      principal_user_id: member.id,
      role_bundle: "viewer",
      institution_scope: "institution",
      institution_id: "BK-SAMP0001",
      module_scope: "reg",
      sensitivity_scope: "restricted",
      reason: "Keep independent regulatory review access",
    };
    const unrelatedPreview = await page.request.post(
      `${API}/authorization/bindings/preview`,
      {
        headers: ownerHeaders,
        data: unrelatedPayload,
      },
    );
    expect(unrelatedPreview.ok()).toBeTruthy();
    const unrelated = await page.request.post(`${API}/authorization/bindings`, {
      headers: ownerHeaders,
      data: {
        ...unrelatedPayload,
        expected_authority_sentence: (await unrelatedPreview.json())
          .authority_sentence,
      },
    });
    expect(unrelated.status()).toBe(201);

    // The composer's institution list must come from the account-plane
    // directory, never from the operational bank list the owner can view.
    const catalogueRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/organization\/institutions(\?|$)/.test(request.url())) {
        catalogueRequests.push(request.url());
      }
    });

    await page.goto("/settings");
    const memberRow = page
      .locator("li")
      .filter({ hasText: "E2E Grant Member" })
      .first();
    // Every active human carries the system-managed baseline `member` sentence
    // (#201) beside whatever is granted, so the unrelated Viewer row makes two.
    await expect(memberRow).toContainText("2 grants");
    await expect(memberRow).toContainText(
      "Viewer · Regulatory Reporting · Sample Bank Ltd",
    );
    await memberRow.getByRole("button", { name: "Add grant" }).click();

    const composer = page.getByRole("dialog", {
      name: "Add grant for E2E Grant Member",
    });
    await composer.getByLabel("Role bundle").selectOption("analyst");
    await expect.poll(() => catalogueRequests.length).toBeGreaterThan(0);
    await expect(
      composer.getByLabel("Institution coverage").locator("option"),
    ).toContainText(["Every institution in the organization", "Sample Bank Ltd"]);
    await composer
      .getByLabel("Institution coverage")
      .selectOption("BK-SAMP0001");
    await composer.getByLabel("Module").selectOption("liq");
    await composer.getByLabel("Sensitivity").selectOption("confidential");
    await composer
      .getByLabel("Reason")
      .fill("Treasury monitoring responsibilities approved for this officer");
    await expect(
      composer.getByText(targetSentence, { exact: true }),
    ).toBeVisible();
    await composer.getByRole("button", { name: "Review grant" }).click();
    await expect(
      composer.getByText(targetSentence, { exact: true }),
    ).toBeVisible();
    await composer.getByRole("button", { name: "Grant access" }).click();
    await expect(
      composer.getByText("Grant created", { exact: true }),
    ).toBeVisible();
    await expect(
      composer.getByText(targetSentence, { exact: true }),
    ).toBeVisible();

    const listedAfterGrant = await page.request.get(
      `${API}/authorization/bindings?principal_user_id=${member.id}`,
      { headers: ownerHeaders },
    );
    expect(listedAfterGrant.ok()).toBeTruthy();
    const createdRows = (await listedAfterGrant.json()).bindings as Array<{
      id: string;
      role_bundle: string;
      institution_scope: string;
      institution_id: string | null;
      module_scope: string;
      sensitivity_scope: string;
      status: string;
    }>;
    // Baseline member + the unrelated Viewer + the grant just composed.
    expect(createdRows).toHaveLength(3);
    expect(createdRows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role_bundle: "analyst",
          institution_scope: "institution",
          institution_id: "BK-SAMP0001",
          module_scope: "liq",
          sensitivity_scope: "confidential",
          status: "active",
        }),
        expect.objectContaining({
          role_bundle: "viewer",
          institution_scope: "institution",
          institution_id: "BK-SAMP0001",
          module_scope: "reg",
          sensitivity_scope: "restricted",
          status: "active",
        }),
      ]),
    );

    // Baseline membership (#201) took the member from authv 1 to 2 at
    // bootstrap, and the two grants above advanced it to 4. This represents
    // their current signed-in session immediately before revoke.
    const currentMemberToken = await mintBackendToken("grant_member", 4);
    const beforeRevoke = await page.request.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${currentMemberToken}` },
    });
    expect(beforeRevoke.ok()).toBeTruthy();

    await composer.getByRole("button", { name: "Done" }).click();
    await memberRow
      .getByRole("button", { name: "View E2E Grant Member" })
      .click();
    const detail = page.getByRole("dialog", { name: "E2E Grant Member" });
    const targetGrant = detail
      .locator("li")
      .filter({ hasText: targetSentence });
    await targetGrant
      .getByRole("button", { name: "Revoke this access" })
      .click();

    const revocation = page.getByRole("dialog", { name: "Revoke access" });
    await expect(
      revocation.getByText(targetSentence, { exact: true }),
    ).toBeVisible();
    await expect(revocation).toContainText(
      "Their current AequorOS sign-ins end and they will be asked to sign in again. Their other grants stay active.",
    );
    await revocation
      .getByLabel("Reason")
      .fill(
        "Liquidity monitoring responsibility transferred to another officer",
      );
    await revocation.getByRole("button", { name: "Revoke access" }).click();
    await expect(revocation).toBeHidden();

    const nextAction = await page.request.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${currentMemberToken}` },
    });
    expect(nextAction.status()).toBe(401);
    await expect(nextAction.json()).resolves.toMatchObject({
      error: { message: expect.stringMatching(/sign in again/i) },
    });

    const listedAfterRevoke = await page.request.get(
      `${API}/authorization/bindings?principal_user_id=${member.id}`,
      { headers: ownerHeaders },
    );
    const finalRows = (await listedAfterRevoke.json()).bindings as Array<{
      role_bundle: string;
      module_scope: string;
      sensitivity_scope: string;
      status: string;
    }>;
    expect(finalRows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role_bundle: "analyst",
          module_scope: "liq",
          sensitivity_scope: "confidential",
          status: "revoked",
        }),
        expect.objectContaining({
          role_bundle: "viewer",
          module_scope: "reg",
          sensitivity_scope: "restricted",
          status: "active",
        }),
      ]),
    );
    // Only the one grant was revoked: the baseline sentence and the unrelated
    // Viewer row remain.
    await expect(memberRow).toContainText("2 grants");
    await expect(memberRow).toContainText(
      "Viewer · Regulatory Reporting · Sample Bank Ltd",
    );
  });
});

test.describe("separation-of-duties at composition time", () => {
  test.use({ storageState: ownerState });

  test("a blocked combination is refused at Define with the policy reason", async ({
    page,
  }) => {
    // E2E Account_admin already administers the account; an operational maker
    // bundle on the same identity is rule C9's hard block. The composer must
    // say so before Review — never walk the owner to a 409 on Grant access.
    await page.goto("/settings");
    const memberRow = page
      .locator("li")
      .filter({ hasText: "E2E Account_admin" })
      .first();
    await memberRow.getByRole("button", { name: "Add grant" }).click();
    const composer = page.getByRole("dialog", {
      name: "Add grant for E2E Account_admin",
    });
    await composer.getByLabel("Role bundle").selectOption("analyst");
    await composer.getByLabel("Reason").fill("Attempted maker authority");
    await expect(composer.getByRole("alert")).toContainText(
      "refused by separation-of-duties policy",
    );
    await expect(composer.getByRole("alert")).toContainText(
      "Account administration and operational maker/checker authority must remain separated",
    );
    await expect(
      composer.getByRole("button", { name: "Cannot be granted" }),
    ).toBeDisabled();
    await expect(
      composer.getByRole("button", { name: "Review grant" }),
    ).toHaveCount(0);

    // Switching to a read-only bundle clears the block on the same identity.
    await composer.getByLabel("Role bundle").selectOption("viewer");
    await expect(composer.getByRole("alert")).toHaveCount(0);
    await expect(
      composer.getByRole("button", { name: "Review grant" }),
    ).toBeEnabled();
    await composer.getByRole("button", { name: "Cancel" }).click();
  });
});
