import { expect } from "@playwright/test";
import path from "node:path";
import { test } from "../e2e/support/story-identities";
import { createFailedIngestion } from "../e2e/support/ingestion-attempt";
import { E2E_TMP } from "../playwright.config";
import { expectAlignedHeaderAndCards } from "./support/layout";

test.use({
  storageState: path.join(E2E_TMP, "analyst.json"),
  storyConfig: { ownedTenant: true, roles: ["admin", "analyst"] },
});
test("a persisted failed ingestion keeps its detail header aligned", async ({
  page,
  request,
  storyIdentity,
}) => {
  const batchId = await createFailedIngestion(request, storyIdentity.bankId);
  await page.goto(`/data-engine/batches/${batchId}`);
  await expect(
    page.getByRole("heading", { name: /Ingestion batch/ }),
  ).toBeVisible();
  await expectAlignedHeaderAndCards(page);
});
