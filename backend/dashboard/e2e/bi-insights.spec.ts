/**
 * Insights — the reconciliation position, the catalogue a reader may analyse,
 * and the statements the platform is prepared to make about a reporting date.
 *
 * Every assertion here names a figure, a production label or a refusal that
 * could only appear if the whole chain worked: the Data Engine fixture book →
 * the live plane → the `bi_*` marts → the reconciliation checks → the badge. A
 * navigate-and-see-a-page test would prove none of it, which is why the
 * catalogue counts, the difference strings and the per-check verdicts are all
 * pinned individually rather than through "the section rendered".
 *
 * THE BADGE THE FIXTURE EARNS IS RED, AND THAT IS ASSERTED AS RED. The canonical
 * fixture's position balances genuinely do not sum to its balance-sheet facts
 * and its loans carry no days past due, so R2, R3 and R10 fail, R6 and R7 report
 * differences, R4 was never assessed and four checks pass. A mixed verdict over
 * a partial book is exactly what a real bank mid-onboarding sees, and it is the
 * state worth pinning — the alternative is to edit the fixture until the badge
 * flatters it, which would delete the coverage rather than earn it.
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
import {
  biApi,
  EXPECTED_CHECKS,
  EXPECTED_OVERALL_BADGE,
  fixtureAsOf,
  SAMPLE_BANK_ID,
} from "./support/bi";

test.describe("Insights", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("the reconciliation position, check by check, with the verdict the book earns", async ({
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

    // `section.card` is the SectionCard shell; the heading identifies which one.
    const reconciliation = page
      .locator("section.card")
      .filter({
        has: page.getByRole("heading", { name: "Reconciliation", level: 3 }),
      });

    // The overall verdict, in the section's own action slot. Red, because three
    // checks failed — asserted against the fixture, never wished green.
    await expect(
      reconciliation.getByText(EXPECTED_OVERALL_BADGE, { exact: true }).first(),
    ).toBeVisible();

    // Every check, by its own production label, with its own verdict. Scoped to
    // the row so four "Reconciled" badges cannot satisfy one another.
    for (const check of EXPECTED_CHECKS) {
      const row = reconciliation
        .locator("li")
        .filter({ hasText: check.label })
        .first();
      await expect(row, `${check.id}: ${check.why}`).toBeVisible();
      await expect(
        row.getByText(check.badge, { exact: true }),
        `${check.id} must read "${check.badge}" — ${check.why}`,
      ).toBeVisible();
    }

    // A failing check states the difference AND the tolerance it broke, in the
    // platform's own precision. These are the marts measured against the live
    // fact plane; neither number can be produced by an empty mart.
    const loanRow = reconciliation
      .locator("li")
      .filter({ hasText: "Loan balances agree with the balance sheet" })
      .first();
    await expect(loanRow).toContainText(
      "Difference -1315150000.000000 against a tolerance of 0.000100",
    );

    // Grey is "not assessed", and it says so rather than reporting a zero
    // difference that would read as agreement.
    const plRow = reconciliation
      .locator("li")
      .filter({
        hasText: "Income-statement lines agree with the regulatory return",
      })
      .first();
    await expect(plRow).toContainText(
      "No difference was measured for this check.",
    );

    // The builds behind the figures. `Engine — Succeeded` exists only because
    // the mart builder found a live plane at the SAME date as the position book;
    // before the H-015 fix it copied zero engine rows and R1/R8 read grey.
    for (const scope of ["Positions", "Engine", "Dims"]) {
      await expect(
        reconciliation.getByText(new RegExp(`${scope} — Succeeded`)),
      ).toBeVisible();
    }
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
        trust: { status: string } | null;
      }[];
    };

    // A statement is composed server-side from typed facts, and it is compared
    // against a prior period the caller did not have to name.
    expect(wideBody.as_of).toBe(asOf);
    expect(wideBody.compare_to).not.toBe(asOf);
    expect(wideBody.insights.length).toBeGreaterThan(0);

    // The reservation rides on the statement: this book does not reconcile, so
    // the strip's first card says so before it says anything else.
    const notice = wideBody.insights.find(
      (insight) => insight.rule_id === "trust_notice",
    );
    expect(notice, "a book that does not reconcile must carry the notice").toBeDefined();
    expect(notice!.headline).toBe(
      "Some figures do not agree with the returns the platform files",
    );
    expect(notice!.trust?.status).toBe("red");

    // Every statement carries its reservations rather than reading clean.
    for (const insight of wideBody.insights) {
      expect(
        insight.qualifiers.length,
        `"${insight.headline}" must carry its reservations`,
      ).toBeGreaterThan(0);
    }

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

    // The reservation first: this book does not reconcile, and the notice the
    // engine composed for that is on screen verbatim.
    const notice = served.insights.find(
      (insight) => insight.rule_id === "trust_notice",
    )!;
    await expect(page.getByText(notice.headline, { exact: true })).toBeVisible();

    // A figure that was not computed is SAID to be absent, in the platform's own
    // words — and explicitly not as a zero or as flat. This sentence is the
    // executable form of the rule the whole engine is built around.
    await expect(
      page.getByText(/It is not zero, and it has not stayed flat\./).first(),
    ).toBeVisible();

    // Every statement on screen carries its reservations: the strip renders the
    // trust verdict beside each one rather than letting a sentence read clean.
    // Scoped to the CARD that carries this statement (`InsightCard` renders an
    // `article.card`), so the badge is asserted on the sentence it qualifies and
    // not satisfied by a badge somewhere else on the page.
    const card = page
      .locator("article.card")
      .filter({ hasText: notice.headline })
      .first();
    await expect(card).toBeVisible();
    await expect(
      card.getByText(EXPECTED_OVERALL_BADGE, { exact: true }).first(),
    ).toBeVisible();
    // A statement never reads clean: it carries at least one reservation beside
    // its badge.
    expect(
      await card.locator("span.rounded.border").count(),
    ).toBeGreaterThan(1);

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
