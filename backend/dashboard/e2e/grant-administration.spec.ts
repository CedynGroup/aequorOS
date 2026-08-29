import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

const API = "http://127.0.0.1:8021/api/v1";
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
    const unrelated = await page.request.post(`${API}/authorization/bindings`, {
      headers: ownerHeaders,
      data: {
        principal_user_id: member.id,
        role_bundle: "viewer",
        institution_scope: "institution",
        institution_id: "BK-SAMP0001",
        module_scope: "reg",
        sensitivity_scope: "restricted",
        reason: "Keep independent regulatory review access",
      },
    });
    expect(unrelated.status()).toBe(201);

    await page.goto("/settings");
    const memberRow = page.locator("li").filter({ hasText: "E2E Grant Member" }).first();
    await expect(memberRow).toContainText("1 grant");
    await expect(memberRow).toContainText(
      "Viewer · Regulatory Reporting · Sample Bank Ltd",
    );
    await memberRow.getByRole("button", { name: "Add grant" }).click();

    const composer = page.getByRole("dialog", {
      name: "Add grant for E2E Grant Member",
    });
    await composer.getByLabel("Role bundle").selectOption("analyst");
    await composer.getByLabel("Institution coverage").selectOption("BK-SAMP0001");
    await composer.getByLabel("Module").selectOption("liq");
    await composer.getByLabel("Sensitivity").selectOption("confidential");
    await composer
      .getByLabel("Reason")
      .fill("Treasury monitoring responsibilities approved for this officer");
    await expect(composer.getByText(targetSentence, { exact: true })).toBeVisible();
    await composer.getByRole("button", { name: "Review grant" }).click();
    await expect(composer.getByText(targetSentence, { exact: true })).toBeVisible();
    await composer.getByRole("button", { name: "Grant access" }).click();
    await expect(composer.getByText("Grant created", { exact: true })).toBeVisible();
    await expect(composer.getByText(targetSentence, { exact: true })).toBeVisible();

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
    expect(createdRows).toHaveLength(2);
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

    // Both grants have now advanced the member from authv 1 to authv 3. This
    // represents their current signed-in session immediately before revoke.
    const currentMemberToken = await mintBackendToken("grant_member", 3);
    const beforeRevoke = await page.request.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${currentMemberToken}` },
    });
    expect(beforeRevoke.ok()).toBeTruthy();

    await composer.getByRole("button", { name: "Done" }).click();
    await memberRow.getByRole("button", { name: "View E2E Grant Member" }).click();
    const detail = page.getByRole("dialog", { name: "E2E Grant Member" });
    const targetGrant = detail.locator("li").filter({ hasText: targetSentence });
    await targetGrant.getByRole("button", { name: "Revoke this access" }).click();

    const revocation = page.getByRole("dialog", { name: "Revoke access" });
    await expect(revocation.getByText(targetSentence, { exact: true })).toBeVisible();
    await expect(revocation).toContainText(
      "Their current AequorOS sign-ins end and they will be asked to sign in again. Their other grants stay active.",
    );
    await revocation
      .getByLabel("Reason")
      .fill("Liquidity monitoring responsibility transferred to another officer");
    await revocation.getByRole("button", { name: "Revoke access" }).click();

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
    await expect(memberRow).toContainText("1 grant");
    await expect(memberRow).toContainText(
      "Viewer · Regulatory Reporting · Sample Bank Ltd",
    );
  });
});
