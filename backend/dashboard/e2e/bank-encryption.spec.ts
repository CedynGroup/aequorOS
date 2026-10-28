import { SignJWT } from "jose";
import type { Page } from "@playwright/test";
import { E2E_API_ORIGIN, E2E_BASE_URL } from "../playwright.config";
import {
  encryptionEvidence,
  expect,
  keyControl,
  keyService,
  storyIdentity,
  test,
  type EncryptionStory,
} from "./support/encryption";
import { mintBackendToken } from "./support/mint";
import { kpi } from "./support/figures";

const NAME = "Synthetic Encryption Director Ñ";
const DRAFT = "Synthetic Unsaved Director Ñ";
const LOCKED = "🔒 Protected data";
const REVOKED =
  "Your bank has turned off aequorOS access to protected data. Contact your bank administrator.";
const OUTAGE = "Can't reach your bank's key service. Retrying.";

async function addParty(page: Page, name = NAME) {
  await page.goto("/institution/parties");
  const add = page.getByRole("button", {
    name: "Add related party",
    exact: true,
  });
  await expect(add).toBeEnabled();
  await add.click();
  await page.getByLabel("Full name").fill(name);
  await page
    .getByLabel("Reason for change")
    .fill("Synthetic encryption journey");
  await page.getByRole("button", { name: "Add party", exact: true }).click();
  await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();
}

async function identityAndNumericDashboard(page: Page, story: EncryptionStory) {
  const { role, org, bank } = storyIdentity(story);
  const token = await mintBackendToken(role);
  const response = await page.request.get(`${E2E_API_ORIGIN}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok()).toBe(true);
  expect(await response.json()).toMatchObject({ organization_id: org });
  const numeric = await page.context().newPage();
  // LCR is unpublished reference analysis, so exercise its numeric workspace
  // through the same deliberate preparation entry as the liquidity journeys.
  await numeric.goto("/liquidity?preparing=1");
  const tile = kpi(numeric, "Liquidity Coverage Ratio");
  await expect(tile.locator("span[title]")).toHaveText(/\d[\d,.]*%/);
  const before = await tile.locator("span[title]").innerText();
  const summary = await page.request.get(
    `${E2E_API_ORIGIN}/api/v1/banks/${bank}/live-summary`,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  expect(summary.ok()).toBe(true);
  return { numeric, before };
}

async function numericStillWorks(page: Page, before: string) {
  await page.reload();
  await expect(
    kpi(page, "Liquidity Coverage Ratio").locator("span[title]"),
  ).toHaveText(before);
}

test.describe("Bank-held encryption: revocation", () => {
  test.use({ encryptionStory: "revocation" });
  test("protects saved fields, preserves drafts on refusal, locks inputs and keeps numeric views", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    const { numeric, before } = await identityAndNumericDashboard(
      page,
      "revocation",
    );
    try {
      await addParty(page);
      await page.reload();
      await expect(
        page.getByRole("cell", { name: NAME, exact: true }),
      ).toBeVisible();
      expect(keyControl("revocation", "assert-storage")).toMatchObject({
        encrypted_parties: 1,
        decrypted_counterparties: 1,
      });
      await encryptionEvidence(page, "readable");
      // Keep an unsaved create form mounted while the first rejected POST
      // reports revocation. No intercepted response or local provider is used.
      await page
        .getByRole("button", { name: "Add related party", exact: true })
        .click();
      await page.getByLabel("Full name").fill(DRAFT);
      await page
        .getByLabel("Reason for change")
        .fill("Synthetic draft must survive key refusal");
      keyControl("revocation", "disable");
      const refused = page.waitForResponse(
        (response) =>
          response.url().endsWith("/related-parties") &&
          response.request().method() === "POST",
      );
      await page
        .getByRole("button", { name: "Add party", exact: true })
        .click();
      expect((await refused).status()).toBe(503);
      await expect(
        page.getByRole("status").filter({ hasText: REVOKED }).first(),
      ).toBeVisible();
      await expect(
        page.getByRole("cell", { name: LOCKED, exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("cell", { name: NAME, exact: true }),
      ).toHaveCount(0);
      await expect(page.getByLabel("Full name")).toHaveValue(DRAFT);
      await expect(page.getByLabel("Full name")).toBeDisabled();
      const protectedField = page
        .locator("fieldset")
        .filter({ has: page.getByLabel("Full name") });
      await expect(protectedField).toHaveAttribute("title", REVOKED);
      await expect(
        protectedField.getByRole("img", { name: "Protected data locked" }),
      ).toBeVisible();
      await expect(page.getByLabel("Status", { exact: true })).toBeEnabled();
      await expect(page.getByLabel("Reason for change")).toBeEnabled();
      await expect(
        page.getByRole("button", { name: "Add party", exact: true }),
      ).toBeDisabled();
      await encryptionEvidence(page, "revoked-draft");
      await numericStillWorks(numeric, before);
      keyControl("revocation", "enable");
      // Recovery is the product's five-second poll; no manual refetch here.
      await expect(page.getByLabel("Full name")).toBeEnabled({
        timeout: 20_000,
      });
      await expect(page.getByLabel("Full name")).toHaveValue(DRAFT);
      await page
        .getByRole("button", { name: "Add party", exact: true })
        .click();
      await expect(
        page.getByRole("cell", { name: DRAFT, exact: true }),
      ).toBeVisible();
      expect(keyControl("revocation", "assert-storage")).toMatchObject({
        encrypted_parties: 2,
      });
      await encryptionEvidence(page, "restored-draft-saved");
    } finally {
      await numeric.close();
    }
  });
});

test.describe("Bank-held encryption: service outage", () => {
  test.use({ encryptionStory: "outage" });
  test("distinguishes a service outage, retries over HTTP and automatically recovers", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await addParty(page);
    keyService(false);
    try {
      await page.reload();
      await expect(
        page.getByRole("status").filter({ hasText: OUTAGE }),
      ).toBeVisible();
      await expect(page.getByText(REVOKED, { exact: true })).toHaveCount(0);
      await expect(
        page.getByRole("cell", { name: LOCKED, exact: true }),
      ).toBeVisible();
      await page.getByRole("cell", { name: LOCKED, exact: true }).click();
      await expect(
        page.getByRole("button", { name: "Edit", exact: true }),
      ).toBeEnabled();
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await expect(page.getByLabel("Full name")).toBeDisabled();
      await expect(page.getByLabel("Full name")).toHaveAttribute(
        "placeholder",
        LOCKED,
      );
      const protectedField = page
        .locator("fieldset")
        .filter({ has: page.getByLabel("Full name") });
      await expect(protectedField).toHaveAttribute("title", OUTAGE);
      await expect(
        protectedField.getByRole("img", { name: "Protected data locked" }),
      ).toBeVisible();
      await expect(page.getByLabel("Status", { exact: true })).toBeEnabled();
      await expect(page.getByLabel("Reason for change")).toBeEnabled();
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(page.getByLabel("Full name")).toHaveCount(0);
      const retry = page.waitForResponse((response) =>
        response.url().endsWith("/institution-profile"),
      );
      await page.getByRole("button", { name: "Retry", exact: true }).click();
      expect((await retry).ok()).toBe(true); // presentation reads keep metadata available
      await expect(
        page.getByRole("cell", { name: NAME, exact: true }),
      ).toHaveCount(0);
      await encryptionEvidence(page, "outage-retry");
      keyService(true);
      await expect(
        page.getByRole("cell", { name: NAME, exact: true }),
      ).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText(OUTAGE, { exact: true })).toHaveCount(0);
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await expect(page.getByLabel("Full name")).toHaveValue(NAME);
      const edited = "Synthetic Recovered Director Ñ";
      await page.getByLabel("Full name").fill(edited);
      await page
        .getByLabel("Reason for change")
        .fill("Synthetic recovered edit");
      const saved = page.waitForResponse(
        (response) =>
          response.url().includes("/related-parties/") &&
          response.request().method() === "PUT",
      );
      await page
        .getByRole("button", { name: "Save party", exact: true })
        .click();
      expect((await saved).ok()).toBe(true);
      await expect(
        page.getByRole("cell", { name: edited, exact: true }),
      ).toBeVisible();
      await page.reload();
      await expect(
        page.getByRole("cell", { name: edited, exact: true }),
      ).toBeVisible();
      await encryptionEvidence(page, "outage-recovered");
    } finally {
      keyService(true);
    }
  });
});

test.describe("Bank-held encryption: rotation and support", () => {
  test.use({ encryptionStory: "rotation" });
  test("keeps old ciphertext readable after rotation and hides protected values from staff", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await addParty(page);
    const before = keyControl("rotation", "assert-storage");
    expect(keyControl("rotation", "rotate")).toMatchObject({
      status: "active",
    });
    await page.reload();
    await expect(
      page.getByRole("cell", { name: NAME, exact: true }),
    ).toBeVisible();
    expect(keyControl("rotation", "assert-storage")).toEqual(before);
    await addParty(page, "Synthetic New Key Director");
    await page.reload();
    await expect(
      page.getByRole("cell", { name: NAME, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("cell", {
        name: "Synthetic New Key Director",
        exact: true,
      }),
    ).toBeVisible();
    expect(keyControl("rotation", "assert-storage")).toMatchObject({
      encrypted_parties: 2,
    });
    await encryptionEvidence(page, "rotation-old-and-new-data");

    const token = await new SignJWT({
      typ: "impersonation",
      org: storyIdentity("rotation").org,
      act_operator: "synthetic.examiner@aequoros.example",
      session_id: "eeeeeeee-3999-4eee-8eee-eeeeeeee3999",
      roles: ["examiner"],
    })
      .setProtectedHeader({ alg: "HS256" })
      .setIssuedAt()
      .setExpirationTime("15m")
      .sign(
        new TextEncoder().encode("e2e-impersonation-secret-not-production-000"),
      );
    const support = await page
      .context()
      .browser()!
      .newContext({ baseURL: E2E_BASE_URL });
    try {
      const staff = await support.newPage();
      await staff.goto(`/inspect#${encodeURIComponent(token)}`);
      await expect(
        staff.getByText(
          "AequorOS staff is inspecting this account — read-only.",
        ),
      ).toBeVisible();
      // Institution authority is absent during staff inspection. The profile
      // API refuses access, while the allowed Data Engine batch view keeps
      // proving field-level protection.
      const profile = await support.request.get(
        `${E2E_API_ORIGIN}/api/v1/banks/${storyIdentity("rotation").bank}/institution-profile`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      expect(profile.status()).toBe(403);
      const profileBody = await profile.json();
      expect(JSON.stringify(profileBody)).not.toContain(NAME);
      expect(JSON.stringify(profileBody)).not.toContain(
        "Synthetic New Key Director",
      );
      expect(before.batch_id).toBeTruthy();
      await staff.goto(`/data-engine/batches/${before.batch_id}`);
      await expect(
        staff
          .getByRole("status")
          .filter({
            hasText: "Protected data is hidden during staff support access.",
          })
          .first(),
      ).toBeVisible();
      await expect(
        staff.getByText("Protected data locked", { exact: true }),
      ).toBeVisible();
      await expect(staff.getByText(NAME, { exact: true })).toHaveCount(0);
      await encryptionEvidence(staff, "staff-placeholders");
    } finally {
      await support.close();
    }
  });
});
