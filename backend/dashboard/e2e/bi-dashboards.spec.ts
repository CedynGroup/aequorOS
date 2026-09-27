/**
 * Dashboards — the seven certified content packs, the trust badge they carry,
 * and what a reader is told about a widget they may not see.
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
import { E2E_TMP } from "../playwright.config";
import {
  biApi,
  CERTIFIED_PACKS,
  EXPECTED_OVERALL_BADGE,
  EXPECTED_OVERALL_STATUS,
  fixtureAsOf,
  SAMPLE_BANK_ID,
} from "./support/bi";

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
  "loans.balance_rc",
  "loans.npl_ratio_pct",
  "loan.grade",
] as const;

test.describe("certified dashboards", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("all seven packs resolve for the date, each certified and carrying its own trust verdict", async ({
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

  test("the trust badge a pack carries is the book's own verdict, with its failing checks named by id only", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);

    // A widget's answer carries the SAME verdict the Insights page shows, so a
    // figure cannot be read off a dashboard without its reservation.
    const answer = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/query`,
      {
        method: "POST",
        data: {
          measures: ["positions.balance_rc"],
          dimensions: ["position.type"],
          time: { as_of: asOf },
          filters: [],
        },
      },
    );
    expect(answer.status).toBe(200);
    const result = answer.body as {
      rows: unknown[][];
      trust: { status: string; failing_checks: string[] };
    };
    expect(result.trust.status).toBe(EXPECTED_OVERALL_STATUS);
    // Check IDS, never the figures behind them: the badge's hover detail is
    // "R2, R3" and not "loans differ by 1.3bn".
    expect(result.trust.failing_checks).toContain("R2");
    expect(result.trust.failing_checks).toContain("R3");
    for (const id of result.trust.failing_checks) {
      expect(id).toMatch(/^R\d+$/);
    }
    // And it is a real answer, not an empty one wearing a badge.
    expect(result.rows.length).toBeGreaterThan(0);
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
      expect(widget.display, `${widget.id} must carry no display hints`).toBeNull();
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
        .filter({ has: page.getByRole("heading", { name: pack.title, level: 2 }) })
        .first();
      await expect(tile, `${pack.id} must be offered`).toBeVisible();
      await expect(
        tile.getByText("Platform-certified", { exact: true }),
      ).toBeVisible();
      // The per-tile badge is the institution's own reconciliation verdict for
      // the date — the same verdict every figure on the pack will carry.
      await expect(
        tile.getByText(EXPECTED_OVERALL_BADGE, { exact: true }),
      ).toBeVisible();
      await expect(tile.getByRole("link")).toHaveAttribute(
        "href",
        `/dashboards/${pack.id}`,
      );
    }
    // And the honest empty state is NOT shown, because there is something to open.
    await expect(page.getByText("No dashboards yet", { exact: true })).toHaveCount(
      0,
    );

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
    await expect(page.getByText("Loans to deposits", { exact: true })).toBeVisible();
    await expect(
      page.getByText(
        /The platform does not publish this figure as a measure yet/,
      ).first(),
    ).toBeVisible();

    // This reader is refused nothing, so the pack-level refusal notice is absent.
    await expect(
      page.getByText(/Your access does not cover/),
    ).toHaveCount(0);
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
    await mix.getByRole("button", { name: /Export/ }).first().click();
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
    // `credit` is the sharpest case: every one of its five query-bearing widgets
    // reads the loan book, so a Liquidity-only reader is refused all of them and
    // the server answers `access: "restricted"`. Four tiles remain — two embedded
    // platform surfaces, a dataset the bank has not supplied and a breakdown the
    // marts cannot yet make — so a sentence that stopped at "none of them are
    // shown" would contradict the screen. Both sentences are asserted.
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

    // The refusals are drawn, with the sentence that names nothing.
    const locked = page.getByText("Access restricted", { exact: true });
    expect(await locked.count()).toBeGreaterThan(0);

    // And the tiles that are NOT refusals are all there, which is what the second
    // sentence accounts for. All three kinds appear on this one screen.
    //
    // An EMBEDDED SURFACE: named, with the door to the part of the platform it
    // shows. Nothing is fetched for it here, so nothing can be disclosed by it —
    // whoever follows the link is authorized when they arrive.
    await expect(
      page.getByText("Roll rates", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Open delinquency and migration/ }).first(),
    ).toBeVisible();

    // A DATASET THE BANK HAS NOT SUPPLIED: the view is named in its author's
    // words, and so is the dataset that turns it on. Not a zero and not a blank.
    await expect(
      page.getByText("Disbursements against target", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Needs data: Performance targets", { exact: true }),
    ).toBeVisible();

    // PLATFORM WORK OUTSTANDING, stated as platform work and never as "needs
    // data": this bank pushes its loan book every night, and telling it to supply
    // something it already supplies would be a false statement about its own book.
    await expect(
      page.getByText("Relationship officer league table", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /The analytics tables do not carry the field this view groups by yet/,
      ),
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
