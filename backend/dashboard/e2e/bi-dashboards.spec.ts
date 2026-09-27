/**
 * Dashboards — the seven certified content packs, the trust badge they carry,
 * and what a reader is told about a widget they may not see.
 *
 * TWO HALVES, AND THE SPLIT IS THE PRODUCT'S, NOT THIS SPEC'S. The packs are
 * authored, validated and authorized server-side and served by
 * `GET …/bi/packs`; `components/bi/dashboards.ts` has not bound that route yet
 * and says so in its own header, so `/dashboards` today shows its honest empty
 * state and every dashboard URL is not found. This spec therefore asserts the
 * pack engine against the wire and the page against the screen, and the page
 * half is a TRIPWIRE: when the pack route binds, `no dashboards yet` stops being
 * true and the failure here is the reminder to replace it with the pack tiles,
 * their certification labels and their per-tile badges. That is the intended
 * behaviour of this test, not a defect in it.
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

  // TRIPWIRE, by design. `components/bi/dashboards.ts` returns an empty list
  // because the pack route above is not bound to the page yet. When agent F
  // binds it, this test fails — and the fix is to assert the seven tiles, their
  // `Platform-certified` labels and their per-tile trust badges, not to relax
  // this. Keeping the current state pinned is what stops the empty state from
  // being mistaken for "packs work in the browser".
  test("says there is nothing to open, rather than showing tiles that resolve to nothing", async ({
    page,
  }) => {
    await page.goto("/dashboards");
    await expect(
      page.getByRole("heading", { name: "Dashboards" }),
    ).toBeVisible();

    await expect(
      page.getByText("No dashboards yet", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /Nothing has been published to this institution and you have not saved a view of your own\./,
      ),
    ).toBeVisible();

    // The empty state is not a dead end: it names where a view starts.
    await page.getByRole("link", { name: /Open Explore/ }).click();
    await expect(page).toHaveURL(/\/explore$/);

    // And a pack id the API serves is NOT openable through the page, which is
    // the unbound half stated as a fact rather than left ambiguous.
    //
    // The refusal is decided by `notFound()` inside a client component, after
    // the shell has been flushed, so the DOCUMENT is a 200 carrying the
    // not-found boundary — measured, not assumed. The product-visible fact is
    // the copy and the absence of the pack, so those are what is asserted.
    await page.goto("/dashboards/alco");
    await expect(
      page.getByText(/404|not found|could not be found/i).first(),
    ).toBeVisible();
    await expect(
      page.getByText("Asset and liability committee"),
    ).toHaveCount(0);
  });
});
