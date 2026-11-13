/**
 * An SDI's credit book is Credit's figure, not Capital's.
 *
 * The #182 remainder cut `GET /banks/{id}/loan-classification` over to
 * CREDIT / ``aggregated`` / ``view`` and `GET /banks/{id}/sdi/large-exposures`
 * to CREDIT / ``restricted`` / ``view``
 * (``backend/docs/credit_enforcement_rollout.md`` §Phase 5). Both surfaces also
 * sit inside Capital screens — the s.29 capital view and the Risk & Limits wall
 * — so the dashboard must not ask for either on Capital authority alone.
 *
 * These journeys provision a real savings-and-loans tenant with real bindings
 * (``support/story-identities``). The institution class comes from the backend's
 * own provisioning, so no profile-response mock carries the authority decision.
 */

import { expect } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { test as identityTest } from "./support/story-identities";
import { settleForCapture } from "./support/capture";

const CLASSIFICATION = /\/banks\/[^/]+\/loan-classification(?:\?|$)/;
const LARGE_EXPOSURES = /\/banks\/[^/]+\/sdi\/large-exposures(?:\?|$)/;

const DENIED_COPY = "A Capital grant does not include the credit book.";

function recordCreditRequests(page: import("@playwright/test").Page) {
  const requests = {
    classification: [] as string[],
    exposures: [] as string[],
  };
  page.on("request", (request) => {
    const url = request.url();
    if (CLASSIFICATION.test(url)) requests.classification.push(url);
    if (LARGE_EXPOSURES.test(url)) requests.exposures.push(url);
  });
  return requests;
}

async function screenshot(page: import("@playwright/test").Page, name: string) {
  if (!process.env.E2E_EVIDENCE_DIR) return;
  // Let the page's own reads settle first: a capture taken mid-load shows
  // shimmer skeletons and proves nothing about the panel's final state.
  await settleForCapture(page);
  await page.screenshot({
    path: path.join(process.env.E2E_EVIDENCE_DIR, name),
    fullPage: true,
  });
}

const CAPITAL_ONLY_BINDINGS = [
  {
    bundle: "viewer" as const,
    module: "institution",
    sensitivity: "confidential",
  },
  { bundle: "viewer" as const, module: "cap", sensitivity: "aggregated" },
  { bundle: "viewer" as const, module: "risk", sensitivity: "aggregated" },
];

identityTest.describe(
  "an SDI reader with Capital but no Credit authority",
  () => {
    identityTest.use({
      storageState: path.join(E2E_TMP, "viewer.json"),
      storyConfig: {
        ownedTenant: true,
        institutionType: "savings_and_loans",
        roles: ["viewer"],
        bindings: CAPITAL_ONLY_BINDINGS,
      },
    });

    identityTest(
      "sees the requirement instead of the book and asks for neither figure",
      async ({ page }) => {
        const requests = recordCreditRequests(page);

        await page.goto("/basel");
        await expect(
          page.getByRole("heading", {
            name: "Loan classification & provisioning",
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByText(DENIED_COPY, { exact: false }),
        ).toBeVisible();
        await screenshot(page, "sdi-credit-denied-capital-only.png");

        // The Risk & Limits wall carries the same named rows.
        await page.goto("/risk");
        await expect(
          page.getByRole("heading", { name: "Risk & Limit Monitor" }),
        ).toBeVisible();

        expect(requests.classification).toEqual([]);
        expect(requests.exposures).toEqual([]);
      },
    );
  },
);

identityTest.describe(
  "an SDI reader holding only the restricted Credit row",
  () => {
    identityTest.use({
      storageState: path.join(E2E_TMP, "viewer.json"),
      storyConfig: {
        ownedTenant: true,
        institutionType: "savings_and_loans",
        roles: ["viewer"],
        bindings: [
          ...CAPITAL_ONLY_BINDINGS,
          { bundle: "viewer", module: "credit", sensitivity: "restricted" },
        ],
      },
    });

    identityTest(
      "is admitted to named exposures but still not to the classification",
      async ({ page }) => {
        const requests = recordCreditRequests(page);

        // Restricted is exact: it is not a step on the way to aggregated.
        await page.goto("/basel");
        await expect(
          page.getByRole("heading", {
            name: "Loan classification & provisioning",
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByText(DENIED_COPY, { exact: false }),
        ).toBeVisible();

        const exposures = page.waitForResponse((response) =>
          LARGE_EXPOSURES.test(response.url()),
        );
        await page.goto("/risk");
        await expect(
          page.getByRole("heading", { name: "Risk & Limit Monitor" }),
        ).toBeVisible();
        expect((await exposures).status()).toBe(200);
        await screenshot(page, "sdi-credit-restricted-exposures.png");

        expect(requests.classification).toEqual([]);
      },
    );
  },
);

identityTest.describe("an SDI reader holding both Credit sentences", () => {
  identityTest.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      ownedTenant: true,
      institutionType: "savings_and_loans",
      roles: ["viewer"],
      bindings: [
        ...CAPITAL_ONLY_BINDINGS,
        { bundle: "viewer", module: "credit", sensitivity: "aggregated" },
        { bundle: "viewer", module: "credit", sensitivity: "restricted" },
      ],
    },
  });

  identityTest("reads both figures from the real API", async ({ page }) => {
    const classification = page.waitForResponse((response) =>
      CLASSIFICATION.test(response.url()),
    );

    await page.goto("/basel");
    await expect(
      page.getByRole("heading", {
        name: "Loan classification & provisioning",
        exact: true,
      }),
    ).toBeVisible();
    expect((await classification).status()).toBe(200);
    // The panel now renders Credit's table instead of the requirement.
    await expect(page.getByText(DENIED_COPY, { exact: false })).toHaveCount(0);
    await expect(
      page.getByRole("columnheader", { name: "Grade" }),
    ).toBeVisible();
    await screenshot(page, "sdi-credit-aggregated-classification.png");
  });
});
