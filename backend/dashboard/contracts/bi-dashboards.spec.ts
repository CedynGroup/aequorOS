/** HTTP certified packs and withheld-widget payload disclosure. */
import { expect, test } from "@playwright/test";
import {
  biApi,
  CERTIFIED_PACKS,
  fixtureAsOf,
  SAMPLE_BANK_ID,
} from "../e2e/support/bi";

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

test.describe("bi-dashboards HTTP contracts", () => {
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
