// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("unbound Capital user", () => {
  test.use({ storageState: path.join(E2E_TMP, "viewer.json") });

  test("hides navigation, 404s deep links, and sends no Capital requests", async ({
    page,
  }) => {
    const capitalRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/banks\/[^/]+\/(capital(?:\/|$)|capital-plan|sdi\/capital|submissions\/bsd2)/.test(
          request.url(),
        )
      ) {
        capitalRequests.push(request.url());
      }
    });

    await page.goto("/");
    await expect(
      page.getByText("No authorized institutions yet", { exact: true }),
    ).toBeVisible();
    // The shell renders its navigation landmark, and since #201 it also lists
    // the modules a baseline member cannot reach — as `role="link"` spans with
    // `aria-disabled="true"` and no href, each explaining why it is blocked.
    // Counting landmarks or roles therefore says nothing. The invariant that
    // matters is that NOTHING here is followable: every entry is disabled.
    // Since #201 the shell LISTS the modules a baseline member cannot reach,
    // as `role="link"` spans carrying `aria-disabled="true"` and no href, each
    // explaining why it is blocked. Counting landmarks or roles therefore says
    // nothing about authority. What must hold is that no MODULE is followable:
    // the only enabled entry is the member's own settings, which
    // `lib/modules.test.ts` pins as deliberately reachable.
    const followable = page.locator(
      'nav [role="link"]:not([aria-disabled="true"]), nav a[href]',
    );
    await expect(followable).toHaveCount(1);
    await expect(followable.first()).toHaveAttribute("href", "/settings");

    await page.goto("/basel");
    // A baseline member deep-linking to a module they cannot reach is shown
    // the empty-state panel explaining why, not a 404 — deliberate since
    // #198/#201 and pinned by `hubRedirectFor` in lib/modules.test.ts. The
    // security property is unchanged and still asserted below: the module is
    // unreachable and no request for its data is made.
    await expect(
      page.getByText(/404|not found|No authorized institutions yet/i).first(),
    ).toBeVisible();

    await page.goto("/basel/planning");
    // A baseline member deep-linking to a module they cannot reach is shown
    // the empty-state panel explaining why, not a 404 — deliberate since
    // #198/#201 and pinned by `hubRedirectFor` in lib/modules.test.ts. The
    // security property is unchanged and still asserted below: the module is
    // unreachable and no request for its data is made.
    await expect(
      page.getByText(/404|not found|No authorized institutions yet/i).first(),
    ).toBeVisible();
    expect(capitalRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "capital-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("bound Capital user", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("confidential-only planning recovers on Retry without querying denied surfaces", async ({
    page,
  }) => {
    // Narrow the browser's authority projection to exercise independently gated
    // queries. The API authorization matrix tests the actual binding decisions.
    await page.route("**/auth/me", async (route) => {
      const response = await route.fetch();
      const profile = await response.json();
      profile.effective_authority.organization_capabilities = [];
      for (const institution of profile.effective_authority
        .institution_capabilities) {
        institution.capabilities = institution.capabilities.filter(
          (capability: {
            module: string;
            sensitivity: string;
            permission: string;
          }) =>
            capability.module === "cap" &&
            capability.sensitivity === "confidential" &&
            capability.permission === "view",
        );
      }
      await route.fulfill({ response, json: profile });
    });
    let failPlan = true;
    let recoveredRequests = 0;
    await page.route("**/banks/*/capital-plan", async (route) => {
      if (failPlan) {
        await route.fulfill({
          status: 503,
          json: {
            error: { code: "unavailable", message: "Temporary plan outage" },
          },
        });
      } else {
        recoveredRequests += 1;
        await route.continue();
      }
    });
    const deniedRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/banks\/[^/]+\/(capital\/dashboard|forecast\/runs)/.test(
          request.url(),
        )
      ) {
        deniedRequests.push(request.url());
      }
    });
    await page.goto("/basel/planning");
    const retry = page.getByRole("button", { name: "Retry", exact: true });
    await expect(retry).toBeVisible();
    failPlan = false;
    await retry.click();
    await expect(
      page.getByRole("heading", { name: "ICAAP and ILAAP governance" }),
    ).toBeVisible();
    await expect(retry).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Refresh ILAAP evidence" }),
    ).toHaveCount(0);
    expect(recoveredRequests).toBeGreaterThan(0);
    expect(deniedRequests).toEqual([]);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "capital-confidential-retry.png"),
        fullPage: true,
      });
    }
  });

  test("shows Basel navigation and opens the governed planning surface", async ({
    page,
  }) => {
    await page.goto("/basel");
    await expect(
      page.getByRole("heading", { name: "Basel Capital" }).first(),
    ).toBeVisible();
    const planning = page.getByRole("link", { name: "Planning" });
    await expect(planning).toBeVisible();
    await planning.click();
    await expect(page).toHaveURL(/\/basel\/planning$/);
    await expect(
      page.getByRole("heading", { name: "ICAAP and ILAAP governance" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Refresh ILAAP evidence" }),
    ).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "capital-bound.png"),
        fullPage: true,
      });
    }
  });
});
