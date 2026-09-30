/**
 * Insights — the catalogue a reader may analyse, and the statements the
 * platform is prepared to make about a reporting date.
 *
 * Every assertion here names a figure, a production label or a refusal that
 * could only appear if the whole chain worked: the Data Engine fixture book →
 * the live plane → the `bi_*` marts → the statements. A navigate-and-see-a-page
 * test would prove none of it, which is why the catalogue counts and the
 * statement sentences are pinned individually rather than through "the section
 * rendered".
 *
 * THE GENERATED STATEMENTS ARE COVERED FROM BOTH SIDES. `GET …/bi/insights`
 * composes them server-side and `InsightStrip` renders them under the page
 * header, so the third journey drives the route — where the per-reader filtering
 * that is the strip's disclosure property can be measured against a narrower
 * identity — and the fourth reads the same sentences off the screen. Neither is
 * redundant: the API half proves the withholding, the browser half proves the
 * reader is actually told.
 */

import { expect, test } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "./support/bi";

test.describe("Insights", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("offers every Intelligence door and opens on the institution's latest computed snapshot", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);

    // The deployment flag is on, so the shell offers the door. With BI_ENABLED
    // off every one of these is hidden rather than disabled — a flag is not a
    // grant — which `bi-authorization.spec.ts` asserts from the other side.
    await page.goto("/insights");
    const nav = page.getByRole("navigation");
    for (const [label, href] of [
      ["Insights", "/insights"],
      ["Dashboards", "/dashboards"],
      ["Explore", "/explore"],
    ] as const) {
      const link = nav.getByRole("link", { name: label, exact: true });
      await expect(link).toHaveAttribute("href", href);
      await expect(link).not.toHaveAttribute("aria-disabled", "true");
    }

    await expect(page.getByRole("heading", { name: "Insights" })).toBeVisible();
    // The page opens on the institution's latest computed snapshot, not on today.
    await expect(page.locator('input[type="date"]').first()).toHaveValue(asOf);
  });

  test("what this reader may analyse is the catalogue the server filtered, not a claim about it", async ({
    page,
  }) => {
    await page.goto("/insights");

    const analyse = page.locator("section.card").filter({
      has: page.getByRole("heading", {
        name: "What you can analyse",
        level: 3,
      }),
    });

    // A real, non-trivial catalogue: four figures of measures and two of fields.
    // The counts are not pinned to an exact number on purpose — the catalogue
    // grows — but an empty or single-digit catalogue fails this, which is the
    // vacuity this assertion exists to prevent.
    await expect(analyse).toContainText(
      /[\d,]{3,} measures and [\d,]+ fields are available to you\./,
    );

    // The module · sensitivity pairs an organization-wide Analyst holds, in the
    // product's own words — never the wire codes `credit` / `liq` / `aggregated`.
    for (const pair of [
      "Credit · Aggregated",
      "Credit · Confidential",
      "Credit · Restricted",
      "Liquidity Monitoring · Aggregated",
      "Basel Capital · Aggregated",
      "Risk & Limits · Confidential",
      "IRRBB · Aggregated",
      "Foreign Exchange · Aggregated",
    ]) {
      await expect(
        analyse.getByText(pair, { exact: true }),
        `the catalogue must name the ${pair} pair in production copy`,
      ).toBeVisible();
    }
    await expect(analyse.getByText("liq", { exact: true })).toHaveCount(0);
    await expect(analyse.getByText("aggregated", { exact: true })).toHaveCount(
      0,
    );

    // This reader is refused nothing, so the withheld-fields sentence must be
    // absent. `bi-authorization.spec.ts` asserts it PRESENT for a reader who is.
    await expect(
      page.getByText(/further field(s)? exist/),
    ).toHaveCount(0);

    await analyse.getByRole("link", { name: /Open Explore/ }).click();
    await expect(page).toHaveURL(/\/explore$/);
  });

  test("the statements the platform will make, and the ones it withholds from a narrower reader", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    const route = `/banks/${SAMPLE_BANK_ID}/bi/insights?as_of=${asOf}`;

    const wide = await biApi(request, "admin", route);
    expect(wide.status).toBe(200);
    const wideBody = wide.body as {
      as_of: string;
      compare_to: string;
      insights: {
        rule_id: string;
        headline: string;
        detail: string;
        qualifiers: string[];
      }[];
    };

    // A statement is composed server-side from typed facts, and it is compared
    // against a prior period the caller did not have to name.
    expect(wideBody.as_of).toBe(asOf);
    expect(wideBody.compare_to).not.toBe(asOf);
    expect(wideBody.insights.length).toBeGreaterThan(0);

    // A figure that was not computed is SAID to be absent, in words, and is
    // explicitly not reported as zero or as flat.
    const gap = wideBody.insights.find(
      (insight) => insight.rule_id === "data_gap",
    );
    expect(gap).toBeDefined();
    expect(gap!.detail).toContain("It is not zero, and it has not stayed flat.");

    // THE DISCLOSURE PROPERTY OF THE STRIP. A reader whose only sentence is
    // Liquidity/aggregated is told about liquidity figures and about nothing
    // else — not even that the credit figures exist.
    const narrow = await biApi(request, "liquidity_viewer", route);
    expect(narrow.status).toBe(200);
    // Withheld measures are a COUNT, never ids: ten were kept back, and the
    // response says how many without saying which.
    expect(
      (narrow.body as { measures_read: number; measures_withheld: number })
        .measures_read,
    ).toBe(2);
    expect(
      (narrow.body as { measures_withheld: number }).measures_withheld,
    ).toBeGreaterThan(0);
    const narrowText = JSON.stringify(narrow.body);
    expect(narrowText).toContain("Liquidity coverage ratio (LCR)");
    for (const withheld of [
      "loans.npl_ratio_pct",
      "engine.car_pct",
      "Gross loans",
      "Non-performing exposure",
      "Capital adequacy ratio",
      "Tier 1 ratio",
      "Net open position",
    ]) {
      expect(
        narrowText.includes(withheld),
        `a Liquidity-only reader's insights must not name ${withheld}`,
      ).toBe(false);
    }
  });

  test("the strip states what the platform will say, under the page header", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    // Read from the route first, so what the screen is asserted to carry is the
    // platform's own words rather than this spec's guess at them.
    const { status, body } = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/insights?as_of=${asOf}`,
    );
    expect(status).toBe(200);
    const served = body as {
      insights: { rule_id: string; headline: string; detail: string }[];
      measures_read: number;
    };
    expect(served.measures_read).toBeGreaterThan(0);

    await page.goto("/insights");
    await expect(page.getByRole("heading", { name: "Insights" })).toBeVisible();

    // The statement the engine composed for the figure it could not compute is
    // on screen verbatim, in its own card (`InsightCard` renders an
    // `article.card`) — the route half of this journey pins that a `data_gap`
    // statement is served for the fixture, so its absence here would be the
    // screen failing to tell the reader, not the platform having nothing to say.
    const gap = served.insights.find(
      (insight) => insight.rule_id === "data_gap",
    )!;
    await expect(page.getByText(gap.headline, { exact: true })).toBeVisible();
    await expect(
      page.locator("article.card").filter({ hasText: gap.headline }).first(),
    ).toBeVisible();

    // A figure that was not computed is SAID to be absent, in the platform's own
    // words — and explicitly not as a zero or as flat. This sentence is the
    // executable form of the rule the whole engine is built around.
    await expect(
      page.getByText(/It is not zero, and it has not stayed flat\./).first(),
    ).toBeVisible();

    // AND THE EMPTY STATE IS NOT SHOWN, in either of its two forms. The strip must
    // never report "nothing stands out" over a date it read nothing for, and this
    // date it read plenty.
    await expect(
      page.getByText("Nothing stands out for this reporting date.", {
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByText(
        /No analytics figures have been computed for this reporting date yet/,
      ),
    ).toHaveCount(0);
  });
});
