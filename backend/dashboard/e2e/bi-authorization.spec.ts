/**
 * Who may see what in Business Intelligence, and what a refusal is allowed to say.
 *
 * Four readers, each proving a different half of the rule that BI has no grant of
 * its own: every measure and dimension carries the module and sensitivity of the
 * thing it reports on, and the query route requires every one the submitted
 * question touches.
 *
 *  1. A DEPLOYMENT WITHOUT BI offers no door at all. The flag is not a grant, so
 *     the three Intelligence entries are HIDDEN rather than disabled-with-a-
 *     sentence, and a deep link refuses. Driven by intercepting `/feature-flags`,
 *     which is the one BI route that is mounted unconditionally.
 *  2. A BASELINE MEMBER with no binding is offered the door, disabled, with the
 *     sentence an owner would act on — because here there IS a grant to ask for.
 *  3. A LIQUIDITY-ONLY READER is the sharpest case. The reconciliation payload
 *     discloses the institution's loan total, its deposit total and its branch
 *     codes, so the route requires every one of those sentences and refuses the
 *     whole page to a reader holding one. What they get back is `Access
 *     restricted` and NOTHING ELSE: this spec asserts that none of the ten check
 *     labels, none of the nine denied member labels and none of the figures
 *     appear anywhere in the document. A lock icon is not the property — the
 *     absence of the withheld strings is.
 *  4. THE SAME READER'S CATALOGUE is filtered member by member, and what is kept
 *     back is reported as a COUNT. The count is asserted, and so is the fact that
 *     the ids behind it never reach the page.
 */

import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, EXPECTED_CHECKS, fixtureAsOf, SAMPLE_BANK_ID } from "./support/bi";

const BI_ENTRIES = ["Insights", "Dashboards", "Explore"] as const;

/**
 * Everything the reconciliation page would have said, which a refused reader
 * must not be told.
 *
 * The check labels come from `read_bi.CHECK_LABELS`; the member labels are the
 * ones the 403 body names for the OPERATOR writing the grant, and which the UI
 * deliberately does not render for the reader. The figures are the two totals
 * R2 and R3 disclose, in every form they could surface in — full precision, the
 * compact display form, and the raw difference.
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
  const document = (await page.locator("body").innerText()).replace(/\s+/g, " ");
  const markup = await page.content();
  for (const withheld of strings) {
    expect(document.includes(withheld), `${why}: "${withheld}" is on screen`).toBe(
      false,
    );
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
    await expect(page.getByRole("heading", { name: "Insights" })).toHaveCount(0);
    await expect(
      page.getByText("Reconciliation", { exact: true }),
    ).toHaveCount(0);
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
      page
        .getByText(/404|not found|No authorized institutions yet/i)
        .first(),
    ).toBeVisible();
    expect(biRequests).toEqual([]);
  });
});

test.describe("a reader whose access does not cover the view", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("is refused the reconciliation position, and told nothing about what it holds", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);

    // First, the refusal on the wire — so the strings this reader was refused
    // are known from the server rather than guessed at.
    const refused = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/trust?as_of=${asOf}`,
    );
    expect(refused.status).toBe(403);
    const detail = (
      refused.body as {
        error: { details: { error_code: string; denied_member_labels: string[] } };
      }
    ).error.details;
    expect(detail.error_code).toBe("bi_authorization_denied");
    // The 403 names the members FOR THE OPERATOR. That is deliberate and is the
    // one place they are named.
    expect(detail.denied_member_labels).toContain("Gross loans");

    await page.goto("/insights");
    const reconciliation = page.locator("section.card").filter({
      has: page.getByRole("heading", { name: "Reconciliation", level: 3 }),
    });
    await expect(reconciliation).toBeVisible();

    // What the reader is given: a refusal, and who can change it.
    await expect(
      reconciliation.getByText("Access restricted", { exact: true }),
    ).toBeVisible();
    await expect(
      reconciliation.getByText(
        "Your access does not cover everything this view needs. An organization owner can grant it.",
      ),
    ).toBeVisible();

    // THE DISCLOSURE PROPERTY. None of the ten checks is named — not even the
    // ones that passed. "Deposit balances agree with the balance sheet" would
    // tell this reader the institution's deposits were reconciled and against
    // what; the verdict is one statement about the whole book.
    await assertAbsentFromPage(
      page,
      EXPECTED_CHECKS.map((check) => check.label),
      "a refused reconciliation must name no check",
    );
    await assertAbsentFromPage(
      page,
      WITHHELD_MEMBER_LABELS,
      "a refused reconciliation must name no withheld field",
    );
    await assertAbsentFromPage(
      page,
      WITHHELD_FIGURES,
      "a refused reconciliation must carry no figure",
    );
    // And none of the badge verdicts, which would leak the outcome without the
    // figures.
    for (const verdict of [
      "Does not reconcile",
      "Differences found",
      "Not assessed",
      "Reconciled",
    ]) {
      await expect(
        reconciliation.getByText(verdict, { exact: true }),
        `a refused reconciliation must show no verdict, and it shows "${verdict}"`,
      ).toHaveCount(0);
    }
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
    const analyse = page.locator("section.card").filter({
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
    expect(counts, "the catalogue sentence must state both counts").not.toBeNull();
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

  test("is refused a query it could not have built, and gets no partial answer", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    // The catalogue never offered this member to this reader, so the only way
    // to ask is to construct the request — which is exactly what an attacker
    // would do, and the route decides again rather than trusting the surface.
    const { status, body } = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/query`,
      {
        method: "POST",
        data: {
          measures: ["loans.balance_rc"],
          dimensions: ["counterparty.name"],
          time: { as_of: asOf },
          filters: [],
        },
      },
    );
    expect(status).toBe(403);
    const text = JSON.stringify(body);
    expect(text).toContain("bi_authorization_denied");
    // No rows, no total, no partial column set.
    expect(text).not.toContain("84850000");
    expect(text).not.toContain('"rows"');
  });
});
