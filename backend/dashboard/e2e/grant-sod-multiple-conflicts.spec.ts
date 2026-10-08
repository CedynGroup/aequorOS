import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_USERS, mintBackendToken } from "./support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.use({ storageState: path.join(E2E_TMP, "admin.json") });

test("all conflicting grants must be revoked even when their modules differ from the draft", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 1100 });
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("admin")}`,
  };
  const member = E2E_USERS.sod_member;
  const createdIds: string[] = [];
  const activeIds = new Set<string>();
  const decisions: unknown[] = [];
  const proposed = {
    principal_user_id: member.id,
    role_bundle: "validator",
    institution_scope: "institution",
    institution_id: "BK-SAMP0001",
    module_scope: "reg",
    sensitivity_scope: "restricted",
    reason_category: "other",
    reason_detail: "Independent filing duties after removing checker grants",
  };
  const checkPreview = async (outcome: string) => {
    const response = await page.request.post(
      `${API}/authorization/bindings/preview`,
      { headers, data: proposed },
    );
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.sod_decision.outcome).toBe(outcome);
    if (activeIds.size) {
      expect(body.sod_decision.findings).toHaveLength(1);
      expect(
        new Set(body.sod_decision.findings[0].conflicting_binding_ids),
      ).toEqual(activeIds);
      expect(body.sod_decision.findings[0].message).toContain(
        "Approver and Validator roles must stay with different people, whatever the scope.",
      );
    }
    decisions.push(body);
  };
  try {
    for (const moduleScope of ["liq", "cap"]) {
      const payload = {
        ...proposed,
        role_bundle: "approver",
        module_scope: moduleScope,
      };
      const preview = await page.request.post(
        `${API}/authorization/bindings/preview`,
        { headers, data: payload },
      );
      expect(preview.status()).toBe(200);
      const created = await page.request.post(`${API}/authorization/bindings`, {
        headers,
        data: {
          ...payload,
          expected_authority_sentence: (await preview.json())
            .authority_sentence,
        },
      });
      expect(created.status()).toBe(201);
      const id = (await created.json()).binding.id as string;
      createdIds.push(id);
      activeIds.add(id);
    }
    await checkPreview("block");
    const unauthorizedHeaders = {
      Authorization: `Bearer ${await mintBackendToken("analyst")}`,
    };
    const forbidden = [];
    for (const [endpoint, data] of [
      ["preview", proposed],
      ["", { ...proposed, expected_authority_sentence: "Unapproved grant" }],
      [`${createdIds[0]}/revoke`, { reason: "Unauthorized removal attempt" }],
    ] as const) {
      const response = await page.request.post(
        `${API}/authorization/bindings${endpoint ? `/${endpoint}` : ""}`,
        { headers: unauthorizedHeaders, data },
      );
      expect(response.status()).toBe(403);
      forbidden.push({
        endpoint,
        status: response.status(),
        body: await response.json(),
      });
    }
    decisions.push({ unauthorizedAttempts: forbidden });
    await checkPreview("block");
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
    await composer.getByLabel("Detail").fill(proposed.reason_detail);
    const notice = composer.getByRole("alert");
    await expect(notice).toHaveCount(1);
    await expect(notice).toContainText("Liquidity Monitoring, Sample Bank Ltd");
    await expect(notice).toContainText("Basel Capital, Sample Bank Ltd");
    await expect(notice).toContainText(
      "Remove all these Approver grants first, or choose someone else.",
    );
    const links = notice.getByRole("button", {
      name: "View E2E Sod Member's Approver grant",
    });
    await expect(links).toHaveCount(2);
    await expect(
      composer.getByRole("button", { name: "Cannot be granted" }),
    ).toBeDisabled();
    if (evidenceDir)
      await page.screenshot({
        path: path.join(evidenceDir, "grant-multiple-conflicts.png"),
      });

    for (let index = 0; index < createdIds.length; index += 1) {
      await links.first().click();
      const detail = page.getByRole("dialog", { name: "E2E Sod Member" });
      const focused = detail.getByTestId("focused-grant");
      await expect(focused).toContainText("Approver");
      await focused.getByRole("button", { name: "Revoke this access" }).click();
      const revoke = page.getByRole("dialog", { name: "Revoke access" });
      await expect(
        revoke.getByRole("button", { name: "Revoke access", exact: true }),
      ).toBeDisabled();
      await revoke
        .getByLabel("Reason")
        .fill("Move checker duties to another person");
      const revoked = page.waitForResponse(
        (response) =>
          response.url().startsWith(`${API}/authorization/bindings/`) &&
          response.url().endsWith("/revoke") &&
          response.request().method() === "POST",
      );
      await revoke
        .getByRole("button", { name: "Revoke access", exact: true })
        .click();
      const response = await revoked;
      expect(response.status()).toBe(200);
      await expect(composer).toBeVisible();
      const id = response.url().split("/").at(-2)!;
      expect(activeIds.has(id)).toBeTruthy();
      activeIds.delete(id);
      await expect(composer.getByLabel("Detail")).toHaveValue(
        proposed.reason_detail,
      );
      if (index === 0) {
        await expect(notice).toContainText(
          "Remove the Approver grant first, or choose someone else.",
        );
        await expect(links).toHaveCount(1);
        await expect(
          composer.getByRole("button", { name: "Cannot be granted" }),
        ).toBeDisabled();
        await checkPreview("block");
      } else {
        await expect(notice).toHaveCount(0);
        await expect(
          composer.getByRole("button", { name: "Review grant" }),
        ).toBeEnabled();
        await checkPreview("allow");
      }
      if (evidenceDir)
        await page.screenshot({
          path: path.join(
            evidenceDir,
            `grant-multiple-after-revoke-${index + 1}.png`,
          ),
        });
    }
    await composer.getByRole("button", { name: "Cancel", exact: true }).click();
    if (evidenceDir)
      await writeFile(
        path.join(evidenceDir, "grant-multiple-decisions.json"),
        JSON.stringify(decisions, null, 2),
      );
  } finally {
    for (const id of activeIds) {
      const cleanup = await page.request.post(
        `${API}/authorization/bindings/${id}/revoke`,
        { headers, data: { reason: "Clean up isolated multi-grant journey" } },
      );
      expect(cleanup.ok()).toBeTruthy();
    }
  }
});
