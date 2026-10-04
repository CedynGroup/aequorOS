/**
 * Reverse-stress frontier: no figure may be drawn over other text.
 *
 * The breach-marker value ("0.74×") used to float above the severity track in
 * the row-title band, centred on the marker: a breach at or below the 1× origin
 * printed it over the axis title, and one near k_max over the "Breaks at …"
 * caption and half past the card edge. This journey stands in for the
 * latest-frontier read with severities spanning the whole search range and
 * asserts, at common viewport widths, that no two text runs on the frontier or
 * its headline cards intersect and that none spills past its card. It also
 * pins the page's one-decimal percentage precision: the headline captions once
 * echoed the API's six-decimal strings ("98.303920%").
 *
 * Storage-free: the frontier is intercepted, so no reverse-stress run is minted.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID } from "./support/bi";

type Severities = {
  label: string;
  liquidity: string | null;
  capital: string | null;
};

/** `null` is an axis whose floor holds across the whole search range. */
const CASES: Severities[] = [
  { label: "as reported", liquidity: "0.74", capital: "0.94" },
  { label: "near the 1x origin", liquidity: "1.00", capital: "1.05" },
  { label: "near the 3x end", liquidity: "2.95", capital: "3.00" },
  { label: "the search extremes", liquidity: "0.05", capital: "5.00" },
  { label: "one axis holds", liquidity: null, capital: "1.85" },
];

const WIDTHS = [390, 768, 1024, 1280, 1440, 1920];

function liquidityAxis(k: string | null) {
  return k === null
    ? { breached: false, lcr_min_pct: "100.000000", k_max: "5" }
    : {
        breached: true,
        breach_multiplier: k,
        lcr_at_breach_pct: "98.303920",
        lcr_min_pct: "100.000000",
      };
}

function capitalAxis(k: string | null) {
  return k === null
    ? { breached: false, cet1_min_pct: "6.5", k_max: "5" }
    : {
        breached: true,
        breach_multiplier: k,
        worst_cet1_at_breach_pct: "6.351883",
        cet1_min_pct: "6.5",
      };
}

async function serveFrontier(page: Page, severities: Severities) {
  await page.route(
    `**/api/v1/banks/${SAMPLE_BANK_ID}/reverse-stress/latest**`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: "00000000-0000-4000-8000-000000000001",
          bank_id: SAMPLE_BANK_ID,
          reporting_period_id: "00000000-0000-4000-8000-000000000002",
          engine_version: "e2e",
          input_hash: "e2e",
          created_at: "2026-09-30T00:00:00Z",
          narrative: "Intercepted frontier for layout verification.",
          liquidity_axis: liquidityAxis(severities.liquidity),
          capital_axis: capitalAxis(severities.capital),
        }),
      }),
  );
}

type TextCollision = { text: string; other: string };
type TextSpill = { text: string };

/**
 * Every text run under the given roots, as tight per-line boxes (a Range over
 * the text node, not its element, whose box spans the whole flex cell), then
 * the pairs of runs whose boxes intersect and the runs that leave their card.
 */
async function textLayoutDefects(page: Page, roots: string[]) {
  return page.evaluate((selectors) => {
    type Run = { text: string; card: DOMRect | null; boxes: DOMRect[] };
    const runs: Run[] = [];
    for (const selector of selectors) {
      for (const root of document.querySelectorAll(selector)) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const text = node.textContent?.trim() ?? "";
          if (!text) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          const boxes = [...range.getClientRects()].filter(
            (box) => box.width > 0 && box.height > 0,
          );
          if (boxes.length === 0) continue;
          const card = node.parentElement?.closest(".card");
          runs.push({
            text,
            card: card?.getBoundingClientRect() ?? null,
            boxes,
          });
        }
      }
    }
    // Sub-pixel slack: adjacent lines of one paragraph legitimately touch.
    const slack = 0.5;
    const meet = (a: DOMRect, b: DOMRect) =>
      a.left < b.right - slack &&
      b.left < a.right - slack &&
      a.top < b.bottom - slack &&
      b.top < a.bottom - slack;
    const collisions: { text: string; other: string }[] = [];
    for (let i = 0; i < runs.length; i += 1) {
      for (let j = i + 1; j < runs.length; j += 1) {
        if (runs[i].boxes.some((a) => runs[j].boxes.some((b) => meet(a, b)))) {
          collisions.push({ text: runs[i].text, other: runs[j].text });
        }
      }
    }
    const spills = runs
      .filter(
        ({ card, boxes }) =>
          card &&
          boxes.some(
            (box) =>
              box.left < card.left - slack ||
              box.right > card.right + slack ||
              box.top < card.top - slack ||
              box.bottom > card.bottom + slack,
          ),
      )
      .map(({ text }) => ({ text }));
    return { collisions, spills };
  }, roots) as Promise<{ collisions: TextCollision[]; spills: TextSpill[] }>;
}

test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

for (const severities of CASES) {
  test(`frontier figures clear every other label — ${severities.label}`, async ({
    page,
  }) => {
    await serveFrontier(page, severities);
    await page.goto("/forecasting/reverse-stress");
    const frontier = page.getByTestId("reverse-stress-frontier");
    await expect(frontier).toBeVisible();

    await expect(
      frontier.getByTestId("reverse-stress-breach-value"),
    ).toHaveText(
      [severities.liquidity, severities.capital]
        .filter((k): k is string => k !== null)
        .map((k) => `${Number(k).toFixed(2)}×`),
    );
    await expect(
      page.getByTestId("reverse-stress-headlines"),
    ).not.toContainText(/\d\.\d{2,}%/);

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(
          () =>
            textLayoutDefects(page, [
              '[data-testid="reverse-stress-headlines"]',
              '[data-testid="reverse-stress-frontier"]',
            ]),
          { message: `text layout at ${width}px` },
        )
        .toEqual({ collisions: [], spills: [] });
    }
  });
}
