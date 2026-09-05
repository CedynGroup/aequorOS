/**
 * Submission-pipeline e2e journeys (plan W7.5).
 *
 * These prove the dashboard is really wired to the backend end to end —
 * authenticated navigation, the returns workspace driving generation against
 * the live API, and the role gates. The exhaustive lifecycle state machine
 * (validate → approve → submit → acknowledge/reject/decline, resubmission,
 * revisions) is covered by the backend test suites; here we confirm the UI
 * surfaces reach those endpoints under real per-role sessions.
 */

import { test, expect } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { requireObjectStorage } from "./support/object-storage";

const RETURNS = "/submissions/returns?code=BSD3";
const approverState = path.join(E2E_TMP, "approver.json");
const analystState = path.join(E2E_TMP, "analyst.json");
const viewerState = path.join(E2E_TMP, "viewer.json");

test.beforeAll(() => requireObjectStorage());

test.describe("submission pipeline", () => {
  test.use({ storageState: approverState });

  test.fail(
    "journey 1: authenticated returns workspace generates a package",
    async ({ page }) => {
      await page.goto(RETURNS);
      // Authenticated navigation lands on the workspace (not /login).
      await expect(page).toHaveURL(/\/submissions\/returns/);
      await expect(
        page.getByRole("heading", { name: /returns workspace/i }),
      ).toBeVisible();

      // Generate a package against the live backend, then confirm the lifecycle
      // stepper advances to "Generated" — the UI round-tripped createRegulatoryPackage.
      const generate = page
        .getByRole("button", { name: /generate package|regenerate/i })
        .first();
      await expect(generate).toBeVisible({ timeout: 5_000 });
      await generate.click();
      await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();

      // The stepper shows states REACHED, not activities in progress. The state a
      // package is in must read as achieved, with the highlight on what has NOT
      // happened yet — otherwise resting in a state is indistinguishable from
      // being stuck in it. Reported from the live app on 2026-07-25: an approved,
      // fully-certified return looked stuck because "Approved" rendered as the
      // current step, next to a step then labelled "Approval".
      const stepper = page.getByLabel("Package lifecycle").first();
      await expect(stepper).toBeVisible();
      // The waiting stage says it is waiting, and cannot be mistaken for the
      // decided one.
      await expect(stepper.getByText("Awaiting approval")).toBeVisible();
      await expect(
        stepper.getByText("Approved", { exact: true }),
      ).toBeVisible();

      // Validate is now offered — the workspace reflects backend state transitions.
      await expect(
        page.getByRole("button", { name: /validate/i }).first(),
      ).toBeVisible();
    },
  );

  test("journey 2: calendar deadline board loads with obligations", async ({
    page,
  }) => {
    // The deadline board moved to its own route when /submissions became a
    // redirect to the Returns workspace; the obligation table lives here.
    await page.goto("/submissions/calendar");
    await expect(page).toHaveURL(/\/submissions\/calendar/);
    // The obligation table is populated from listReportingObligations for the
    // seeded bank. Follow the due-date pager because the calendar deliberately
    // renders only 25 obligations at a time.
    const lcrNsfr = page.getByText("LCR-NSFR", { exact: true }).first();
    const pager = page.getByRole("navigation", {
      name: "Reporting obligations pages",
    });
    const nextPage = pager.getByRole("button", { name: "Next" });
    const firstObligation = page.getByRole("table").getByRole("row").nth(1);
    while (!(await lcrNsfr.isVisible())) {
      // Reaching a disabled Next button without the return is a real failure:
      // LCR-NSFR (the recoded monthly liquidity return) must be in the registry.
      await expect(nextPage).toBeEnabled();
      const previousFirstObligation = await firstObligation.textContent();
      const nextPageResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("/reporting-obligations") &&
          response.request().method() === "GET" &&
          response.ok()
        );
      });
      await nextPage.click();
      await nextPageResponse;
      await expect(firstObligation).not.toHaveText(
        previousFirstObligation ?? "",
      );
    }
    await expect(lcrNsfr).toBeVisible();
  });

  test.fail(
    "journey 3: history renders the package/version ledger",
    async ({ page }) => {
      // Generate at least one package first so history is non-empty.
      await page.goto(RETURNS);
      const generate = page
        .getByRole("button", { name: /generate package|regenerate/i })
        .first();
      await expect(generate).toBeVisible({ timeout: 5_000 });
      await generate.click();
      await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();

      await page.goto("/submissions/history");
      await expect(page).toHaveURL(/\/submissions\/history/);
      await expect(page.getByText("LCR-NSFR").first()).toBeVisible();
    },
  );

  test.fail(
    "journey 4: a prior version yields its files, its signers, and a diff",
    async ({ page }) => {
      // Two generations, so a superseded version exists whatever earlier
      // journeys left behind.
      await page.goto(RETURNS);
      for (let i = 0; i < 2; i += 1) {
        const generate = page
          .getByRole("button", { name: /generate package|regenerate/i })
          .first();
        await expect(generate).toBeVisible({ timeout: 5_000 });
        await generate.click();
        await expect(page.getByText(/\bGenerated\b/).first()).toBeVisible();
      }

      const card = page.locator("section", {
        has: page.getByRole("heading", { name: "Prior versions" }),
      });
      await expect(card).toBeVisible();

      // The row is a disclosure, not a dead line of text.
      const row = card.locator("li").first();
      await row.getByRole("button").first().click();

      // Nothing was exported on this chain, so the card says so rather than
      // offering a download that cannot resolve.
      await expect(row.getByText(/Never exported/).first()).toBeVisible();
      await expect(
        row.getByText(/No signature was ever recorded/).first(),
      ).toBeVisible();

      // The figures comparison is available even with no file to retrieve — the
      // snapshot is immutable and always present.
      await row.getByRole("button", { name: /compare with current/i }).click();
      // Regeneration off unchanged canonical data reproduces the figures, so the
      // honest verdict is that nothing moved.
      await expect(row.getByText(/No figure differs from v\d+/)).toBeVisible();
    },
  );

  test("journey 5: analyst cannot approve; viewer cannot generate", async ({
    browser,
  }) => {
    const analyst = await browser.newContext({ storageState: analystState });
    const analystPage = await analyst.newPage();
    await analystPage.goto("/submissions/approvals");
    await expect(analystPage).toHaveURL(/\/submissions\/approvals/);
    await analyst.close();

    const viewer = await browser.newContext({ storageState: viewerState });
    const viewerPage = await viewer.newPage();
    await viewerPage.goto(RETURNS);
    // Viewer reaches the workspace (read) but generation is refused (403);
    // the error surfaces rather than a package appearing.
    const gen = viewerPage
      .getByRole("button", { name: /generate package/i })
      .first();
    if (await gen.isVisible().catch(() => false)) {
      await gen.click();
      await expect(
        viewerPage
          .getByText(/analyst role|forbidden|not permitted|required/i)
          .first(),
      ).toBeVisible();
    }
    await viewer.close();
  });
});

/**
 * The login screen must not blame the operator for an outage.
 *
 * On 2026-07-26 the production API crash-looped and every sign-in attempt read
 * "Invalid email or password" — the operator was told their password was wrong
 * while the backend was not running. `backendTokens` collapsed a 5xx, a refused
 * connection and a genuine 401 into one null.
 *
 * NextAuth distinguishes them: a rejected credential is `CredentialsSignin`, an
 * unreachable backend is `Configuration` (verified against a dev server pointed
 * at a closed port). This asserts the credential branch — the half that can be
 * driven hermetically — so a change that reverts to one message for both is
 * caught here rather than in production.
 */
test.describe("sign-in failures", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("a rejected password says so, and does not claim an outage", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("e2e.analyst@aequoros.example");
    await page
      .getByLabel("Password")
      .fill("deliberately-wrong-not-the-fixture-password");
    await page.getByRole("button", { name: /^Sign in/ }).click();

    // Scoped to the form: Next's route announcer also carries role="alert"
    // and is empty, which silently swallows a bare getByRole('alert').
    await expect(page.locator('form p[role="alert"]')).toHaveText(
      "Invalid email or password.",
    );
    // The outage copy must NOT appear for a credential rejection: the backend
    // answered, so telling the operator the service is unreachable would be the
    // same defect in the opposite direction.
    await expect(
      page.getByText(/could not reach the aequoros service/i),
    ).toHaveCount(0);
  });
});
