// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("unbound IRRBB user", () => {
  test.use({ storageState: path.join(E2E_TMP, "viewer.json") });

  test("hides navigation and 404s the deep link without product queries", async ({
    page,
  }) => {
    const irrRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\/irr\//.test(request.url())) {
        irrRequests.push(request.url());
      }
    });

    await page.goto("/irr");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    expect(irrRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("IRRBB reader without run permission", () => {
  test.use({ storageState: path.join(E2E_TMP, "approver.json") });

  test("keeps run controls visible, disabled, and explained", async ({
    page,
  }) => {
    await page.goto("/irr");
    await expect(
      page.getByRole("heading", { name: "Interest Rate Risk" }).first(),
    ).toBeVisible();

    const runButton = page.getByRole("button", {
      name: "Run IRRBB scenarios",
    });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeDisabled();
    const wrapper = runButton.locator("..");
    await wrapper.focus();
    await expect(wrapper).toBeFocused();
    await expect(wrapper).toHaveAccessibleDescription(
      /Requires IRRBB · Confidential · Run/i,
    );
    await expect(
      page.getByRole("tooltip", {
        name: /Requires IRRBB · Confidential · Run/i,
      }),
    ).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-reader-disabled-run.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("IRRBB analyst", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("shows an enabled run action derived from effective authority", async ({
    page,
  }) => {
    await page.goto("/irr");
    const runButton = page.getByRole("button", {
      name: "Run IRRBB scenarios",
    });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeEnabled();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-analyst-enabled-run.png"),
        fullPage: true,
      });
    }
  });
});
