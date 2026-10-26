import { expect } from "@playwright/test";
import path from "node:path";
import { test } from "../e2e/support/story-identities";
import { E2E_TMP } from "../playwright.config";
import { expectAlignedHeaderAndCards } from "./support/layout";

test.use({
  storageState: path.join(E2E_TMP, "analyst.json"),
  storyConfig: {
    roles: ["admin", "analyst"],
    ownedTenant: true,
    institutionType: "savings_and_loans",
  },
});

test("SDI liquidity cards align with the real response for an isolated institution", async ({
  page,
  storyIdentity,
}) => {
  const response = page.waitForResponse((read) =>
    read
      .url()
      .includes(`/banks/${storyIdentity.bankId}/sdi/liquidity-position`),
  );
  await page.goto("/liquidity");
  expect((await response).status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "LMTD Table 1 prudential ratios" }),
  ).toBeVisible();
  await expectAlignedHeaderAndCards(page);
});
