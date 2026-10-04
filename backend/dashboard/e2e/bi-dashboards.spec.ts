/**
 * Dashboards — the seven certified content packs, and what a reader is told
 * about a widget they may not see.
 *
 * TWO HALVES, AND THE SPLIT IS THE PRODUCT'S, NOT THIS SPEC'S. The packs are
 * authored, validated and authorized server-side and served by
 * `GET …/bi/packs`; `components/bi/dashboards.ts` reads that route and holds no
 * pack of its own. So this spec asserts the pack engine against the WIRE and the
 * same packs against the SCREEN, and the two halves are deliberately not the same
 * assertions: the wire half proves the resolution and the refusal, the screen half
 * proves a reader can actually open one and that the sentence a refused pack shows
 * agrees with the tiles underneath it.
 *
 * THE REFUSED WIDGET IS THE SHARPEST ASSERTION IN THE BI SUITE. A refusal that
 * names what it hid is a disclosure: "you may not see Largest single-name share"
 * tells the reader the institution tracks one, and on a filtered view the filter
 * is usually the sensitive half. So a refused widget is served its GEOMETRY and
 * nothing else — no title, no caption, no kind, no measure, no dimension, no
 * figure — and this spec proves those strings are absent from the whole payload,
 * the way `RestrictedWidget`'s own unit test proves the component cannot render
 * them. Asserting only that a lock appeared would miss a leak in the same
 * response.
 */

import { expect, test } from "@playwright/test";
import path from "node:path";
import { E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import {
  biApi,
  CERTIFIED_PACKS,
  fixtureAsOf,
  SAMPLE_BANK_ID,
} from "./support/bi";

/**
 * The dashboard this suite builds, and the two views on it.
 *
 * One reads a LIQUIDITY figure and one reads a CREDIT figure, chosen so that the
 * same document resolves differently for two real identities: the Liquidity-only
 * reader is granted the first and refused the second, which is what makes the
 * refusal marker on a SHARED saved dashboard something the browser can be shown
 * rather than something asserted from code.
 */
const SAVED_TITLE = "Weekly funding review";
const GRANTED_VIEW = "Funding by account type";
const REFUSED_VIEW = "Loan book total";
/** The measure the refused view reads, which must never reach that reader. */
const REFUSED_MEMBER = "loans.balance_rc";

/**
 * Titles and captions the `alco` pack carries for the widgets a
 * Liquidity-only reader is refused. Every one must be absent from that reader's
 * response: each names a figure, and naming the figure is the disclosure.
 */
const WITHHELD_ALCO_STRINGS = [
  "Balance sheet mix",
  "Cost of funds",
  "Net open position",
  "Economic value and earnings",
  "Maturity ladder",
  "Repricing ladder",
  "Margin decomposition",
  "positions.balance_rc",
  "loans.weighted_average_rate",
  "position.maturity_bucket",
] as const;

/**
 * Titles, captions and members the `credit` pack carries for the widgets a
 * Liquidity-only reader is refused. Every one must be absent from that reader's
 * SCREEN, for the same reason it is absent from the payload.
 *
 * `Disbursements` is deliberately NOT in this list even though it is a refused
 * widget's whole title: the pack also carries `Disbursements against target`,
 * which this reader IS granted (it reads no figure — it names a dataset the bank
 * has not supplied), so a substring scan cannot tell the two apart. It is
 * asserted separately below as an EXACT text node, which can.
 */
const WITHHELD_CREDIT_STRINGS = [
  "Portfolio by product",
  "Portfolio at risk",
  "Recoveries and write-offs",
  "Balances by origination month",
  "Relationship officer league table",
  "Loan book by channel",
  "loans.balance_rc",
  "loans.npl_ratio_pct",
  "loan.grade",
] as const;

test.describe("certified dashboards", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("all seven packs resolve for the date, each certified", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    const { status, body } = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/packs?as_of=${asOf}`,
    );
    expect(status).toBe(200);
    const packs = (
      body as {
        as_of: string;
        packs: {
          id: string;
          title: string;
          version: string;
          access: string;
          message: string;
          readable_widgets: number;
          restricted_widgets: number;
          widgets: { id: string; access: string; title: string | null }[];
        }[];
      }
    ).packs;

    expect(packs.map((pack) => pack.id)).toEqual(
      CERTIFIED_PACKS.map((pack) => pack.id),
    );

    for (const expected of CERTIFIED_PACKS) {
      const pack = packs.find((entry) => entry.id === expected.id)!;
      expect(pack.title).toBe(expected.title);
      // A certified pack carries a version, because a reader citing a figure
      // from it has to be able to say which definition produced it.
      expect(pack.version).toMatch(/^\d+\.\d+\.\d+$/);
      // An organization-wide Analyst is refused nothing, so every widget on
      // every pack resolves and the pack says so in the product's own words.
      expect(pack.access).toBe("granted");
      expect(pack.message).toBe(
        "Your access covers every figure this dashboard reads.",
      );
      expect(pack.restricted_widgets).toBe(0);
      expect(pack.widgets.length).toBeGreaterThan(0);
      for (const widget of pack.widgets) {
        expect(widget.access).toBe("granted");
        expect(
          widget.title,
          `${pack.id}/${widget.id} must be named for its reader`,
        ).toBeTruthy();
      }
    }

    // A pack widget is a real question over the marts, and one of them must be
    // the balance-sheet mix the fixture book can actually answer.
    const alco = packs.find((pack) => pack.id === "alco")!;
    expect(alco.widgets.map((widget) => widget.id)).toContain(
      "balance_sheet_mix",
    );
    expect(alco.readable_widgets).toBeGreaterThan(0);
  });

  test("a widget this reader may not see is given its geometry and nothing else", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    const { status, body } = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/packs?as_of=${asOf}`,
    );
    expect(status).toBe(200);
    const packs = (
      body as {
        packs: {
          id: string;
          access: string;
          message: string;
          readable_widgets: number;
          restricted_widgets: number;
          widgets: {
            id: string;
            access: string;
            title: string | null;
            caption: string | null;
            kind: string | null;
            query: unknown | null;
            panel: string | null;
            display: unknown | null;
            layout: { i: string; w: number; h: number };
          }[];
        }[];
      }
    ).packs;

    const alco = packs.find((pack) => pack.id === "alco")!;
    // A dashboard a reader holds SOME of is still opened, and says which it is.
    expect(alco.access).toBe("granted");
    expect(alco.message).toBe(
      "Your access does not cover some of the figures this dashboard reads, so they are " +
        "not shown. An organization owner can grant them.",
    );
    expect(alco.restricted_widgets).toBeGreaterThan(0);

    const refused = alco.widgets.filter(
      (widget) => widget.access === "restricted",
    );
    expect(refused.length).toBeGreaterThan(0);
    for (const widget of refused) {
      // The geometry survives so the canvas keeps the layout the pack authored.
      expect(widget.layout.i).toBe(widget.id);
      expect(widget.layout.w).toBeGreaterThan(0);
      expect(widget.layout.h).toBeGreaterThan(0);
      // Everything that could name a figure is gone — dropped by not being
      // passed, not blanked out downstream.
      expect(widget.title, `${widget.id} must carry no title`).toBeNull();
      expect(widget.caption, `${widget.id} must carry no caption`).toBeNull();
      expect(widget.kind, `${widget.id} must carry no chart kind`).toBeNull();
      expect(widget.query, `${widget.id} must carry no query`).toBeNull();
      expect(widget.panel, `${widget.id} must carry no panel`).toBeNull();
      expect(
        widget.display,
        `${widget.id} must carry no display hints`,
      ).toBeNull();
    }

    // THE DISCLOSURE PROPERTY, over the whole payload rather than one widget:
    // nothing the reader was refused is named anywhere in the response.
    const payload = JSON.stringify(body);
    for (const withheld of WITHHELD_ALCO_STRINGS) {
      expect(
        payload.includes(withheld),
        `a Liquidity-only reader's pack payload must not name "${withheld}"`,
      ).toBe(false);
    }
    // The positive control: what they DO hold is served, so the assertion above
    // is not passing because the payload is empty.
    expect(payload).toContain("Deposit mix");

    // A pack whose every query was refused reads as refused, not as a dashboard
    // with nothing on it.
    const credit = packs.find((pack) => pack.id === "credit")!;
    expect(credit.access).toBe("restricted");
    expect(credit.message).toBe(
      "Your access does not cover any of the figures this dashboard reads, so none of " +
        "them are shown. An organization owner can grant them.",
    );
  });
});

test.describe("the Dashboards page", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("offers all seven certified packs, and one of them opens with its figures", async ({
    page,
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    await page.goto("/dashboards");
    await expect(
      page.getByRole("heading", { name: "Dashboards" }),
    ).toBeVisible();

    // Every pack the route serves is on screen, by the title the pack file
    // carries, each labelled with the standing that stands behind its figures.
    // Nothing here can be produced by a client-side list: `dashboards.ts` names
    // no pack (pinned by `components/bi/disclosure.test.ts`).
    for (const pack of CERTIFIED_PACKS) {
      const tile = page
        .locator("li")
        .filter({
          has: page.getByRole("heading", { name: pack.title, level: 3 }),
        })
        .first();
      await expect(tile, `${pack.id} must be offered`).toBeVisible();
      await expect(
        tile.getByText("Platform-certified", { exact: true }),
      ).toBeVisible();
      await expect(tile.getByRole("link")).toHaveAttribute(
        "href",
        `/dashboards/${pack.id}`,
      );
    }
    // And the honest empty state is NOT shown, because there is something to open.
    await expect(
      page.getByText("No dashboards yet", { exact: true }),
    ).toHaveCount(0);

    // OPEN ONE. The pack resolves for the date the reader is on, carries the
    // version a citation needs, and draws the figures the fixture book answers.
    await page
      .getByRole("link", { name: /Asset and liability committee/ })
      .first()
      .click();
    await expect(page).toHaveURL(/\/dashboards\/alco$/);
    await expect(
      page.getByRole("heading", { name: "Asset and liability committee" }),
    ).toBeVisible();
    await expect(page.getByText(/^Version \d+\.\d+\.\d+$/)).toBeVisible();
    await expect(page.locator('input[type="date"]').first()).toHaveValue(asOf);

    // Two widgets whose queries the marts can answer, by their authored titles.
    for (const widget of ["Balance sheet mix", "Deposit mix"]) {
      await expect(
        page.getByText(widget, { exact: true }).first(),
        `${widget} must be drawn for a reader refused nothing`,
      ).toBeVisible();
    }
    // A figure the platform has not published yet says so, and shows no number:
    // `loans_to_deposits` is a `pending_capability` tile on this pack.
    await expect(
      page.getByText("Loans to deposits", { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByText(/The platform does not publish this figure as a measure yet/)
        .first(),
    ).toBeVisible();

    // This reader is refused nothing, so the pack-level refusal notice is absent.
    await expect(page.getByText(/Your access does not cover/)).toHaveCount(0);
  });

  test("the governed export is the control on a figure's own frame", async ({
    page,
  }) => {
    await page.goto("/dashboards/alco");
    const mix = page
      .locator("section.card, div.card")
      .filter({ has: page.getByText("Deposit mix", { exact: true }) })
      .first();
    await expect(mix).toBeVisible();

    // A governed export is an export of ONE ANSWER, so the control belongs to
    // the widget and carries the widget's own question. Every artifact in it goes
    // through `POST …/bi/export`, which authorizes, audits and watermarks.
    await mix
      .getByRole("button", { name: /Export/ })
      .first()
      .click();
    const menu = page.getByRole("menu").first();
    // Matched on the START of each item's accessible name, because the name folds
    // in the description: a bare "PDF" would match the print item too, which is
    // exactly the ambiguity the governed item is named "Watermarked PDF" to avoid.
    for (const item of [
      /^Comma-separated values\b/,
      /^Excel workbook\b/,
      /^Watermarked PDF\b/,
      /^Print or save as PDF\b/,
    ]) {
      await expect(menu.getByRole("menuitem", { name: item })).toBeVisible();
    }
  });
});

test.describe("a pack whose every figure this reader is refused", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("says so, names none of them, and accounts for the tiles still on screen", async ({
    page,
  }) => {
    // `credit` is the sharpest case: every one of its seven query-bearing widgets
    // reads the loan book, so a Liquidity-only reader is refused all of them and
    // the server answers `access: "restricted"`. Three tiles remain — two
    // embedded platform surfaces and a dataset the bank has not supplied — so a
    // sentence that stopped at "none of them are shown" would contradict the
    // screen. Both sentences are asserted.
    await page.goto("/dashboards/credit");
    await expect(
      page.getByRole("heading", { name: "Credit and collections" }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Your access does not cover any of the figures this dashboard reads, so none of them are shown. An organization owner can grant them.",
      ),
    ).toBeVisible();
    await expect(
      page.getByText(
        "The views still shown read no figure of their own: they open another part of the platform, or name what is outstanding.",
      ),
    ).toBeVisible();

    // The refusals are drawn, one per refused widget, with the sentence that
    // names nothing.
    await expect(
      page.getByText("Access restricted", { exact: true }),
    ).toHaveCount(7);

    // And the tiles that are NOT refusals are all there, which is what the second
    // sentence accounts for. Both kinds appear on this one screen.
    //
    // An EMBEDDED SURFACE: named, with the door to the part of the platform it
    // shows. Nothing is fetched for it here, so nothing can be disclosed by it —
    // whoever follows the link is authorized when they arrive.
    await expect(
      page.getByText("Roll rates", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page
        .getByRole("link", { name: /Open delinquency and migration/ })
        .first(),
    ).toBeVisible();

    // A DATASET THE BANK HAS NOT SUPPLIED: the view is named in its author's
    // words, and so is the dataset that turns it on. Not a zero and not a blank.
    await expect(
      page.getByText("Disbursements against target", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Needs data: Performance targets", { exact: true }),
    ).toBeVisible();

    // The refused `Disbursements` widget's own title, as an exact text node, is
    // absent — which the substring scan below cannot decide because the granted
    // `Disbursements against target` contains it.
    await expect(
      page.getByText("Disbursements", { exact: true }),
      "the refused Disbursements widget must not be named",
    ).toHaveCount(0);

    // THE DISCLOSURE PROPERTY, on the rendered document rather than the payload:
    // not one of the refused widgets' titles, captions or members is anywhere on
    // screen or in the markup.
    const document = (await page.locator("body").innerText()).replace(
      /\s+/g,
      " ",
    );
    const markup = await page.content();
    for (const withheld of WITHHELD_CREDIT_STRINGS) {
      expect(
        document.includes(withheld),
        `a refused widget must not name "${withheld}" on screen`,
      ).toBe(false);
      expect(
        markup.includes(withheld),
        `a refused widget must not name "${withheld}" in the markup`,
      ).toBe(false);
    }
  });
});

/**
 * A DASHBOARD A READER SAVES FOR THEMSELVES, end to end and in a browser.
 *
 * Serial on purpose: these are the stages of ONE document's life — built, listed,
 * arranged into a second version, opened by a colleague with narrower access, and
 * deleted — and each stage is only meaningful against the state the previous one
 * left. The id is not known until the first save, so it is carried between them.
 *
 * Every assertion here is of something only a working feature produces: a figure
 * in the institution's own unit inside the tile, a second version in an
 * append-only history, a geometry change that survived a save, a refusal marker
 * for a reader who is refused, and a 404 after the delete. Navigating to a page
 * and finding an empty state would prove none of it.
 */
test.describe.serial("a dashboard the reader saves for themselves", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  let dashboardPath = "";

  test("is built from the catalogue and opens with its figures", async ({
    page,
  }) => {
    await page.goto("/dashboards/new");
    await expect(
      page.getByRole("heading", { name: "New dashboard" }),
    ).toBeVisible();

    await page.getByLabel("Name", { exact: true }).fill(SAVED_TITLE);
    await page
      .getByLabel("What it is for", { exact: true })
      .fill("Deposit mix and the loan book, together, for the Monday call.");

    // COMPOSE A VIEW. Everything offered is something this reader's access
    // already covers — the catalogue arrives filtered member by member through
    // the same decision the query path makes.
    await page.getByRole("button", { name: "Add a view" }).click();
    await page.getByLabel("Heading", { exact: true }).fill(GRANTED_VIEW);
    await page.getByRole("button", { name: "Table", exact: true }).click();
    await page.getByRole("checkbox", { name: "Deposits", exact: true }).check();
    await page
      .getByRole("checkbox", { name: "Deposit account type", exact: true })
      .check();
    await page.getByRole("button", { name: /Put it on the canvas/ }).click();

    // The tile is placed on the canvas, and the canvas is the grid library's.
    await expect(page.locator(".react-grid-layout")).toBeVisible();
    await expect(
      page.locator(".react-grid-item").filter({ hasText: GRANTED_VIEW }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Save dashboard" }).click();

    // The first save makes this reader the owner and lands on the document.
    await page.waitForURL(
      /\/dashboards\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    dashboardPath = new URL(page.url()).pathname;

    await expect(
      page.getByRole("heading", { name: SAVED_TITLE }),
    ).toBeVisible();
    // THE BADGE IS THE SERVER'S WORD. `bi_dashboards.badge` admits
    // `bank_certified` and nothing writes it — there is no maker-checker
    // promotion for a dashboard — so the honest badge for a document its owner
    // may still edit is Personal, and that is what the row says.
    await expect(
      page.getByText("Personal", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByText(/^Version 1$/).first()).toBeVisible();

    // A REAL ANSWER, in the institution's own unit. A refusal tile and a
    // needs-data tile both draw without one, so this is the assertion that
    // separates a working view from a placeholder.
    const tile = page
      .locator("section.card, div.card")
      .filter({ has: page.getByText(GRANTED_VIEW, { exact: true }) })
      .first();
    await expect(tile).toBeVisible();
    await expect(tile.getByText(/GHS\s?[\d.]/).first()).toBeVisible();
    await expect(tile.getByText("Access restricted")).toHaveCount(0);
    await expect(tile.getByText(/^Needs data: /)).toHaveCount(0);

    // The history exists from the first save and says what it is.
    await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
    await expect(page.getByText("Saved for the first time.")).toBeVisible();
    await expect(page.getByText("Shown now", { exact: true })).toBeVisible();
  });

  test("is on the Dashboards list beside the certified packs", async ({
    page,
  }) => {
    await page.goto("/dashboards");
    await expect(
      page.getByRole("heading", { name: "Saved at this institution" }),
    ).toBeVisible();

    const tile = page
      .locator("li")
      .filter({
        has: page.getByRole("heading", { name: SAVED_TITLE, level: 3 }),
      })
      .first();
    await expect(tile).toBeVisible();
    // Its own badge, not the platform's: the two lists are on one page and a
    // saved dashboard must never wear `Platform-certified`.
    await expect(tile.getByText("Personal", { exact: true })).toBeVisible();
    await expect(tile.getByText("Platform-certified")).toHaveCount(0);
    await expect(tile.getByText("Saved by you", { exact: true })).toBeVisible();
    await expect(tile.getByText("1 view", { exact: true })).toBeVisible();
    await expect(tile.getByRole("link")).toHaveAttribute("href", dashboardPath);

    // And a certified pack on the same page still carries the platform's word.
    const pack = page
      .locator("li")
      .filter({
        has: page.getByRole("heading", {
          name: "Asset and liability committee",
          level: 3,
        }),
      })
      .first();
    await expect(
      pack.getByText("Platform-certified", { exact: true }),
    ).toBeVisible();
  });

  test("is arranged, widened and shared, and the history keeps both versions", async ({
    page,
  }) => {
    await page.goto(`${dashboardPath}/edit`);
    await expect(
      page.getByRole("heading", { name: "Arrange this dashboard" }),
    ).toBeVisible();

    // THE GRID LIBRARY IS REALLY MOUNTED. Both class names are written onto the
    // DOM by react-grid-layout itself, so neither can be produced by this app's
    // own markup — the same contract `scripts/assert-home-route-bundle.mjs`
    // watches in the built output.
    await expect(page.locator(".react-grid-layout")).toBeVisible();
    await expect(page.locator(".react-resizable-handle").first()).toBeVisible();

    // RESIZE. The drag handle above is the mouse path; this is the same change
    // from a keyboard, which is the one a test can make deterministically and the
    // one some of this bank's people will use.
    const columns = page.getByLabel(`Width of ${GRANTED_VIEW} in columns`);
    await expect(columns).toHaveValue("6");
    await columns.fill("12");

    // ADD THE VIEW THAT WILL BE REFUSED TO A NARROWER READER.
    await page.getByRole("button", { name: "Add a view" }).click();
    await page.getByLabel("Heading", { exact: true }).fill(REFUSED_VIEW);
    await page
      .getByRole("button", { name: "Single figure", exact: true })
      .click();
    await page
      .getByRole("checkbox", { name: "Gross loans", exact: true })
      .check();
    await page.getByRole("button", { name: /Put it on the canvas/ }).click();

    // OPEN IT TO THE INSTITUTION, so a colleague can be shown what sharing does
    // and does not grant. Reachability, never authority.
    await page
      .getByRole("radio", {
        name: /Everyone with access to this institution/,
      })
      .check();

    await page
      .getByLabel("What changed", { exact: true })
      .fill("Added the loan book total and opened it to the institution.");
    await page.getByRole("button", { name: "Save new version" }).click();

    await page.waitForURL(new RegExp(`${dashboardPath}$`));
    await expect(page.getByText(/^Version 2$/).first()).toBeVisible();

    // BOTH VERSIONS ARE IN THE HISTORY, because it is append-only in the
    // database: a save extends it and can never rewrite it.
    await expect(page.getByText("Saved for the first time.")).toBeVisible();
    await expect(
      page.getByText(
        "Added the loan book total and opened it to the institution.",
      ),
    ).toBeVisible();

    // The second view draws its own figure.
    const total = page
      .locator("section.card, div.card")
      .filter({ has: page.getByText(REFUSED_VIEW, { exact: true }) })
      .first();
    await expect(total.getByText(/GHS\s?[\d.]/).first()).toBeVisible();

    // THE GEOMETRY SURVIVED THE SAVE. Reopening the builder reads the width back
    // out of the stored canvas, so this is the round trip and not the form's own
    // state.
    await page.goto(`${dashboardPath}/edit`);
    await expect(
      page.getByLabel(`Width of ${GRANTED_VIEW} in columns`),
    ).toHaveValue("12");
  });

  test("resolves for a colleague with narrower access as a refusal, naming nothing", async ({
    browser,
  }) => {
    // A SECOND REAL IDENTITY, not a mocked one: the Liquidity-only reader holds
    // an exact LIQUIDITY binding and nothing else, so the deposit view is theirs
    // and the loan view is not. The document is the same document.
    const context = await browser.newContext({
      storageState: path.join(E2E_TMP, "liquidity_viewer.json"),
      baseURL: E2E_BASE_URL,
    });
    const page = await context.newPage();
    try {
      await page.goto(dashboardPath);
      await expect(
        page.getByRole("heading", { name: SAVED_TITLE }),
      ).toBeVisible();

      // They are told whose document it is, and that they cannot change it.
      await expect(
        page.getByText(/is the only person who can change it\./),
      ).toBeVisible();
      // And they are not offered the controls only its owner may use.
      await expect(page.getByRole("link", { name: "Arrange" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Delete" })).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "Sharing" })).toHaveCount(
        0,
      );

      // THE VIEW THEY HOLD IS SERVED, which is the positive control: the absence
      // assertions below cannot pass on a blank page.
      const granted = page
        .locator("section.card, div.card")
        .filter({ has: page.getByText(GRANTED_VIEW, { exact: true }) })
        .first();
      await expect(granted.getByText(/GHS\s?[\d.]/).first()).toBeVisible();

      // THE VIEW THEY DO NOT HOLD IS A REFUSAL CARRYING NOTHING. The server's own
      // sentence is shown, and the tile names no figure.
      await expect(
        page.getByText(
          "Your access does not cover some of the figures this dashboard reads, so they are not shown. An organization owner can grant them.",
        ),
      ).toBeVisible();
      await expect(
        page.getByText("Access restricted", { exact: true }).first(),
      ).toBeVisible();

      // THE DISCLOSURE PROPERTY, on the rendered document and on the markup: the
      // refused view's heading and the measure behind it are nowhere — not in a
      // title attribute, not in an aria-label, not in a hidden node.
      const document = (await page.locator("body").innerText()).replace(
        /\s+/g,
        " ",
      );
      const markup = await page.content();
      for (const withheld of [REFUSED_VIEW, REFUSED_MEMBER, "Gross loans"]) {
        expect(
          document.includes(withheld),
          `a refused view must not name "${withheld}" on screen`,
        ).toBe(false);
        expect(
          markup.includes(withheld),
          `a refused view must not name "${withheld}" in the markup`,
        ).toBe(false);
      }
    } finally {
      await context.close();
    }
  });

  test("says what sharing does and does not grant, and names a colleague", async ({
    page,
  }) => {
    await page.goto(dashboardPath);

    // THE HONEST NOTE, in production copy, on the panel that does the sharing.
    await expect(
      page.getByText(
        "Sharing a dashboard does not share its figures. Everyone you name is authorized view by view when they open it, against their own access — so a colleague sees the views their access already covers, and in the place of the others that a view is restricted and nothing about what it holds.",
      ),
    ).toBeVisible();
    // And what the current setting actually means, rather than a raw enum.
    await expect(
      page.getByText(
        /Anyone at your organization whose access covers this institution can open this dashboard\./,
      ),
    ).toBeVisible();

    // NAMING PEOPLE IS NOT OFFERED WHILE THAT IS NOT HOW IT IS SHARED. The server
    // refuses the list outright for any other reachability rule, so a control here
    // would be a button that answers 403. The reason is on screen instead.
    await expect(
      page.getByText(/This dashboard is not shared by name/),
    ).toBeVisible();
    await expect(page.getByLabel("Name someone else")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Add to the list" }),
    ).toHaveCount(0);

    // SWITCH TO NAMING PEOPLE, which is a save of the canvas like any other.
    await page.goto(`${dashboardPath}/edit`);
    await page.getByRole("radio", { name: /People I name/ }).check();
    await page.getByRole("button", { name: "Save new version" }).click();
    await page.waitForURL(new RegExp(`${dashboardPath}$`));

    await expect(
      page.getByText("Only the people named below can open this dashboard."),
    ).toBeVisible();
    await expect(page.getByText("Nobody is named yet.")).toBeVisible();

    await page
      .getByLabel("Name someone else")
      .selectOption({ label: "E2E Analyst (e2e.analyst@aequoros.example)" });
    await page.getByRole("button", { name: "Add to the list" }).click();

    await expect(page.getByText("1 person is named")).toBeVisible();
    await expect(
      page.getByText("e2e.analyst@aequoros.example", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Nobody is named yet.")).toHaveCount(0);

    // And taking the name off is the same save, so revocation is ordinary.
    await page.getByRole("button", { name: "Take off the list" }).click();
    await expect(page.getByText("Nobody is named yet.")).toBeVisible();
  });

  test("is deleted by its owner, and the history goes with it", async ({
    page,
  }) => {
    await page.goto(dashboardPath);
    await expect(
      page.getByRole("heading", { name: "Delete this dashboard" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page
      .getByRole("button", { name: "Delete it and its history" })
      .click();

    // THE OWNER IS TAKEN BACK TO THE LIST, NOT TO A NOT-FOUND PAGE. The deleted
    // document answers 404 on the next read — correctly — and the invalidated read
    // races the navigation, so before `SavedDashboardScreen` named the act this
    // journey landed on "This page could not be found" and never left the
    // document's own address. Both halves are asserted: the navigation happened,
    // and the not-found page is not what happened.
    await page.waitForURL(/\/dashboards$/);
    await expect(
      page.getByRole("heading", { name: "This page could not be found." }),
    ).toHaveCount(0);
    await expect(
      page.locator("li").filter({
        has: page.getByRole("heading", { name: SAVED_TITLE, level: 3 }),
      }),
    ).toHaveCount(0);

    // And the document itself is gone, not merely off the list.
    await page.goto(dashboardPath);
    await expect(page.getByText(/not be found|404/i).first()).toBeVisible();
  });
});

/**
 * COPYING A CERTIFIED PACK, and the one thing this surface will not pretend to do.
 *
 * A certified pack is never edited in place — it has a version behind it and a
 * badge that would then cover two different things — so the affordance is a copy,
 * and the copy is made SERVER-SIDE from the pack file: this client never sends a
 * canvas, so it cannot pass off an edited one as a copy of certified content.
 *
 * The copy is then NOT rearrangeable here, and that refusal is the point of the
 * second test. `alco` authors three views over relative periods (cost of funds and
 * margin decomposition over a trailing year), and the read route serves every
 * window already resolved into dates — from which the relative form cannot be
 * recovered, because on 31 January month-to-date, quarter-to-date and year-to-date
 * all resolve to a window starting 1 January. Guessing would turn a year's figure
 * into a month's under the heading the pack's author wrote, so the builder refuses
 * and says so.
 */
test.describe.serial("a certified pack copied into the reader's own", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  let copyPath = "";

  test("becomes a Personal dashboard the reader owns, with the pack's own views", async ({
    page,
  }) => {
    await page.goto("/dashboards/alco");
    await expect(
      page.getByRole("heading", { name: "Asset and liability committee" }),
    ).toBeVisible();
    await expect(
      page.getByText("Platform-certified", { exact: true }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Make my own copy" }).click();
    await page.waitForURL(
      /\/dashboards\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    copyPath = new URL(page.url()).pathname;

    await expect(
      page.getByRole("heading", {
        name: "Copy of Asset and liability committee",
      }),
    ).toBeVisible();
    // The copy is the copier's own working document, so it is Personal — never
    // the badge the original wears.
    await expect(
      page.getByText("Personal", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByText("Platform-certified")).toHaveCount(0);

    // The pack's own views came across, drawing the pack's own figures.
    for (const view of ["Balance sheet mix", "Deposit mix"]) {
      await expect(
        page.getByText(view, { exact: true }).first(),
        `${view} must be on the copy`,
      ).toBeVisible();
    }
    // And it is the copier's, so they are offered the owner's controls.
    await expect(page.getByRole("link", { name: "Arrange" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sharing" })).toBeVisible();
  });

  test("is not rearranged here, and the refusal says why", async ({ page }) => {
    await page.goto(`${copyPath}/edit`);
    // `EmptyState` states its title as a paragraph, not a heading, so the refusal
    // is read as text.
    await expect(
      page.getByText("This dashboard cannot be rearranged here", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /holds a view that reads a period rather than a single reporting date/,
      ),
    ).toBeVisible();
    // Nothing was changed by looking: the document is still there and still
    // version 1.
    await page.goto(copyPath);
    await expect(page.getByText(/^Version 1$/).first()).toBeVisible();
  });

  test("is deleted, leaving the list as it was", async ({ page }) => {
    await page.goto(copyPath);
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page
      .getByRole("button", { name: "Delete it and its history" })
      .click();
    await page.waitForURL(/\/dashboards$/);
    await expect(
      page.getByText("Copy of Asset and liability committee"),
    ).toHaveCount(0);
  });
});

/**
 * THE EDIT ROUTE REFUSES A CERTIFIED PACK KEY, decided before any read.
 *
 * Whether a view may be edited is a property of its STANDING, not of its
 * contents: a pack is certified content and is never edited in place, so the
 * route answers not-found rather than opening an editor with nowhere to save to.
 */
test.describe("the edit route", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("is not found for a certified pack", async ({ page }) => {
    await page.goto("/dashboards/alco/edit");
    // The not-found page a reader sees, not the HTTP status: the route streams
    // its shell before the refusal is decided, so the response is already 200 by
    // the time `notFound()` is reached. What matters is that there is no editor
    // and no saved document behind this address.
    await expect(
      page.getByRole("heading", { name: "This page could not be found." }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Arrange this dashboard" }),
    ).toHaveCount(0);
  });
});
