import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_PASSWORD } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("fresh active member baseline", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("deep links land in the shell with the module catalogue disabled", async ({
    page,
  }) => {
    const productRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\//.test(request.url())) {
        productRequests.push(request.url());
      }
    });

    await page.goto("/login");
    await page
      .getByLabel("Email", { exact: true })
      .fill("e2e.invite_fresh@aequoros.example");
    await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
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

    await navigation
      .getByRole("link", { name: "Settings", exact: true })
      .click();
    await expect(page).toHaveURL(/\/settings\/profile$/);
    await expect(
      page.getByRole("heading", { name: "Profile & preferences", exact: true }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-membership-settings.png"),
        fullPage: true,
      });
    }

    for (const route of [
      "/does-not-exist",
      "/liquidity/unknown",
      "/data-engine/batches/00000000-0000-0000-0000-000000000000",
      "/data-engine/batches/foreign-batch",
    ]) {
      await page.goto(route);
      await expect(page.getByText(/404|not found/i).first()).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`${route}$`));
      await expect(
        page.getByText("No authorized institutions yet", { exact: true }),
      ).toHaveCount(0);
    }
  });
});
