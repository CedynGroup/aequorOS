import { expect, test } from "playwright/test";

const evidenceDir = process.env.NO_MISTAKES_EVIDENCE_DIR;

test("shows and navigates the adverse case-health summary in frontend demo mode", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/cases?tab=overview&archived=false");
  await page.getByRole("button", { name: "Demo seed data" }).click();
  await page.getByRole("combobox", { name: "Current case" }).click();
  await page
    .getByRole("option", {
      name: "Covenant exception — Adom Textiles & Garments Ltd",
    })
    .click();

  const health = page.getByTestId("case-health-header");
  await expect(health).toBeVisible();
  await expect(health.getByText("Validated")).toBeVisible();
  await expect(health.getByText("Ready", { exact: true })).toBeVisible();
  await expect(health.getByText("Forecast #1 · Succeeded")).toBeVisible();
  await expect(health.getByText("Non-compliant")).toBeVisible();
  await expect(health.getByText("Needs More Info")).toBeVisible();
  expect(
    await health.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true);

  if (evidenceDir) {
    await page.screenshot({
      path: `${evidenceDir}/frontend-demo-adverse-case-health-1280.png`,
      fullPage: true,
    });
  }

  await health.getByRole("button", { name: /Covenants/ }).click();
  await expect(page).toHaveURL(/tab=financial/);
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.id))
    .toBe("case-health-target-financial");
  await expect(page.getByText("Non-compliant", { exact: true })).toBeVisible();
});
