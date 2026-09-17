// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test } from "@playwright/test";
import path from "path";
import { writeFile } from "node:fs/promises";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("unbound IRRBB user", () => {
  // A baseline-only member redirects to the hub. Use a user with institution
  // access in another module to exercise the IRRBB-specific denial boundary.
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

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

  test("executes scenarios with effective authority and reloads the persisted run", async ({
    page,
  }) => {
    await page.goto("/irr");
    const runButton = page.getByRole("button", {
      name: "Run IRRBB scenarios",
    });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeEnabled();

    const batchResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/irr/run-all-scenarios") &&
        response.request().method() === "POST",
    );
    await runButton.click();
    const response = await batchResponse;
    expect(response.status()).toBe(201);
    const batch = await response.json();
    expect(
      batch.runs.map((run: { scenario_code: string }) => run.scenario_code),
    ).toEqual([
      "baseline",
      "parallel_up_200",
      "parallel_down_200",
      "short_up_250",
      "short_down_250",
      "steepener",
      "flattener",
    ]);
    for (const run of batch.runs) {
      expect(run.module).toBe("irr");
      expect(run.status).toBe("succeeded");
    }
    await expect(runButton).toBeEnabled();
    const persisted = await page.request.get(
      response
        .url()
        .replace(
          "/irr/run-all-scenarios",
          `/regulatory-runs/${batch.runs[0].id}`,
        ),
      {
        headers: {
          Authorization:
            (await response.request().headerValue("authorization")) ?? "",
        },
      },
    );
    expect(persisted.status()).toBe(200);
    expect((await persisted.json()).input_hash).toBe(batch.runs[0].input_hash);
    await page.reload();
    await expect(runButton).toBeEnabled();

    if (evidenceDir) {
      await writeFile(
        path.join(evidenceDir, "irrbb-executed-runs.json"),
        JSON.stringify(batch, null, 2),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-analyst-enabled-run.png"),
        fullPage: true,
      });
    }
  });
});
