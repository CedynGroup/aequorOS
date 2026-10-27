/** Browser disclosure and navigation boundaries for actual scoped BI readers.
 * HTTP query refusals live in contracts/bi-authorization.spec.ts. */
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "./support/bi";

const BI_ENTRIES = ["Insights", "Dashboards", "Explore"] as const;

/**
 * What the Insights page holds for a wider reader, which a Liquidity-only
 * reader must not be told.
 *
 * The member labels are catalogue members outside this reader's sentence, which
 * the catalogue withholds as a count and the UI never renders for them. The
 * figures are the institution's loan and deposit totals, in every form they
 * could surface in — full precision, the compact display form, and the gap
 * between the position book and the balance sheet.
 */
const WITHHELD_MEMBER_LABELS = [
  "Gross loans",
  "Non-performing exposure",
  "Days past due band",
  "Number of positions",
  "Positions without a reporting-currency conversion",
  "Branch code",
  "Profit and loss line",
] as const;

const WITHHELD_FIGURES = [
  "84850000",
  "84,850,000",
  "GHS 84.9M",
  "1400000000",
  "1,400,000,000",
  "1315150000",
  "1,315,150,000",
] as const;

/** Assert that not one of these strings is anywhere in the rendered document. */
async function assertAbsentFromPage(
  page: Page,
  strings: readonly string[],
  why: string,
): Promise<void> {
  const document = (await page.locator("body").innerText()).replace(
    /\s+/g,
    " ",
  );
  const markup = await page.content();
  for (const withheld of strings) {
    expect(
      document.includes(withheld),
      `${why}: "${withheld}" is on screen`,
    ).toBe(false);
    expect(
      markup.includes(withheld),
      `${why}: "${withheld}" is in the markup (a title, an aria-label or a hidden node)`,
    ).toBe(false);
  }
}

test.describe("a deployment that does not serve BI", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("offers no door, and refuses a deep link, without asking for a grant", async ({
    page,
  }) => {
    // `GET /feature-flags` is mounted unconditionally — a flag nobody can read
    // cannot gate anything — so this is exactly what a BI-less deployment says.
    await page.route("**/api/v1/feature-flags", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          bi_enabled: false,
          bi_mart_enqueue_enabled: false,
          bi_scheduler_enabled: false,
        }),
      });
    });

    await page.goto("/");
    const nav = page.getByRole("navigation");
    for (const label of BI_ENTRIES) {
      await expect(
        nav.getByRole("link", { name: label, exact: true }),
        `${label} must be hidden, not disabled: a deployment flag is not a grant`,
      ).toHaveCount(0);
    }
    // And the group heading goes with them rather than standing over nothing.
    await expect(nav.getByText("Intelligence", { exact: true })).toHaveCount(0);

    // A deep link is NOT FOUND rather than redirected to a hub with a sentence:
    // this reader holds the authority BI reads from, so there is nothing for
    // them to ask for — the surface simply does not exist here. (A baseline
    // member, who does have a grant to ask for, is redirected instead; that is
    // the next describe block.)
    await page.goto("/insights");
    await expect(
      page.getByText(/404|not found|could not be found/i).first(),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Insights" })).toHaveCount(
      0,
    );
  });
});

test.describe("a baseline member with no binding", () => {
  test.use({ storageState: path.join(E2E_TMP, "viewer.json") });

  test("is told which grant opens Business Intelligence, and reaches no BI route", async ({
    page,
  }) => {
    const biRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\/bi\//.test(request.url())) {
        biRequests.push(request.url());
      }
    });

    await page.goto("/");
    const nav = page.getByRole("navigation");
    for (const label of BI_ENTRIES) {
      const entry = nav.getByRole("link", { name: label, exact: true });
      await expect(entry).toBeVisible();
      await expect(entry).toHaveAttribute("aria-disabled", "true");
      await expect(entry).not.toHaveAttribute("href", /.+/);
    }

    // BI has no sentence of its own, so the sentence names a module the
    // catalogue draws from rather than inventing a grant nobody can issue.
    const insights = nav.getByRole("link", { name: "Insights", exact: true });
    await insights.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires a view grant on a module Business Intelligence reads, " +
          "such as Credit · Aggregated · View. Ask your organization owner or " +
          "admin to grant it.",
      }),
    ).toBeVisible();

    await page.goto("/explore");
    await expect(
      page.getByText(/404|not found|No authorized institutions yet/i).first(),
    ).toBeVisible();
    expect(biRequests).toEqual([]);
  });
});

test.describe("a reader whose access does not cover the view", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("is refused uncertified headlines without disclosing withheld figures", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);

    // Catalogue access is real, but advisory liquidity metrics are not certified
    // headline candidates. A grant must not turn them into official statements.
    const catalogue = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/catalogue`,
    );
    expect(catalogue.status).toBe(200);
    expect(
      (catalogue.body as { measures: unknown[] }).measures.length,
    ).toBeGreaterThan(0);
    const strip = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/insights?as_of=${asOf}`,
    );
    expect(strip.status).toBe(403);
    expect(strip.body).toMatchObject({
      error: { details: { error_code: "bi_insights_authorization_denied" } },
    });

    await page.goto("/insights");
    await expect(page.getByRole("heading", { name: "Insights" })).toBeVisible();
    await expect(
      page.getByText(
        "Your access does not cover everything this view needs. An organization owner can grant it.",
        { exact: true },
      ),
    ).toBeVisible();

    // THE DISCLOSURE PROPERTY. Nothing outside this reader's sentence reaches
    // the document — not a member label the catalogue withheld, and not a
    // loan-book figure in any of the forms it could surface in.
    await assertAbsentFromPage(
      page,
      WITHHELD_MEMBER_LABELS,
      "a Liquidity-only Insights page must name no withheld field",
    );
    await assertAbsentFromPage(
      page,
      WITHHELD_FIGURES,
      "a Liquidity-only Insights page must carry no loan-book figure",
    );
  });

  test("sees a catalogue filtered to its own sentence, with the rest reported as a count", async ({
    page,
    request,
  }) => {
    const catalogue = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/catalogue`,
    );
    expect(catalogue.status).toBe(200);
    const served = catalogue.body as {
      measures: { module: string; sensitivity: string }[];
      dimensions: { module: string; sensitivity: string }[];
      withheld_members: number;
    };
    // Exactly one module · sensitivity pair reaches this reader.
    const pairs = new Set(
      [...served.measures, ...served.dimensions].map(
        (member) => `${member.module}/${member.sensitivity}`,
      ),
    );
    expect([...pairs]).toEqual(["liq/aggregated"]);
    expect(served.withheld_members).toBeGreaterThan(0);

    await page.goto("/insights");
    const analyse = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: "What you can analyse",
        level: 3,
      }),
    });

    // The counts the server returned, on screen. Compared numerically for the
    // same reason as the withheld count below.
    const availability = analyse.getByText(/are available to you\./);
    await expect(availability).toBeVisible();
    const counts = (await availability.innerText())
      .replace(/,/g, "")
      .match(/(\d+) measures? and (\d+) fields? are available to you\./);
    expect(
      counts,
      "the catalogue sentence must state both counts",
    ).not.toBeNull();
    expect(Number(counts![1])).toBe(served.measures.length);
    expect(Number(counts![2])).toBe(served.dimensions.length);
    expect(served.measures.length).toBeGreaterThan(0);
    // The single pair, in production copy — never the wire code `liq`.
    await expect(
      analyse.getByText("Liquidity Monitoring · Aggregated", { exact: true }),
    ).toBeVisible();
    for (const absent of [
      "Credit · Aggregated",
      "Credit · Confidential",
      "Credit · Restricted",
      "Basel Capital · Aggregated",
      "Risk & Limits · Confidential",
    ]) {
      await expect(
        analyse.getByText(absent, { exact: true }),
        `a Liquidity-only catalogue must not name the ${absent} pair`,
      ).toHaveCount(0);
    }

    // What is kept back is a COUNT and a route to a grant — never a list. The
    // count on screen is compared numerically with the server's, so the
    // thousands separator the active jurisdiction happens to use cannot make
    // this assertion pass or fail for the wrong reason.
    const withheldSentence = analyse.getByText(
      /further fields exist that your access does not cover/,
    );
    await expect(withheldSentence).toBeVisible();
    const withheldText = await withheldSentence.innerText();
    expect(
      Number(withheldText.replace(/\D/g, "")),
      "the page must report the same number of withheld fields the server withheld",
    ).toBe(served.withheld_members);
    await assertAbsentFromPage(
      page,
      ["loans.balance_rc", "counterparty.name", "loan.employer", "Gross loans"],
      "a withheld member must never be named on the catalogue surface",
    );
  });
});
