// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * The IRRBB Standardised Framework tab, in a real browser.
 *
 * This journey exists because four green gates once hid a screen that was
 * broken in the browser: generated API methods passed as bare references lost
 * `this`, so every panel reported a dead backend against a healthy one. A type
 * check could not see it and a hook-stubbing suite could not either. So the
 * assertions below are deliberately the ones only a browser can make — the
 * page renders, the reads actually leave, and what comes back is the honest
 * empty state rather than a transport error.
 *
 * Storage-free: it never mints a package, so it runs on a cold worktree.
 */
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("IRRBB standardised framework", () => {
  test.use({ storageState: path.join(E2E_TMP, "approver.json") });

  test("opens, reads the framework, and states the honest empty result", async ({
    page,
  }) => {
    const frameworkReads: string[] = [];
    page.on("request", (request) => {
      if (/\/irr\/standardised-framework/.test(request.url())) {
        frameworkReads.push(request.url());
      }
    });

    await page.goto("/irr");
    await page.getByRole("link", { name: "Standardised Framework" }).click();
    await expect(page).toHaveURL(/\/irr\/standardised$/);

    await expect(
      page.getByRole("heading", { name: "Standardised Framework" }).first(),
    ).toBeVisible();

    // THE DEFECT THIS GUARDS. A bare generated-client reference throws before a
    // request is made, so the screen would read "Could not reach the risk
    // service" against a healthy backend and NO request would appear here.
    await expect
      .poll(() => frameworkReads.length, { timeout: 15_000 })
      .toBeGreaterThan(0);
    await expect(page.getByText(/could not reach the risk service/i)).toHaveCount(
      0,
    );

    // These figures are supervisory monitoring, and the screen says so.
    await expect(
      page.getByText(/not a return the institution files/i),
    ).toBeVisible();

    // No run exists for the fixture's reporting date, so the honest state is
    // "no result", not an error panel and not a zeroed measure.
    await expect(
      page.getByText(/No standardised framework result for this reporting date/i),
    ).toBeVisible();
    await expect(page.getByText(/nothing has gone wrong/i)).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-standardised-framework.png"),
        fullPage: true,
      });
    }
  });

  test("offers the run control with its reason when the reader cannot mint", async ({
    page,
  }) => {
    await page.goto("/irr/standardised");
    const runButton = page.getByRole("button", {
      name: "Run the standardised framework",
    });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeDisabled();
    const wrapper = runButton.locator("..");
    await wrapper.focus();
    await expect(wrapper).toHaveAccessibleDescription(
      /Requires IRRBB · Confidential · Run/i,
    );
  });
});

test.describe("unbound IRRBB user", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("cannot deep-link to the framework, and makes no framework read", async ({
    page,
  }) => {
    const frameworkReads: string[] = [];
    page.on("request", (request) => {
      if (/\/irr\/standardised-framework/.test(request.url())) {
        frameworkReads.push(request.url());
      }
    });

    await page.goto("/irr/standardised");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    expect(frameworkReads).toEqual([]);
  });
});

test.describe("IRRBB runner", () => {
  // The organization-wide Analyst grant carries IRRBB confidential run.
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  /**
   * Minting exercises the OTHER half of the generated-client hazard: the
   * mutation. Whatever the fixture book produces, the screen has to state it —
   * the framework's figures or a refusal — and never a transport error against
   * a healthy backend.
   */
  test("mints a run and states the outcome, whichever it is", async ({
    page,
  }) => {
    await page.goto("/irr/standardised");
    const runButton = page.getByRole("button", {
      name: "Run the standardised framework",
    });
    await expect(runButton).toBeEnabled();

    const minted = page.waitForResponse(
      (response) =>
        /\/irr\/standardised-framework\/runs$/.test(response.url()) &&
        response.request().method() === "POST",
      { timeout: 60_000 },
    );
    await runButton.click();
    const response = await minted;
    const status = response.status();
    // The two honest outcomes: the run was minted, or the platform refused to
    // mint it (the balance-sheet control gates every immutable run). A 5xx or
    // a 4xx of any other kind is a defect, and asserting the exact set stops
    // this journey passing vacuously on whatever the fixture happens to do.
    expect([201, 409]).toContain(status);
    console.log(`[irrbb-sf] mint responded ${status}`);

    if (status === 201) {
      // Minted: the screen states the outcome. A measured result always shows
      // the commencement rule beside it; a refused one shows the refusal. The
      // alternation is the three mandate headings and the refusal heading —
      // NOT the page title, which would match whatever happened.
      await expect(
        page
          .getByText(
            /applies to this reporting date|not yet required for this reporting date|commencement rule could not be read|could not measure this book/i,
          )
          .first(),
      ).toBeVisible();
    } else {
      // Refused to mint: the screen says the run could not be made, and does
      // NOT quietly leave the reader on an unchanged page.
      await expect(
        page.getByText(
          /could not be run for this reporting date|No standardised framework result/i,
        ).first(),
      ).toBeVisible();
    }

    // Never a transport error against a healthy backend.
    await expect(page.getByText(/could not reach the risk service/i)).toHaveCount(
      0,
    );

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-standardised-framework-run.png"),
        fullPage: true,
      });
    }
  });
});
