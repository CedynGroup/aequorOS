/**
 * A reader whose grant covers part of a book is TOLD SO, on every surface that
 * shows them a figure.
 *
 * Phase 4 gave `authorization_bindings` a data scope and the backend honours it
 * everywhere: a branch- or region-scoped reader's rows, totals, pages, facets and
 * counts are all computed over their slice, and the three credit payloads disclose
 * which slice. Until P4-I nothing in the browser said so, so a scoped total was
 * drawn exactly as the institution's own book would be — and a number on a screen
 * is what gets quoted into a board paper.
 *
 * WHY THIS SPEC INTERCEPTS, AND WHY THAT IS HONEST. Every grant
 * `scripts/e2e_bootstrap.py` writes is institution-wide: there is no
 * branch-scoped fixture identity, and `scripts/e2e_bootstrap.py` is not this
 * track's file. A spec that only asserted "no notice appears" would then be
 * exactly the shape AGENTS.md warns about — a green test that proves nothing,
 * because a notice that can never render also never renders wrongly.
 *
 * So each journey runs BOTH halves against the same live stack:
 *
 *  * the NEGATIVE — the real payload, every grant institution-wide, and no
 *    coverage notice anywhere. This is the half that would catch a notice that
 *    cried wolf at every reader in the product.
 *  * the POSITIVE — the same request, answered by the same server, with the scope
 *    field rewritten in flight to the narrowed shape the server WILL send once an
 *    owner composes such a grant. Nothing else about the response is touched:
 *    `route.fetch()` gets the real answer and only the scope is edited, so the
 *    figures, the rows and the counts on screen are the platform's own.
 *
 * The interception is a FIXTURE FOR A GRANT THAT CANNOT YET BE WRITTEN, not a
 * substitute for the server's behaviour — which `backend/tests/api/
 * test_data_scope_grants.py` and `tests/services/bi/test_data_scope.py` own.
 *
 * WIRE SPELLING. The generated client is stale, and `<Model>FromJSON` spreads
 * `...json` before overwriting the fields it knows, so `data_scope` arrives under
 * its snake_case wire name today and becomes `dataScope` the day the client is
 * regenerated. These journeys inject the SNAKE_CASE name — the one a browser
 * actually receives — and `lib/api/dataScope.test.ts` proves both spellings
 * produce identical copy.
 */

import { expect, test, type Page, type Route } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID } from "./support/bi";

/** The branch codes the injected grant names. Arbitrary tokens, as a code is. */
const BRANCHES = ["ACC-001", "TEM-002"];

const NARROWED_SCOPE = {
  kind: "branch",
  branches: BRANCHES,
  regions: [],
  unresolved_regions: [],
};

/** The opening clause of every narrowed sentence (`lib/api/dataScope.ts`). */
const QUALIFIED_OPENER = "You are seeing part of this institution's book.";

/** The whole-book sentence must never appear: there is no such sentence. */
const NOTICE = "[data-testid='coverage-notice']";

/**
 * Answer a route with the server's own response, with one field edited.
 *
 * `route.fetch()` performs the real request, so the figures on screen are the
 * platform's. Only the scope is rewritten.
 */
async function withScope(
  route: Route,
  edit: (body: Record<string, unknown>) => void,
): Promise<void> {
  const response = await route.fetch();
  const body = (await response.json()) as Record<string, unknown>;
  edit(body);
  await route.fulfill({
    response,
    json: body,
    headers: { ...response.headers(), "content-type": "application/json" },
  });
}

/** Narrow every CREDIT view capability `/auth/me` projects for the sample bank. */
async function narrowCreditAuthority(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/me", (route) =>
    withScope(route, (body) => {
      const authority = body.effective_authority as {
        institution_capabilities: {
          institution_id: string;
          capabilities: Record<string, unknown>[];
        }[];
      };
      let narrowed = 0;
      for (const entry of authority.institution_capabilities) {
        if (entry.institution_id !== SAMPLE_BANK_ID) continue;
        for (const capability of entry.capabilities) {
          if (capability.module !== "credit") continue;
          capability.data_scope = NARROWED_SCOPE;
          narrowed += 1;
        }
      }
      // A fixture that narrowed NOTHING would make every assertion below vacuous.
      expect(
        narrowed,
        "the projection must carry credit capabilities to narrow",
      ).toBeGreaterThan(0);
    }),
  );
}

test.describe("a reader whose grant covers the whole book", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  /**
   * THE NEGATIVE HALF, and it is the one that protects every other reader in the
   * product. `admin` holds organization-wide Analyst with the default
   * institution-wide scope, so a coverage notice here would be a false statement
   * shown to everybody.
   */
  for (const [surface, route] of [
    ["the loan blotter", "/credit/book"],
    ["loan-book activity", "/credit/activity"],
    ["Explore", "/explore"],
    ["Insights", "/insights"],
    // A certified pack: authored by the platform but ANSWERED under the reader's
    // own scope, which is why this screen carries the notice at all.
    ["a certified pack", "/dashboards/credit"],
  ] as const) {
    test(`is told nothing about coverage on ${surface}`, async ({ page }) => {
      await page.goto(route);
      // Wait for the surface to have actually drawn something, so the absence is
      // an absence on a rendered page rather than on an empty one.
      await expect(page.locator("h1").first()).toBeVisible();
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
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("the loan blotter says so, names the branches, and qualifies the totals", async ({
    page,
  }) => {
    await page.route("**/api/v1/banks/*/credit/loans?**", (route) =>
      withScope(route, (body) => {
        expect(body.total, "the real payload must carry a total").toBeDefined();
        body.data_scope = NARROWED_SCOPE;
      }),
    );

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

  test("an EMPTY blotter under that grant is not reported as an empty book", async ({
    page,
  }) => {
    // THE DEFECT THIS CLOSES. Before P4-I a scoped reader whose branches hold no
    // loans was told "No loans in the canonical book yet" and sent to the Data
    // Engine — a claim about the institution that they are in no position to make,
    // and an instruction to fix a problem that is not theirs.
    await page.route("**/api/v1/banks/*/credit/loans?**", (route) =>
      withScope(route, (body) => {
        body.data_scope = NARROWED_SCOPE;
        body.total = 0;
        body.filtered = 0;
        body.rows = [];
      }),
    );

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

  test("loan-book activity says so, and its counts are qualified", async ({
    page,
  }) => {
    await page.route("**/api/v1/banks/*/credit/activity**", (route) =>
      withScope(route, (body) => {
        expect(
          body.disbursement_count,
          "the real payload must carry the event counts",
        ).toBeDefined();
        body.data_scope = NARROWED_SCOPE;
      }),
    );

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
    await narrowCreditAuthority(page);
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
    await narrowCreditAuthority(page);
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
   * statements and the reconciliation checks name no member on the wire, so the
   * reader's grants describe what the page COULD read and not which statement was
   * narrowed. Claiming "these figures" there would be a fabricated specific.
   */
  test("Insights says part of the page is narrowed, and does not invent which part", async ({
    page,
  }) => {
    await narrowCreditAuthority(page);
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
