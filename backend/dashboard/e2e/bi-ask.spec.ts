/**
 * Asking a question in words — the confirmation, and what it refuses.
 *
 * WHY THIS FILE HAS TWO HALVES, AND WHY THE SECOND ONE INTERCEPTS.
 *
 * The WIRE half runs against the real stack, as a real identity, and asserts the
 * refusal a person actually reaches here. That is not a shortcut: this stack runs
 * no `ai`-lane worker, holds no vendor credential and has no approved model
 * configuration, so `POST …/bi/ask` genuinely cannot produce a proposal — it
 * refuses at the deployment gate, in the platform's own sentence. The property
 * worth pinning is exactly that: the reader is told something they can act on,
 * and no reason code reaches the screen.
 *
 * The SCREEN half drives the confirmation, which needs a `proposed` state that no
 * hermetic stack can produce without a model. So the two ask routes are
 * intercepted with bodies shaped exactly as `app/schemas/bi_nlq.py` declares —
 * the same technique `bi-authorization.spec.ts` uses on `/feature-flags`, and for
 * the same reason: the state under test is a deployment fact, not a data fact.
 * What is NOT faked is the thing that matters most, and it cannot be: the browser
 * makes a real `POST …/run`, and this spec reads its body back out of the request
 * and asserts the query is BYTE-IDENTICAL to the proposal it was shown, including
 * a field the generated client does not know about. That is the one assertion a
 * unit test cannot make, because the hazard lives in the browser's serialisation.
 *
 * WHAT HAS NO JOURNEY HERE, SO NOBODY MISTAKES THE ABSENCE FOR COVERAGE. The
 * AI-gate refusal — the sentence an admin gets on a platform with AI switched off —
 * cannot be reached in this stack today: `app/jobs/bi_nlq.py::request_translation`
 * meters the AI quota BEFORE it honours the gate decision, and the quota query
 * touches `ai_commentary_drafts`, which `Base.metadata.create_all` does not build
 * because `app/models/bi_commentary.py` is not imported by `app/models/__init__.py`.
 * So the route answers 500 rather than its refusal. Diagnosis, traceback and the
 * two-line fix are in `.ai/bi_recon/p5g_ask_surface_report.md`; the refusal itself
 * is covered by `backend/tests/api/test_bi_ask_routes.py`, where the table exists.
 * Add the journey when the ordering is fixed.
 *
 * `BI_NLQ_ENABLED` is set in `playwright.config.ts` beside `BI_ENABLED`, for the
 * same reason and with the same care: with it off the ask routes answer 409 and
 * the shell HIDES the tab, so these journeys would navigate to a walled-up door
 * and pass on an empty state. Setting it sends nothing to any vendor — the AI
 * gates are independent and all shut.
 */

import { expect, test, type Page, type Route } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "./support/bi";

const ASK_TAB = "Ask a question";
const QUESTION = "gross loans by branch, largest first";

/**
 * A field of the proposed query that the generated `BiQuery` serializer does not
 * know about. It stands for every field added to `BiQuery` since the client was
 * last generated: `BiQueryToJSON` hand-enumerates its keys with no spread, so a
 * surface that re-serialised the proposal would drop this one, the server's digest
 * comparison would fail, and the reader would meet a 409 about a question they
 * did confirm. `lib/api/ask.test.ts` proves the generated serializer would drop
 * it; this file proves the browser does not.
 */
const FUTURE_FIELD = "segment_scope";

const PROPOSED_QUERY: Record<string, unknown> = {
  measures: ["loans.balance_rc"],
  dimensions: ["loans.branch"],
  filters: [],
  time: { as_of: "2026-08-31" },
  sort: [{ member: "loans.balance_rc", direction: "desc" }],
  top_n: { dimension: "loans.branch", n: 10, other: true },
  [FUTURE_FIELD]: { kind: "branch", values: ["B001", "B002"] },
};

const QUESTION_ID = "6f2a1c90-1d3e-4a5b-8c6d-9e0f1a2b3c4d";

function askBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    question_id: QUESTION_ID,
    state: "translating",
    message:
      "Working out what you asked for. This usually takes a few seconds.",
    question: QUESTION,
    as_of: "2026-08-31",
    catalogue_version: "e2e-catalogue",
    figures_offered: 40,
    query: null,
    reading: null,
    suggestions: [],
    ...over,
  };
}

const PROPOSED = askBody({
  state: "proposed",
  message: "Check this is the question you meant, then run it.",
  query: PROPOSED_QUERY,
  reading: {
    sentence:
      "Gross loans, grouped by Branch, as at 2026-08-31, showing only the 10 largest Branch groups by Gross loans",
    clauses: [
      { kind: "figure", text: "Gross loans" },
      { kind: "grouping", text: "grouped by Branch" },
      { kind: "period", text: "as at 2026-08-31" },
      {
        kind: "ranking",
        text: "showing only the 10 largest Branch groups by Gross loans, with the rest grouped together",
      },
    ],
    as_of: "2026-08-31",
    range_start: null,
    range_end: null,
    compare_to: null,
    member_ids: ["loans.balance_rc", "loans.branch"],
  },
});

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * Stand in for the two ask routes: the POST answers `translating`, the GET
 * answers `proposed`, and the confirmation's POST is handed to `onRun` so the
 * body the BROWSER built can be inspected.
 */
async function interceptAsk(
  page: Page,
  onRun: (body: unknown) => Promise<unknown>,
): Promise<void> {
  await page.route(
    `**/api/v1/banks/${SAMPLE_BANK_ID}/bi/ask**`,
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.endsWith("/run")) {
        await json(route, 200, await onRun(request.postDataJSON()));
        return;
      }
      if (request.method() === "POST") {
        await json(route, 202, askBody());
        return;
      }
      await json(route, 200, PROPOSED);
    },
  );
}

// --- the wire half ----------------------------------------------------------

test.describe("the ask routes", () => {
  test("refuse a reader whose access covers no figure, naming none", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    // A baseline member holds no BI-source grant, so there is no question they
    // could ask — and the refusal is decided BEFORE anything leaves the platform.
    const asked = await biApi(
      request,
      "viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/ask`,
      { method: "POST", data: { question: QUESTION, as_of: asOf } },
    );
    expect(asked.status).toBe(403);
    const detail =
      (asked.body as { error?: { details?: Record<string, unknown> } }).error
        ?.details ?? {};
    expect(detail.error_code).toBe("bi_ask_no_figures_available");
    // Production copy: a sentence naming the action an owner would take, and no
    // figure, id or count that would tell this reader what exists.
    expect(String(detail.message)).toMatch(/Org Owner/);
    expect(String(detail.message)).toMatch(/[.!]$/);
    expect(Object.keys(detail)).toEqual(["error_code", "message"]);
  });

  test("do not serve one reader's question to another", async ({ request }) => {
    // A question is translated over the members THAT reader may see, so serving
    // it to a colleague would disclose exactly what their grants withhold.
    const read = await biApi(
      request,
      "viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/ask/${QUESTION_ID}`,
    );
    expect(read.status).toBe(404);
  });
});

// --- the screen half --------------------------------------------------------

test.describe("a reader asking in words", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("is offered the door, and told plainly what happens to their words", async ({
    page,
  }) => {
    // The real projection: `GET /api/v1/feature-flags` serves `bi_nlq_enabled`
    // from `BI_NLQ_ENABLED`, which `playwright.config.ts` switches on.
    await page.goto("/explore");
    const tabs = page.getByLabel("Module sections");
    await expect(tabs.getByRole("link", { name: ASK_TAB })).toBeVisible();
    await tabs.getByRole("link", { name: ASK_TAB }).click();
    await expect(
      page.getByRole("heading", { name: "Ask a question" }),
    ).toBeVisible();
    // The reader is told, before typing, that the words leave the platform and
    // that their figures do not.
    await expect(
      page.getByText(/Your words are sent to the assistant/i),
    ).toBeVisible();
    await expect(
      page.getByText(/do not type a customer's name/i),
    ).toBeVisible();
    // Asking is not running, and the control says so.
    await expect(
      page.getByRole("button", { name: /Work out my question/i }),
    ).toBeVisible();
  });

  test("is shown the platform's refusal, never a reason code", async ({
    page,
  }) => {
    // THE REFUSAL IS THE SERVER'S SENTENCE, rendered verbatim. It is delivered by
    // interception because no refusal is reachable end to end in this stack: the
    // only pre-quota refusal (`bi_ask_no_figures_available`) belongs to a reader
    // with no institution, whose screen never offers the box at all — asserted on
    // its own terms in the next journey — and every other refusal is behind the
    // 500 named in the file docstring. The BODY here is the route's own, copied
    // from `app/features/ask_bi.py::_unavailable`.
    await page.route(
      `**/api/v1/banks/${SAMPLE_BANK_ID}/bi/ask`,
      async (route) => {
        await json(route, 409, {
          error: {
            code: "conflict",
            message: "Conflict",
            request_id: "e2e",
            details: {
              error_code: "bi_ask_unavailable",
              message:
                "Asking questions in words is not switched on for this platform.",
              reason: "nlq_disabled",
            },
          },
        });
      },
    );

    await page.goto("/explore/ask");
    await page.getByRole("textbox").first().fill(QUESTION);
    await page.getByRole("button", { name: /Work out my question/i }).click();

    const refusal = page.getByRole("status");
    await expect(refusal).toBeVisible();
    await expect(refusal).toHaveText(
      "Asking questions in words is not switched on for this platform.",
    );

    // Not a code, not an enum, not a status token, anywhere on the page — and the
    // machine `reason` that rode in the same body is nowhere either.
    const document = (await page.locator("body").innerText()).toLowerCase();
    for (const token of [
      "bi_ask_unavailable",
      "bi_ask_no_figures_available",
      "deployment_disabled",
      "nlq_disabled",
      "configuration_not_approved",
      "unrecognised_member",
      "translating",
    ]) {
      expect(document.includes(token), `"${token}" reached the reader`).toBe(
        false,
      );
    }
  });

  test("confirms the question they were shown, byte for byte", async ({
    page,
  }) => {
    let runBody: unknown = null;
    await interceptAsk(page, async (body) => {
      runBody = body;
      return {
        columns: [
          {
            id: "loans.branch",
            kind: "dimension",
            label: "Branch",
            format: "text",
            member_id: "loans.branch",
          },
          {
            id: "loans.balance_rc",
            kind: "measure",
            label: "Gross loans",
            format: "amount",
            member_id: "loans.balance_rc",
          },
        ],
        rows: [
          ["Adum", 4210000],
          ["Asokwa", 2880000],
        ],
        truncated: false,
        elapsed_ms: 12,
        used_aggregate: false,
        catalogue_version: "e2e-catalogue",
      };
    });

    await page.goto("/explore/ask");
    await page.getByRole("textbox").first().fill(QUESTION);
    await page.getByRole("button", { name: /Work out my question/i }).click();

    // THE CONFIRMATION. Every clause is on screen, in the catalogue's own words,
    // under a heading a person reads — and nothing has run yet.
    await expect(
      page.getByRole("heading", {
        name: /Check this is the question you meant/i,
      }),
    ).toBeVisible();
    for (const heading of [
      "Figure",
      "Broken down by",
      "Reporting period",
      "Ranking",
    ]) {
      await expect(page.getByText(heading, { exact: true })).toBeVisible();
    }
    await expect(page.getByText("Gross loans", { exact: true })).toBeVisible();
    await expect(page.getByText("grouped by Branch")).toBeVisible();
    // The reader's own words are quoted back so they can tell what was read.
    // Scoped to the confirmation card: the textarea still holds the same string,
    // and a page-wide match would resolve to both.
    await expect(
      page.getByText(new RegExp(`You asked: .${QUESTION}`)),
    ).toBeVisible();
    // No JSON, and no field editor: run it or discard it. Asserted on the
    // confirmation card rather than the document, because "Calculated measures" is
    // a sibling tab and a whole-page word match would convict it.
    await expect(page.getByRole("button", { name: /Discard/i })).toBeVisible();
    const card = (
      await page
        .locator("section", { hasText: "Check this is the question you meant" })
        .first()
        .innerText()
    ).toLowerCase();
    for (const shape of ['"measures"', "measures:", "loans.", "{", "}"]) {
      expect(
        card.includes(shape),
        `the card shows the raw query (${shape})`,
      ).toBe(false);
    }
    expect(runBody).toBeNull();

    await page.getByRole("button", { name: /Yes, run this question/i }).click();
    await expect(
      page.getByRole("heading", { name: /The question you confirmed/i }),
    ).toBeVisible();

    // THE ECHO-BACK, out of the real request the browser made.
    expect(runBody).not.toBeNull();
    const sent = (runBody as { query?: unknown }).query;
    expect(sent).toEqual(PROPOSED_QUERY);
    // Named explicitly, because this is the field a re-serialisation would lose
    // and the reason the whole proposal is held opaque.
    expect((sent as Record<string, unknown>)[FUTURE_FIELD]).toEqual(
      PROPOSED_QUERY[FUTURE_FIELD],
    );
    expect(JSON.stringify(sent)).toBe(JSON.stringify(PROPOSED_QUERY));

    // The answer is the reader's.
    await expect(page.getByText("Adum")).toBeVisible();
  });

  test("is told what is absent when the answer has nothing in it", async ({
    page,
  }) => {
    await interceptAsk(page, async () => ({
      columns: [
        {
          id: "loans.branch",
          kind: "dimension",
          label: "Branch",
          format: "text",
          member_id: "loans.branch",
        },
        {
          id: "loans.balance_rc",
          kind: "measure",
          label: "Gross loans",
          format: "amount",
          member_id: "loans.balance_rc",
        },
      ],
      rows: [],
      truncated: false,
      elapsed_ms: 8,
      used_aggregate: false,
      catalogue_version: "e2e-catalogue",
    }));

    await page.goto("/explore/ask");
    await page.getByRole("textbox").first().fill(QUESTION);
    await page.getByRole("button", { name: /Work out my question/i }).click();
    await page.getByRole("button", { name: /Yes, run this question/i }).click();

    // MISSING IS NOT ZERO: the absence is stated, it names the figure and the
    // date, and there is no table, no total and no chart.
    await expect(page.getByText(/There is nothing to show/i)).toBeVisible();
    await expect(page.getByText(/This is not a zero/i)).toBeVisible();
    await expect(page.getByText(/Gross loans/)).toBeVisible();
    await expect(page.locator("table")).toHaveCount(0);
    await expect(page.locator("canvas")).toHaveCount(0);
  });
});

test.describe("a deployment that has not answered about questions in words", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("hides the tab without refusing the route", async ({ page }) => {
    // A backend that predates the flag answers `/feature-flags` without
    // `bi_nlq_enabled`, so the flag reads as "not yet known". Both halves of the
    // asymmetry are asserted, and they point opposite ways on purpose — the nav
    // hides on anything but a definite yes so no link flashes, and the route
    // guard refuses only on a definite no so a deep-link refresh never 404s an
    // unresolved flag.
    await page.route("**/api/v1/feature-flags", async (route) => {
      await json(route, 200, {
        bi_enabled: true,
        bi_mart_enqueue_enabled: false,
        bi_scheduler_enabled: false,
      });
    });

    await page.goto("/explore");
    const tabs = page.getByLabel("Module sections");
    await expect(tabs.getByRole("link", { name: "Explore" })).toBeVisible();
    await expect(tabs.getByRole("link", { name: ASK_TAB })).toHaveCount(0);

    await page.goto("/explore/ask");
    await expect(
      page.getByRole("heading", { name: "Ask a question" }),
    ).toBeVisible();
  });
});

test.describe("a reader whose access covers no institution", () => {
  test.use({ storageState: path.join(E2E_TMP, "viewer.json") });

  test("is never handed a question box", async ({ page }) => {
    // A baseline member holds no BI-source grant, so there is no institution whose
    // figures they could ask about. The route guard answers not-found — the same
    // treatment `/explore/measures` gives them, and deliberately not a hub redirect:
    // nobody is ever SENT to the ask surface, which is what makes `/explore/alerts`
    // and `/explore/subscriptions` public and this one not.
    //
    // The page's "No institution to ask about" panel is the defensive fallback
    // BEHIND this guard, for the narrow case of a reader the nav admits whose
    // institution list is still empty. It exists so that case is a sentence rather
    // than a question box that silently refuses to accept a question, which is what
    // a bare `disabled` prop produced.
    await page.goto("/explore/ask");
    await expect(
      page.getByRole("button", { name: /Work out my question/i }),
    ).toHaveCount(0);
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("link", { name: ASK_TAB })).toHaveCount(0);
  });
});

// --- the flag is the whole gate ---------------------------------------------

test.describe("a deployment that does not serve questions in words", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("offers no tab, and refuses the deep link, without asking for a grant", async ({
    page,
  }) => {
    // BI on, natural language off — which is the shipped configuration today.
    await page.route("**/api/v1/feature-flags", async (route) => {
      await json(route, 200, {
        bi_enabled: true,
        bi_mart_enqueue_enabled: false,
        bi_scheduler_enabled: false,
        bi_nlq_enabled: false,
      });
    });

    await page.goto("/explore");
    // The rest of Explore is untouched: this flag closes one door, not the module.
    // Scoped to the module tab strip, because "Explore" is also a sidebar link.
    const tabs = page.getByLabel("Module sections");
    await expect(tabs.getByRole("link", { name: "Explore" })).toBeVisible();
    await expect(tabs.getByRole("link", { name: ASK_TAB })).toHaveCount(0);

    await page.goto("/explore/ask");
    // Hidden, not disabled-with-a-sentence: a deployment flag is not a grant, so
    // there is no sentence a reader could act on.
    await expect(
      page.getByRole("heading", { name: "Ask a question" }),
    ).toHaveCount(0);
    const document = (await page.locator("body").innerText()).toLowerCase();
    expect(document.includes("bi_nlq_enabled")).toBe(false);
  });
});
