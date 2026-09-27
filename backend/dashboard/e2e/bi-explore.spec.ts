/**
 * Explore — build a question, group it, narrow it, pivot it, follow a figure into
 * the rows behind it, and take it out only through the governed door.
 *
 * Each journey here asserts a FIGURE the fixture book produces, not that a
 * control moved. `GHS 81.9M` against the Standard grade and `GHS 3.0M` against
 * Substandard can only appear if the position book reached the marts, the
 * compiler emitted the group-by, the authorization decision admitted both
 * members, and `formatCell` resolved the institution's own currency — so a
 * single cell assertion carries the whole path.
 *
 * ON THE EXPORT. AG Grid's own CSV/Excel export is deliberately absent: the
 * module is never registered and `suppressCsvExport` / `suppressExcelExport` are
 * set, because an export from the browser has no authorization decision, no audit
 * row and no watermark behind it. Exercising it would be testing the thing the
 * product forbids. So the export journey does two things instead: it proves the
 * page offers NO ungoverned way out, and it drives the governed route itself and
 * asserts the artifact carries its provenance block, its disclosure class and its
 * trust verdict — and that the same request from a reader without the sentence is
 * refused. The Export menu on screen currently offers print only, because the
 * governed route is not bound to a menu item yet; that gap is stated in the
 * journey rather than papered over.
 */

import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import type { BiQuery, BiQueryResult } from "@aequoros/risk-service-api";
import { E2E_TMP } from "../playwright.config";
// The product's own drill mapping, imported rather than re-implemented: the
// property under test is that a destination is offered only when it can
// reproduce the figure's slice exactly, and a hand-written URL would test this
// spec instead of the product.
import { drillDestinations } from "../components/bi/drill";
import {
  biApi,
  FIXTURE_FIGURES,
  fixtureAsOf,
  SAMPLE_BANK_ID,
} from "./support/bi";

/** Tick the named measure in the Measure card. */
async function chooseMeasure(page: Page, label: string): Promise<void> {
  const card = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Measure", level: 3 }),
  });
  await card
    .locator("label")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first()
    .locator('input[type="checkbox"]')
    .check();
}

/** Tick the named field in the Break down by card. */
async function chooseDimension(page: Page, label: string): Promise<void> {
  const card = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Break down by", level: 3 }),
  });
  await card
    .locator("label")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first()
    .locator('input[type="checkbox"]')
    .check();
}

test.describe("Explore", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("a measure, a breakdown and a filter, answered in the institution's own units", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    await page.goto("/explore");
    await expect(page.getByRole("heading", { name: "Explore" })).toBeVisible();
    await expect(page.locator('input[type="date"]').first()).toHaveValue(asOf);

    // Nothing is queried until a measure is chosen, and the Break down by card
    // says why it is offering nothing.
    //
    // NOTE, and reported as defect T20-D1: the `Choose a measure to see an
    // answer` empty state next to the answer area is UNREACHABLE. It renders
    // only when `query === null && blocking.length === 0`, and the one path in
    // `buildExploreQuery` that returns a null query also adds the blocking
    // `no-measure` problem — whose own message is rendered inside a branch gated
    // on at least one measure being chosen. So a reader who lands on Explore is
    // shown no instruction beside the answer area at all. Asserted as it
    // behaves, not as it was meant to.
    await expect(
      page.getByText(
        "Choose a measure first — the fields it can be grouped by depend on what is being measured.",
      ),
    ).toBeVisible();
    await expect(
      page.getByText("Choose a measure to see an answer", { exact: true }),
    ).toHaveCount(0);

    await chooseMeasure(page, "Gross loans");
    await chooseDimension(page, "Classification grade");

    // THE ANSWER. Two grades the fixture book actually holds, each with the
    // figure the marts carry, formatted in the bank's own reporting currency.
    const answer = page.locator("section.card").filter({
      has: page.getByRole("heading", { name: "Your question", level: 3 }),
    });
    await expect(answer.getByText("standard", { exact: true })).toBeVisible();
    await expect(
      answer.getByText(FIXTURE_FIGURES.standardGradeOnScreen, { exact: true }),
    ).toBeVisible();
    await expect(answer.getByText("substandard", { exact: true })).toBeVisible();
    await expect(
      answer.getByText(FIXTURE_FIGURES.substandardGradeOnScreen, {
        exact: true,
      }),
    ).toBeVisible();

    // A second measure of the same time behaviour joins the same answer, and a
    // percentage is shown as a percentage rather than as a raw fraction. Nothing
    // graded standard is non-performing and everything graded substandard is, so
    // the two ratios are 0.00% and 100.00% — figures that come from the
    // classification engine's own rule, not from the row count.
    await chooseMeasure(page, "NPL ratio");
    await expect(
      answer.getByText(FIXTURE_FIGURES.standardNplOnScreen, { exact: true }),
    ).toBeVisible();
    await expect(
      answer.getByText(FIXTURE_FIGURES.substandardNplOnScreen, { exact: true }),
    ).toBeVisible();

    // A FILTER IS A READ, and it is applied by the server. Narrowing to the
    // Substandard grade drops the Standard row out of the answer entirely — a
    // client-side hide would leave it in the DOM.
    //
    // The bar is identified by its own "Reporting date" control rather than by
    // position, so the two selects below cannot be confused with the Break down
    // by checkboxes, which carry the same field names.
    const filterBar = page
      .locator("div.card")
      .filter({ has: page.getByText("Reporting date", { exact: true }) })
      .first();
    // Positional within the bar rather than by label: the value select's own
    // accessible name folds in every option, and the field select's folds in
    // every field name, so both match either label text. The bar renders the
    // field select and then the value select, and nothing else.
    await filterBar
      .locator("select")
      .first()
      .selectOption({ label: "Classification grade" });
    await filterBar
      .locator("select")
      .nth(1)
      .selectOption({ label: "Substandard" });

    // The applied filter is shown as a chip in the reader's own words.
    await expect(
      page.getByText("Classification grade: Substandard"),
    ).toBeVisible();
    await expect(
      answer.getByText(FIXTURE_FIGURES.substandardGradeOnScreen, {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      answer.getByText(FIXTURE_FIGURES.standardGradeOnScreen, { exact: true }),
    ).toHaveCount(0);
    await expect(answer.getByText("standard", { exact: true })).toHaveCount(0);
  });

  test("the full grid rolls the answer up and spreads one field across the columns", async ({
    page,
  }) => {
    await page.goto("/explore");
    await chooseMeasure(page, "Gross loans");
    await chooseDimension(page, "Classification grade");

    await page.getByRole("button", { name: "Full grid", exact: true }).click();
    await expect(
      page.getByText(
        "The answer paged row by row, with subtotals and columns you choose.",
      ),
    ).toBeVisible();

    const shape = page.locator("section.card").filter({
      has: page.getByRole("heading", { name: "Shape the grid", level: 3 }),
    });
    await shape
      .getByText("Show subtotals for each group", { exact: true })
      .click();
    await shape.locator("#bi-grid-pivot").selectOption({ label: "IFRS 9 stage" });

    // The grid is paged by the server, and the columns it returns are the
    // pivoted ones — a measure per value of the spread field, each naming the
    // unit so the reader does not infer it from the first cell.
    const grid = page.locator(".ag-root-wrapper");
    await expect(grid).toBeVisible();
    await expect(
      grid.getByText("Gross loans · 1 (GHS)", { exact: true }),
    ).toBeVisible();
    await expect(
      grid.getByText("Gross loans · 3 (GHS)", { exact: true }),
    ).toBeVisible();
    await expect(
      grid.getByText("Classification grade", { exact: true }),
    ).toBeVisible();

    // The roll-up row, computed by the server from the same book as the rows.
    await expect(
      grid.getByText("Total for the institution", { exact: true }),
    ).toBeVisible();
    await expect(
      grid.getByText(FIXTURE_FIGURES.standardGradeOnScreen).first(),
    ).toBeVisible();

    // Provenance of the page, stated rather than assumed.
    await expect(
      page.getByText(
        /Read from the position book\.|Read from the pre-aggregated tables\./,
      ),
    ).toBeVisible();
  });

  test("the only way out is the governed one, and it carries its provenance", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    await page.goto("/explore");
    await chooseMeasure(page, "Gross loans");
    await chooseDimension(page, "Classification grade");
    await page.getByRole("button", { name: "Full grid", exact: true }).click();
    await expect(page.locator(".ag-root-wrapper")).toBeVisible();

    // NO UNGOVERNED DOOR. AG Grid's export module is never registered, so the
    // grid offers no CSV or Excel action anywhere on the page — the menu the
    // reader can open must not contain one either.
    await page
      .getByRole("button", { name: "Export", exact: false })
      .first()
      .click();
    const menu = page.getByRole("menu");
    await expect(menu).toBeVisible();
    await expect(
      menu.getByRole("menuitem", { name: /Print or save as PDF/ }),
    ).toBeVisible();
    for (const forbidden of [
      /CSV/i,
      /Excel/i,
      /spreadsheet/i,
      /download data/i,
      /copy to clipboard/i,
    ]) {
      await expect(
        menu.getByRole("menuitem", { name: forbidden }),
        `the export menu must offer no ungoverned ${forbidden} action`,
      ).toHaveCount(0);
    }
    await page.keyboard.press("Escape");
    await expect(page.getByText(/Export to (CSV|Excel)/i)).toHaveCount(0);

    // THE GOVERNED PATH ITSELF. `POST …/bi/export` authorizes every member,
    // classifies the disclosure, audits the release and stamps the artifact.
    const query = {
      measures: ["loans.balance_rc"],
      dimensions: ["loan.grade"],
      time: { as_of: asOf },
      filters: [],
    };
    const governed = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/export`,
      { method: "POST", data: { format: "csv", query } },
    );
    expect(governed.status).toBe(200);
    // The class the server decided, not one the caller asked for.
    expect(governed.headers["x-bi-export-class"]).toBe("summary");
    expect(governed.headers["content-disposition"]).toContain("attachment");
    expect(governed.headers["cache-control"]).toContain("no-store");

    const csv = String(governed.body);
    // The provenance block: who took it, from which institution, on which
    // catalogue, under which analytics build, with the book's own trust verdict
    // and the standing of the figures. None of these can be produced by an
    // unauthorized copy out of the browser, which is the whole point.
    expect(csv).toContain("Exported by,e2e.admin@aequoros.example");
    expect(csv).toContain("Institution,Sample Bank Ltd (BK-SAMP0001)");
    expect(csv).toContain("Disclosure class,Summary");
    expect(csv).toContain("Data scope,Whole institution");
    expect(csv).toContain(
      "Standing,Management information. Not a regulatory return and not a signed record of filing.",
    );
    expect(csv).toContain('Data confidence,"Does not reconcile');
    expect(csv).toMatch(/Analytics build,[0-9a-f]{64}/);
    // And the figures themselves, at full precision in the file.
    expect(csv).toContain(`standard,${FIXTURE_FIGURES.standardGradeRaw}`);
    expect(csv).toContain(`substandard,${FIXTURE_FIGURES.substandardGradeRaw}`);

    // The same request from a reader whose sentence does not cover the loan book
    // is refused outright. There is no partial file.
    const refused = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/export`,
      { method: "POST", data: { format: "csv", query } },
    );
    expect(refused.status).toBe(403);
    const refusedText = JSON.stringify(refused.body);
    expect(refusedText).toContain("bi_authorization_denied");
    expect(refusedText).not.toContain(FIXTURE_FIGURES.standardGradeRaw);
  });

  test("a figure carries the reader into the rows behind it, on the same slice", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    await page.goto("/explore");
    await chooseMeasure(page, "Gross loans");
    await chooseDimension(page, "Classification grade");

    const answer = page.locator("section.card").filter({
      has: page.getByRole("heading", { name: "Your question", level: 3 }),
    });
    await expect(
      answer.getByText(FIXTURE_FIGURES.standardGradeOnScreen, { exact: true }),
    ).toBeVisible();

    // REPORTED AS DEFECT T20-D2, and asserted as it behaves. `WidgetRenderer`
    // adds the `Rows behind` column only when it is given the query AS
    // SUBMITTED, and `app/(app)/explore/page.tsx` does not pass `query` to it —
    // `DashboardCanvas` does. So the only surface that renders a drill action is
    // a dashboard canvas, and no dashboard has widgets yet. The mapping itself
    // is real and is exercised below against the answer the server gave; when
    // Explore passes its query, this expectation flips to the link being present
    // and the navigation below becomes a click.
    await expect(
      answer.getByRole("columnheader", { name: "Rows behind" }),
    ).toHaveCount(0);

    // THE MAPPING, from the product's own module, over the server's own answer.
    // Nothing about the destination is hand-written here: `drillDestinations`
    // decides whether the row's slice can be reproduced exactly and builds the
    // URL, which is what makes the navigation below a test of the product.
    const asked = {
      measures: ["loans.balance_rc"],
      dimensions: ["loan.grade"],
      time: { asOf: new Date(`${asOf}T00:00:00.000Z`) },
      filters: [],
    } as unknown as BiQuery;
    const answered = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/query`,
      {
        method: "POST",
        data: {
          measures: ["loans.balance_rc"],
          dimensions: ["loan.grade"],
          time: { as_of: asOf },
          filters: [],
        },
      },
    );
    expect(answered.status).toBe(200);
    const wire = answered.body as {
      columns: { id: string; kind: string; member_id: string | null }[];
      rows: unknown[][];
    };
    // The raw route answers in wire case; the mapping reads the client's own
    // camelCase shape, so the one field it needs is renamed explicitly rather
    // than the whole payload being re-typed.
    const result = {
      columns: wire.columns.map((column) => ({
        ...column,
        memberId: column.member_id,
      })),
    } as unknown as BiQueryResult;

    const standardRow = wire.rows.find((row) => row[0] === "standard")!;
    const destinations = drillDestinations(result, standardRow, asked);
    expect(destinations).toHaveLength(1);
    expect(destinations[0].id).toBe("loan_book");
    expect(destinations[0].label).toBe("Open in Loan Book");
    // The slice travels in the URL: the row's own group AND the date the figure
    // was measured on. A link that dropped either would land on a wider book.
    expect(destinations[0].href).toBe(
      `/credit/book?grade=standard&as_of=${asOf}`,
    );

    // A row whose group is "not stated" carries no destination at all, because
    // no destination has an is-absent filter and carrying it would silently
    // widen the page to every group. The fixture has exactly such a row.
    const notStatedRow = wire.rows.find((row) => row[0] === null);
    expect(notStatedRow, "the fixture must have a not-stated group").toBeDefined();
    expect(drillDestinations(result, notStatedRow!, asked)).toHaveLength(0);

    await page.goto(destinations[0].href);
    await expect(page).toHaveURL(
      new RegExp(`/credit/book\\?grade=standard&as_of=${asOf}$`),
    );
    await expect(page.getByRole("heading", { name: "Loan Book" })).toBeVisible();

    // The destination is genuinely narrowed to the figure's own rows: the grade
    // control arrives set, and the book shows the loans behind GHS 81.8M.
    await expect(
      page.getByLabel("Filter by classification grade"),
    ).toHaveValue("standard");
    await expect(page.getByText("LOAN/1", { exact: true })).toBeVisible();
    // Two of the standard-graded loans are this borrower's, so the name is not
    // unique on the page.
    await expect(
      page.getByText("Volta Agro Ltd", { exact: true }).first(),
    ).toBeVisible();

    // The loan book's own server-side count proves the narrowing happened
    // there and not in the browser.
    const { status, body } = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/credit/loans?grade=standard&as_of=${asOf}&limit=50`,
    );
    expect(status).toBe(200);
    const loans = body as {
      total: number;
      filtered: number;
      rows: { grade: string }[];
    };
    expect(loans.filtered).toBeLessThan(loans.total);
    expect(loans.rows.every((row) => row.grade === "standard")).toBe(true);
  });
});
