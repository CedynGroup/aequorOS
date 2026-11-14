/**
 * The credit Board registers need their OWN exact Credit sentence.
 *
 * The four credit registers became CREDIT / ``restricted`` / ``view`` in the
 * #182 remainder (``backend/docs/credit_enforcement_rollout.md`` §Phase 5). The
 * Board Registers page sits under Institution Profile, so a Capital reader or a
 * reader holding the retired CREDIT/``confidential`` row used to see those
 * cards; after the cutover they must not be mounted or fetched at all, and a
 * reader holding the restricted sentence must get them from the real API.
 *
 * These journeys use freshly provisioned principals with real bindings
 * (``support/story-identities``) — no profile-response mock carries the
 * authority decision.
 */

import { expect } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { test as identityTest } from "./support/story-identities";
import { settleForCapture } from "./support/capture";

const REGISTERS = "/institution/registers";

const CREDIT_REGISTER_TITLES = [
  "Credit early-warning thresholds",
  "CRM haircut register",
  "ECL assumptions register",
] as const;

const CREDIT_REGISTER_REQUESTS =
  /\/banks\/[^/]+\/(ecl-assumptions|crm-haircuts|concentration-limits|credit-thresholds)(?:\?|$)/;

function recordCreditRegisterRequests(page: import("@playwright/test").Page) {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (CREDIT_REGISTER_REQUESTS.test(request.url())) {
      requests.push(request.url());
    }
  });
  return requests;
}

/** Capture after the page's own reads settle, so no shimmer reaches the image. */
async function screenshot(page: import("@playwright/test").Page, name: string) {
  if (!process.env.E2E_EVIDENCE_DIR) return;
  await settleForCapture(page);
  await page.screenshot({
    path: path.join(process.env.E2E_EVIDENCE_DIR, name),
    fullPage: true,
  });
}

identityTest.describe("a reader without the restricted Credit sentence", () => {
  identityTest.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [
        // Opens Institution Profile, where the registers live.
        {
          bundle: "viewer",
          module: "institution",
          sensitivity: "confidential",
        },
        // Opens the page through the Liquidity register family.
        { bundle: "viewer", module: "liq", sensitivity: "confidential" },
        // Capital authority never implies Credit.
        { bundle: "viewer", module: "cap", sensitivity: "aggregated" },
        // The sensitivity the registers used before the cutover: exact, so it
        // must no longer admit them.
        { bundle: "viewer", module: "credit", sensitivity: "confidential" },
      ],
    },
  });

  identityTest(
    "sees the Liquidity registers but no credit register and fires no credit request",
    async ({ page }) => {
      const requests = recordCreditRegisterRequests(page);

      await page.goto(REGISTERS);
      await expect(
        page.getByRole("heading", { name: "Board Registers", exact: true }),
      ).toBeVisible();
      // The page itself is open: this is the card-level refusal.
      await expect(
        page.getByRole("heading", {
          name: "Board threshold register",
          exact: true,
        }),
      ).toBeVisible();

      for (const title of CREDIT_REGISTER_TITLES) {
        await expect(
          page.getByRole("heading", { name: title, exact: true }),
        ).toHaveCount(0);
      }
      // Not merely hidden by a CSS rule: the data was never requested.
      expect(requests).toEqual([]);

      await screenshot(page, "credit-registers-denied-confidential.png");
    },
  );
});

identityTest.describe("a reader holding neither register family", () => {
  identityTest.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [
        {
          bundle: "viewer",
          module: "institution",
          sensitivity: "confidential",
        },
        { bundle: "viewer", module: "credit", sensitivity: "confidential" },
      ],
    },
  });

  identityTest(
    "is told the exact sentence that opens them",
    async ({ page }) => {
      const requests = recordCreditRegisterRequests(page);

      await page.goto(REGISTERS);
      await expect(
        page.getByRole("heading", { name: "Access required" }),
      ).toBeVisible();
      // The retired sentence must not be the one the page names.
      await expect(
        page.getByText("Credit · Restricted · View", { exact: true }),
      ).toBeVisible();
      expect(requests).toEqual([]);
    },
  );
});

identityTest.describe("a reader holding the restricted Credit sentence", () => {
  identityTest.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [
        {
          bundle: "viewer",
          module: "institution",
          sensitivity: "confidential",
        },
        { bundle: "viewer", module: "credit", sensitivity: "restricted" },
      ],
    },
  });

  identityTest(
    "reads the credit registers from the real API",
    async ({ page }) => {
      const first = page.waitForResponse((response) =>
        /\/banks\/[^/]+\/(ecl-assumptions|crm-haircuts|credit-thresholds)(?:\?|$)/.test(
          response.url(),
        ),
      );

      await page.goto(REGISTERS);
      for (const title of CREDIT_REGISTER_TITLES) {
        await expect(
          page.getByRole("heading", { name: title, exact: true }),
        ).toBeVisible();
      }

      expect((await first).status()).toBe(200);
      // The register tables render the API's own empty-register answer rather than
      // an authorization error.
      await expect(page.getByText("No assumptions adopted")).toBeVisible();

      await screenshot(page, "credit-registers-allowed-restricted.png");
    },
  );
});
