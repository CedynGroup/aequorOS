import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const API = `${E2E_API_ORIGIN}/api/v1`;
const ownerState = path.join(E2E_TMP, "admin.json");
const member = E2E_USERS.grant_member;
/** The active-grant count the Members row renders for one member. */
async function activeGrantCount(
  memberRow: ReturnType<Page["locator"]>,
): Promise<number> {
  const label = await memberRow.getByText(/^\d+ grants?$/).textContent();
  return Number.parseInt(label ?? "", 10);
}

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
      reason_category: "other",
      reason_detail: "Keep independent regulatory review access",
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

    await page.goto("/access/members");
    const memberRow = page
      .locator("li")
      .filter({ hasText: "E2E Grant Member" })
      .first();
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
    ).toContainText([
      "Every institution in the organization",
      "Sample Bank Ltd",
    ]);
    await composer
      .getByLabel("Institution coverage")
      .selectOption("BK-SAMP0001");
    await composer.getByLabel("Module").selectOption("liq");
    await composer.getByLabel("Sensitivity").selectOption("confidential");
    await composer.getByLabel("Reason category").selectOption("other");
    await composer
      .getByLabel("Detail")
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

    // Both grants have advanced the baseline member from authv 2 to authv 4. This
    // represents their current signed-in session immediately before revoke.
    const currentMemberToken = await mintBackendToken(
      "grant_member",
      member.authv + 2,
    );
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
    await expect(memberRow).toContainText("2 grants");
    await expect(memberRow).toContainText(
      "Viewer · Regulatory Reporting · Sample Bank Ltd",
    );
  });

  // Credit and Institution are grantable vocabulary ahead of their cutovers:
  // the composer offers them, the server composes the sentence, and the saved
  // grant lists under the member — while no product surface consumes it yet.
  test("the composer offers Credit and Institution Profile and saves one exact sentence for each", async ({
    page,
  }) => {
    const ownerToken = await mintBackendToken("admin");
    const ownerHeaders = { Authorization: `Bearer ${ownerToken}` };
    const vocabulary = [
      {
        module: "credit",
        label: "Credit",
        sensitivity: "aggregated",
        sentence:
          "E2E Grant Member is a Viewer in Credit for Sample Bank Ltd, covering Aggregated data.",
        reason: "Credit committee pack contributor reads the loan book",
      },
      {
        module: "institution",
        label: "Institution Profile",
        sensitivity: "confidential",
        sentence:
          "E2E Grant Member is a Viewer in Institution Profile for Sample Bank Ltd, covering Confidential data.",
        reason: "Company secretary maintains the institution register",
      },
    ] as const;

    await page.goto("/settings");
    const memberRow = page
      .locator("li")
      .filter({ hasText: "E2E Grant Member" })
      .first();
    const grantsBefore = await activeGrantCount(memberRow);

    for (const entry of vocabulary) {
      await memberRow.getByRole("button", { name: "Add grant" }).click();
      const composer = page.getByRole("dialog", {
        name: "Add grant for E2E Grant Member",
      });
      const moduleSelect = composer.getByLabel("Module");
      await expect(moduleSelect.locator("option")).toContainText([
        "Credit",
        "Institution Profile",
      ]);
      await composer.getByLabel("Role bundle").selectOption("viewer");
      await composer
        .getByLabel("Institution coverage")
        .selectOption("BK-SAMP0001");
      await moduleSelect.selectOption(entry.module);
      await expect(moduleSelect).toHaveValue(entry.module);
      await composer.getByLabel("Sensitivity").selectOption(entry.sensitivity);
      await composer.getByLabel("Reason").fill(entry.reason);
      await expect(
        composer.getByText(entry.sentence, { exact: true }),
      ).toBeVisible();
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, `grant-composer-${entry.module}.png`),
        });
      }
      await composer.getByRole("button", { name: "Review grant" }).click();
      await composer.getByRole("button", { name: "Grant access" }).click();
      await expect(
        composer.getByText("Grant created", { exact: true }),
      ).toBeVisible();
      await expect(
        composer.getByText(entry.sentence, { exact: true }),
      ).toBeVisible();
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, `grant-created-${entry.module}.png`),
        });
      }
      await composer.getByRole("button", { name: "Done" }).click();
    }

    await expect(memberRow).toContainText(`${grantsBefore + 2} grants`);
    await memberRow
      .getByRole("button", { name: "View E2E Grant Member" })
      .click();
    const detail = page.getByRole("dialog", { name: "E2E Grant Member" });
    for (const entry of vocabulary) {
      await expect(
        detail.getByText(entry.sentence, { exact: true }),
      ).toBeVisible();
    }
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "grant-member-credit-institution.png"),
      });
    }

    const listed = await page.request.get(
      `${API}/authorization/bindings?principal_user_id=${member.id}`,
      { headers: ownerHeaders },
    );
    expect(listed.ok()).toBeTruthy();
    const rows = (await listed.json()).bindings as Array<{
      module_scope: string;
      sensitivity_scope: string;
      status: string;
      authority_sentence: string;
    }>;
    expect(rows).toEqual(
      expect.arrayContaining(
        vocabulary.map((entry) =>
          expect.objectContaining({
            module_scope: entry.module,
            sensitivity_scope: entry.sensitivity,
            status: "active",
            authority_sentence: entry.sentence,
          }),
        ),
      ),
    );
  });
});
