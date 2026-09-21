/**
 * An SDI tenant has no ICAAP.
 *
 * There is no Pillar 2 regime for a savings-and-loans institution under the
 * BoG framework (DV-006), and the backend 404s every `/banks/{id}/icaap/*`
 * route for one before it looks at a binding. The nav must agree — an entry
 * that leads to a 404 is worse than no entry.
 *
 * The hermetic stack has no real SDI bank (C-20), so the institution class is
 * mocked on the `/banks` response, which is exactly where the dashboard reads
 * it (the `page-header-navigation.spec.ts` pattern). The REAL server-side 404
 * is proven in pytest, not here.
 */

import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

test("an SDI tenant sees no ICAAP entry and 404s the deep link", async ({
  page,
}) => {
  await page.route("**/api/v1/banks", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    for (const bank of payload.banks) {
      bank.institution_type_detail.institution_class = "sdi";
    }
    await route.fulfill({ response, json: payload });
  });

  const icaapApiRequests: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/banks\/[^/]+\/icaap/.test(request.url())) {
      icaapApiRequests.push(request.url());
    }
  });

  await page.goto("/");
  await expect(page.getByRole("link", { name: "ICAAP" })).toHaveCount(0);

  await page.goto("/icaap");
  await expect(page.getByText(/404|not found/i).first()).toBeVisible();

  const cycleId = "00000000-0000-4000-8000-000000000000";
  await page.goto(`/icaap/${cycleId}/overview`);
  await expect(page.getByText(/404|not found/i).first()).toBeVisible();

  expect(icaapApiRequests).toEqual([]);
});
