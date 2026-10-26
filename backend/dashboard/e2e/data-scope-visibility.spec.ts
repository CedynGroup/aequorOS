/**
 * Real Credit bindings restrict rows, counts and figures to the same declared
 * branch slice that the browser names. Institution-wide readers see no coverage
 * notice; scoped readers receive qualified labels on the loan book, activity,
 * Explore, certified packs and Insights. An empty authorized slice is distinct
 * from an empty institution book. Every story uses fresh principals; scoped
 * stories compare their response with a whole-book control on the same bank.
 */

import { expect } from "@playwright/test";
import { test as identityTest } from "./support/story-identities";
import { CreditLoansPageReadFromJSON } from "@aequoros/risk-service-api";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID, biApi, FIXTURE_FIGURES } from "./support/bi";

const BRANCHES = ["BR-001"];
const test = identityTest.extend<{
  sliceBranches: string[];
  sliceReferences: string[];
}>({
  sliceBranches: [BRANCHES, { option: true }],
  sliceReferences: [
    ["LOAN/1", "LOAN/4", "LOAN/6", "LOAN/USD"],
    { option: true },
  ],
});

/** The opening clause of every narrowed sentence (`lib/api/dataScope.ts`). */
const QUALIFIED_OPENER = "You are seeing part of this institution's book.";

/** The whole-book sentence must never appear: there is no such sentence. */
const NOTICE = "[data-testid='coverage-notice']";

test.describe("a reader whose grant covers the whole book", () => {
  test.use({
    storageState: path.join(E2E_TMP, "analyst.json"),
    storyConfig: { roles: ["analyst"] },
  });

  /**
   * THE NEGATIVE HALF, and it is the one that protects every other reader in the
   * product. The fresh Analyst holds the default
   * institution-wide scope, so a coverage notice here would be a false statement
   * shown to everybody.
   */
  for (const [surface, route, dataRoute] of [
    ["the loan blotter", "/credit/book", /\/credit\/loans\?/],
    ["loan-book activity", "/credit/activity", /\/credit\/activity(?:\?|$)/],
    ["Explore", "/explore", /\/bi\/catalogue(?:\?|$)/],
    ["Insights", "/insights", /\/bi\/insights(?:\?|$)/],
    // A certified pack: authored by the platform but ANSWERED under the reader's
    // own scope, which is why this screen carries the notice at all.
    ["a certified pack", "/dashboards/credit", /\/bi\/query(?:\?|$)/],
  ] as const) {
    test(`is told nothing about coverage on ${surface}`, async ({ page }) => {
      const ready = page.waitForResponse((response) =>
        dataRoute.test(response.url()),
      );
      await page.goto(route);
      expect((await ready).status()).toBe(200);
      if (route === "/explore") {
        const figures = page.waitForResponse((response) =>
          /\/bi\/query$/.test(response.url()),
        );
        await page
          .locator("section.card")
          .filter({
            has: page.getByRole("heading", { name: "Measure", level: 3 }),
          })
          .locator("label")
          .filter({
            has: page.getByText("Gross loans", { exact: true }),
          })
          .first()
          .locator('input[type="checkbox"]')
          .check();
        expect((await figures).status()).toBe(200);
        const grouped = page.waitForResponse((response) =>
          /\/bi\/query$/.test(response.url()),
        );
        await page
          .locator("section.card")
          .filter({
            has: page.getByRole("heading", { name: "Break down by", level: 3 }),
          })
          .locator("label")
          .filter({
            has: page.getByText("Classification grade", { exact: true }),
          })
          .first()
          .locator('input[type="checkbox"]')
          .check();
        expect((await grouped).status()).toBe(200);
        const answer = page.locator("section.card").filter({
          has: page.getByRole("heading", { name: "Your question", level: 3 }),
        });
        await expect(
          answer.getByText(FIXTURE_FIGURES.standardGradeOnScreen, {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          answer.getByText(FIXTURE_FIGURES.substandardGradeOnScreen, {
            exact: true,
          }),
        ).toBeVisible();
      }
      await expect(page.locator("h1").first()).toBeVisible();
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
      await expect(page.locator(NOTICE)).toHaveCount(0);
      const text = (await page.locator("body").innerText()).replace(
        /\s+/g,
        " ",
      );
      expect(text).not.toContain(QUALIFIED_OPENER);
      expect(text).not.toContain("in your branches");
    });
  }
});

test.describe("a reader whose grant covers some branches", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer", "analyst"],
      bindings: [
        {
          bundle: "viewer",
          module: "credit",
          sensitivity: "all",
          branches: BRANCHES,
        },
      ],
    },
  });
  test.beforeEach(async ({ request, sliceBranches, sliceReferences }) => {
    const route = `/banks/${SAMPLE_BANK_ID}/credit/loans?limit=100`;
    const whole = await biApi(request, "analyst", route);
    const narrowed = await biApi(request, "viewer", route);
    expect(whole.status).toBe(200);
    expect(narrowed.status).toBe(200);
    const fullBook = CreditLoansPageReadFromJSON(whole.body);
    const slice = CreditLoansPageReadFromJSON(narrowed.body);
    expect(fullBook.total).toBe(7);
    expect(
      fullBook.rows.filter((loan) => loan.branchId === "BR-002"),
    ).toHaveLength(3);
    expect(fullBook.rows.map((loan) => loan.sourceReference)).toContain(
      "LOAN/2",
    );
    expect(fullBook.dataScope.kind).toBe("all");
    expect(slice.total).toBe(sliceReferences.length);
    expect(slice.filtered).toBe(sliceReferences.length);
    expect(slice.rows.map((loan) => loan.sourceReference).sort()).toEqual(
      [...sliceReferences].sort(),
    );
    expect(slice.dataScope).toMatchObject({
      kind: "branch",
      branches: sliceBranches,
    });
    expect(
      slice.rows.every((loan) => sliceBranches.includes(loan.branchId ?? "")),
    ).toBe(true);
    const escape = await biApi(request, "viewer", `${route}&branch=BR-002`);
    expect(escape.status).toBe(200);
    const deniedSlice = CreditLoansPageReadFromJSON(escape.body);
    expect(deniedSlice.total).toBe(sliceReferences.length);
    expect(deniedSlice.filtered).toBe(0);
    expect(deniedSlice.rows).toEqual([]);
    expect(deniedSlice.dataScope).toMatchObject({
      kind: "branch",
      branches: sliceBranches,
    });
  });

  test("the loan blotter says so, names the branches, and qualifies the totals", async ({
    page,
  }) => {
    await page.goto("/credit/book");
    const notice = page.locator(NOTICE);
    await expect(notice).toBeVisible();
    await expect(notice).toHaveAttribute("data-coverage-determined", "yes");
    await expect(
      notice.getByText(
        `${QUALIFIED_OPENER} These figures cover only the branches your access names, so they are not the institution's own totals.`,
      ),
    ).toBeVisible();
    // WHICH part, in the institution's own tokens.
    for (const code of BRANCHES) {
      await expect(notice.getByText(new RegExp(code))).toBeVisible();
    }

    // THE QUALIFICATION IS ALSO ON THE FIGURE'S OWN LABEL. A banner is a thing a
    // reader can miss; the words beside the number are what get quoted.
    await expect(
      page.getByText("Loans on book in your branches", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Loans on book", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByText("Matching filters in your branches", { exact: true }),
    ).toBeVisible();
    // The pager's denominator carries it too, because that is a count as well.
    await expect(page.getByText(/loans in your branches$/)).toBeVisible();
    // And the filter choices are drawn from the same slice, which is said once.
    await expect(
      page.getByText(
        /These filter choices and their counts are drawn from the same part/,
      ),
    ).toBeVisible();
  });

  test.describe("a branch with no loans", () => {
    test.use({
      sliceBranches: ["BR-NOLOANS"],
      sliceReferences: [],
      storyConfig: {
        roles: ["viewer", "analyst"],
        bindings: [
          {
            bundle: "viewer",
            module: "credit",
            sensitivity: "all",
            branches: ["BR-NOLOANS"],
          },
        ],
      },
    });
    test("an EMPTY blotter under that grant is not reported as an empty book", async ({
      page,
    }) => {
      // THE DEFECT THIS CLOSES. Before P4-I a scoped reader whose branches hold no
      // loans was told "No loans in the canonical book yet" and sent to the Data
      // Engine — a claim about the institution that they are in no position to make,
      // and an instruction to fix a problem that is not theirs.
      await page.goto("/credit/book");
      // `EmptyState` typesets its title as a `<p class="text-h3">` and not as a
      // heading element, so this is addressed as text. (Measured: the first run of
      // this journey looked for a heading role and found none while the page was
      // rendering the sentence perfectly.)
      await expect(
        page.getByText("No loans in the part of the book you can see", {
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        page.getByText(
          /Nothing was returned for the branches your access covers/,
        ),
      ).toBeVisible();
      // The ingestion call to action is WITHHELD: it is not this reader's problem.
      await expect(
        page.getByRole("link", { name: "Open the Data Engine" }),
      ).toHaveCount(0);
      await expect(
        page.getByText("No loans in the canonical book yet"),
      ).toHaveCount(0);
    });
  });

  test.describe("whole-summary and scoped activity grants", () => {
    test.use({
      storyConfig: {
        roles: ["viewer", "analyst"],
        bindings: [
          { bundle: "viewer", module: "credit", sensitivity: "aggregated" },
          {
            bundle: "viewer",
            module: "credit",
            sensitivity: "confidential",
            branches: BRANCHES,
          },
          {
            bundle: "viewer",
            module: "credit",
            sensitivity: "restricted",
            branches: BRANCHES,
          },
        ],
      },
    });
    test("loan-book activity says so, and its counts are qualified", async ({
      page,
    }) => {
      await page.goto("/credit/activity");
      await expect(page.locator(NOTICE)).toBeVisible();
      await expect(
        page.getByText("Restructures (12m) in your branches", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("Disbursements / repayments in your branches", {
          exact: true,
        }),
      ).toBeVisible();
    });
  });

  /**
   * EXPLORE IS THE DERIVED CASE. The BI query payload discloses no scope at all,
   * so the coverage comes from the per-capability scope `/auth/me` projects,
   * resolved against the (module, sensitivity) addresses the question itself
   * reads. It is `exact` precision, so the sentence may speak about "these
   * figures" — and it appears only once a CREDIT figure is actually chosen.
   */
  test("Explore says nothing until a narrowed figure is in the question, and then says it exactly", async ({
    page,
  }) => {
    await page.goto("/explore");
    await expect(page.getByRole("heading", { name: "Explore" })).toBeVisible();
    // No figure chosen: nothing is on screen to qualify, so nothing is claimed.
    await expect(page.locator(NOTICE)).toHaveCount(0);

    const measures = page.locator("section.card").filter({
      has: page.getByRole("heading", { name: "Measure", level: 3 }),
    });
    await measures
      .locator("label")
      .filter({ has: page.getByText("Gross loans", { exact: true }) })
      .first()
      .locator('input[type="checkbox"]')
      .check();

    const answer = page.locator("section.card").filter({
      has: page.getByRole("heading", { name: "Your question", level: 3 }),
    });
    await expect(answer.getByText("GHS 57.9M", { exact: true })).toBeVisible();
    const notice = page.locator(NOTICE);
    await expect(notice).toBeVisible();
    await expect(
      notice.getByText(
        `${QUALIFIED_OPENER} These figures cover only the branches your access names, so they are not the institution's own totals.`,
      ),
    ).toBeVisible();
    // The answer's own caption carries it, because a chart is what gets
    // screenshotted out of this page and the banner does not travel with it.
    await expect(
      page.getByText(/measure for .*, in your branches$/),
    ).toBeVisible();
  });

  test("a certified pack says part of it is narrowed, resolved from its OWN views", async ({
    page,
  }) => {
    // The pack's addresses come from the widgets' own queries rather than from the
    // reader's whole catalogue, so a pack built only from institution-wide figures
    // would say nothing even for this reader. `credit` is a CREDIT pack, so it is
    // covered by the narrowed capability and the notice is earned.
    await page.goto("/dashboards/credit");
    const notice = page.locator(NOTICE);
    await expect(notice).toBeVisible();
    await expect(
      notice.getByText(/Some of what is shown here covers only/),
    ).toBeVisible();
    for (const code of BRANCHES) {
      await expect(notice.getByText(new RegExp(code))).toBeVisible();
    }
  });

  /**
   * INSIGHTS IS THE HEDGED CASE, and the wording is deliberately weaker. The
   * statements name no member on the wire, so the reader's grants describe what
   * the page COULD read and not which statement was narrowed. Claiming "these
   * figures" there would be a fabricated specific.
   */
  test("Insights says part of the page is narrowed, and does not invent which part", async ({
    page,
  }) => {
    await page.goto("/insights");
    const notice = page.locator(NOTICE);
    await expect(notice).toBeVisible();
    await expect(
      notice.getByText(/Some of what is shown here covers only/),
    ).toBeVisible();
    await expect(
      notice.getByText(/This page does not say which of them/),
    ).toBeVisible();
    // And it must NOT make Explore's exact claim on this surface.
    const text = (await notice.innerText()).replace(/\s+/g, " ");
    expect(text).not.toContain("These figures cover only");
  });
});
