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
      page.getByText("No authorized institutions", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("navigation")).toHaveCount(0);

    await page.goto("/basel");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();

    await page.goto("/basel/planning");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
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
