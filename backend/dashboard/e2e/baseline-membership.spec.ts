import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("fresh active member baseline", () => {
  test.use({ storageState: path.join(E2E_TMP, "invite_fresh.json") });

  test("deep links land in the shell with the module catalogue disabled", async ({
    page,
  }) => {
    const productRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\//.test(request.url())) {
        productRequests.push(request.url());
      }
    });

    await page.goto("/liquidity/monitoring");
    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByText("No authorized institutions yet", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Ask your organization owner to grant access to an institution.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByText("No institution selected", { exact: true }),
    ).toBeVisible();

    const navigation = page.getByRole("navigation");
    for (const name of [
      "Command Center",
      "Risk & Limits",
      "Markets",
      "IRRBB",
      "Liquidity",
      "FX",
      "Basel Capital",
      "Data Engine",
      "Regulatory Reporting",
    ]) {
      await expect(
        navigation.getByRole("link", { name, exact: true }),
      ).toHaveAttribute("aria-disabled", "true");
    }

    const liquidity = navigation.getByRole("link", {
      name: "Liquidity",
      exact: true,
    });
    await liquidity.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires Liquidity Monitoring · Aggregated · View. Ask your organization owner or admin to grant it.",
      }),
    ).toBeVisible();

    await expect(
      navigation.getByRole("link", { name: "Settings", exact: true }),
    ).toHaveAttribute("href", "/settings");
    expect(productRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-membership-shell.png"),
        fullPage: true,
      });
    }

    await page.goto("/does-not-exist");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    await expect(
      page.getByText("No authorized institutions yet", { exact: true }),
    ).toHaveCount(0);
  });
});
