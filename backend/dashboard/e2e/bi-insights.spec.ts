/** Browser catalogue, navigation and certified insight rendering.
 * Per-reader payload filtering lives in contracts/bi-insights.spec.ts. */
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

    const analyse = page.locator("section").filter({
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
    await expect(page.getByText(/further field(s)? exist/)).toHaveCount(0);

    await analyse.getByRole("link", { name: /Open Explore/ }).click();
    await expect(page).toHaveURL(/\/explore$/);
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
