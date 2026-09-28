/**
 * Calculated measures — a bank defining a figure of its own, and two people
 * standing behind it.
 *
 * WHY THE WIRE HALF EXISTS BESIDE THE SCREEN HALF. The language is the server's
 * (`app/domain/bi/expr.py`) and the browser holds no parser, so the sharpest
 * assertions about a FORMULA are assertions about what the validation route said.
 * The screen half then proves a person can actually get from an empty textarea to
 * a certified figure in Explore's picker.
 *
 * THE SCREEN HALF IS THE SECOND HALF OF THIS FILE, and it was added by track P4-I.
 * Until then this file was wire-only while its own docstring claimed a screen half
 * — which is the exact shape AGENTS.md names: "the endpoint exists" is not "the
 * feature works", and a green suite is evidence about the code that was written.
 * The browser journeys drive the five things only a person can do — write a
 * formula, have the SERVER check it, save a draft, send it for certification, and
 * certify it or send it back — plus the two refusals that are visible only on
 * screen: the save that will not fire until the server has passed the exact text,
 * and the delete control a certified measure deliberately does not offer.
 *
 * TWO FIXTURE IDENTITIES ARE A MAKER AND A CHECKER, and that is not arranged
 * here — it is what `scripts/e2e_bootstrap.py` already grants. `admin` holds
 * organization-wide ANALYST (`VIEW, CREATE, EDIT, RUN, VALIDATE, EXPORT` — no
 * APPROVE) and `approver` holds organization-wide APPROVER (`VIEW, REVIEW,
 * APPROVE`). So the proposer genuinely cannot certify their own formula and the
 * checker genuinely can, against real bindings rather than a flag.
 */

import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, SAMPLE_BANK_ID } from "./support/bi";

/**
 * The figures the formula reads, and their catalogue labels.
 *
 * Both are CREDIT, both are a position on a date, and both carry the position
 * dimensions — so they share a time behaviour and a breakdown, which is what
 * makes a formula over them answerable. Picked from
 * `app/domain/bi/catalogue/measures.py`, not invented.
 */
const NUMERATOR = "loans.npl_exposure_rc";
const NUMERATOR_LABEL = "Non-performing exposure";
const DENOMINATOR = "loans.balance_rc";
const DENOMINATOR_LABEL = "Gross loans";

/**
 * `* 100` so the answer is a real percentage and can be declared one. A ratio
 * declared as a percentage would render 0.05 as "0.05%", which is the kind of
 * dishonest fixture that makes a green journey worthless.
 */
const FORMULA = `SAFE_DIV([m:${NUMERATOR}], [m:${DENOMINATOR}]) * 100`;

const MEASURE_KEY = "e2e_npl_share_pct";
const MEASURE_LABEL = "Non-performing share of the loan book";

function digestOf(source: string): string {
  return createHash("sha256").update(Buffer.from(source, "utf8")).digest("hex");
}

/**
 * THE VALIDATION ROUTE IS THE ONLY AUTHORITY ON THE LANGUAGE, and this is what
 * the editor renders. Three answers, each of which the surface shows differently:
 * accepted (with the figures it reads), refused by the parser (with a character
 * offset), and refused because the caller may not read a figure it names.
 */
test.describe("the formula language", () => {
  test("says what a good formula reads, and where a bad one went wrong", async ({
    request,
  }) => {
    const good = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/validation`,
      { method: "POST", data: { expression: FORMULA } },
    );
    expect(good.status).toBe(200);
    const accepted = good.body as {
      valid: boolean;
      message: string;
      position: number | null;
      referenced_members: string[];
      referenced_member_labels: string[];
    };
    expect(accepted.valid).toBe(true);
    // Production copy, rendered as the server worded it.
    expect(accepted.message).toBe("This formula is valid.");
    expect(accepted.position).toBeNull();
    // THE FIGURES COME FROM THE SERVER'S OWN PARSE, in the order written — which
    // is what the editor shows back as "It reads …".
    expect(accepted.referenced_members).toEqual([NUMERATOR, DENOMINATOR]);
    expect(accepted.referenced_member_labels).toEqual([
      NUMERATOR_LABEL,
      DENOMINATOR_LABEL,
    ]);

    // A formula the parser refuses carries a 1-based character offset and no
    // echo of the caller's text. The editor's "Show me character N" selects it.
    const bad = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/validation`,
      { method: "POST", data: { expression: `SAFE_DIV([m:${DENOMINATOR}],` } },
    );
    expect(bad.status).toBe(200);
    const refused = bad.body as {
      valid: boolean;
      message: string;
      position: number | null;
      referenced_members: string[];
    };
    expect(refused.valid).toBe(false);
    expect(refused.message.length).toBeGreaterThan(0);
    expect(refused.position).not.toBeNull();
    expect(refused.position).toBeGreaterThan(0);
    // A refusal names no figure it could not establish.
    expect(refused.referenced_members).toEqual([]);
  });

  test("refuses a formula naming a figure this reader cannot read, and names it", async ({
    request,
  }) => {
    // The Liquidity-only fixture holds an exact LIQUIDITY binding, so a CREDIT
    // figure is genuinely outside its access — a real refusal, not a contrived one.
    const response = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/validation`,
      { method: "POST", data: { expression: FORMULA } },
    );
    expect(response.status).toBe(200);
    const body = response.body as {
      valid: boolean;
      message: string;
      denied_members: string[];
    };
    expect(body.valid).toBe(false);
    // THE FIGURE IS THE SUBJECT OF THE REFUSAL, never the formula. The ids are
    // the author's own text — they typed `[m:…]` — which is why naming them back
    // is not a disclosure.
    expect(body.denied_members.length).toBeGreaterThan(0);
    expect(body.denied_members).toContain(DENOMINATOR);
    expect(body.message).toMatch(/does not cover/i);
  });
});

/**
 * THE PROMOTION IS TWO IDENTITIES, ON THE WIRE.
 *
 * Serial because these are the stages of ONE formula's life and each is only
 * meaningful against the state the last one left. The id is not known until the
 * create, so it is carried between them, and the last stage removes the fixture
 * so the list is left as it was found.
 */
test.describe.serial("certifying a formula takes two people", () => {
  let measureId = "";

  test("its author writes it, and it is a draft that only they can see", async ({
    request,
  }) => {
    const created = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures`,
      {
        method: "POST",
        data: {
          measure_key: MEASURE_KEY,
          label: MEASURE_LABEL,
          description: "What share of the loan book is non-performing.",
          expression: FORMULA,
          value_type: "pct",
          favourable_direction: "lower_better",
        },
      },
    );
    expect(created.status).toBe(201);
    const measure = created.body as {
      id: string;
      state: string;
      badge: string;
      expression: string;
      referenced_members: string[];
      owned_by_caller: boolean;
      awaiting_caller_decision: boolean;
    };
    measureId = measure.id;
    expect(measure.state).toBe("personal");
    expect(measure.badge).toBe("personal");
    expect(measure.owned_by_caller).toBe(true);
    // THE FIGURES ARE THE SERVER'S PARSE OF THE TEXT, not anything the request
    // said about what the text means — the request carried no member list.
    expect(measure.referenced_members).toEqual([NUMERATOR, DENOMINATOR]);
    // Nobody is being asked to decide anything yet.
    expect(measure.awaiting_caller_decision).toBe(false);

    // A DRAFT IS ITS AUTHOR'S ALONE. The checker cannot even see it, so there is
    // nothing for them to certify by mistake.
    const asChecker = await biApi(
      request,
      "approver",
      `/banks/${SAMPLE_BANK_ID}/bi/measures`,
    );
    expect(asChecker.status).toBe(200);
    const theirs = asChecker.body as { measures: { id: string }[] };
    expect(theirs.measures.map((row) => row.id)).not.toContain(measureId);
  });

  test("its author cannot certify it, and the refusal is the platform's own SoD verdict", async ({
    request,
  }) => {
    const proposed = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}/proposal`,
      { method: "POST", data: { reason: "The board pack needs this figure." } },
    );
    expect(proposed.status).toBe(200);
    const maker = proposed.body as {
      state: string;
      badge: string;
      proposal_reason: string;
      awaiting_caller_decision: boolean;
    };
    expect(maker.state).toBe("proposed");
    // A PROPOSAL IS NOT A CERTIFICATION. The badge stays personal, so no surface
    // can draw it as the institution's while it is only one person's.
    expect(maker.badge).toBe("personal");
    expect(maker.proposal_reason).toBe("The board pack needs this figure.");
    // And the proposer is not the one being asked — which is the flag the UI
    // gates its decision control on, and the reason it shows them a sentence
    // instead of a button.
    expect(maker.awaiting_caller_decision).toBe(false);

    const selfApproved = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}/decision`,
      {
        method: "POST",
        data: {
          decision: "approve",
          reason: "Trying to certify my own formula.",
          expression_digest: digestOf(FORMULA),
        },
      },
    );
    expect(selfApproved.status).toBe(409);
    // The backend's own error envelope: `{error: {code, message, details}}`.
    const blocked = (
      selfApproved.body as {
        error: {
          details: {
            error_code: string;
            sod_decision: { outcome: string; findings: { code: string }[] };
          };
        };
      }
    ).error.details;
    expect(blocked.error_code).toBe("bi_measure_promotion_refused");
    expect(blocked.sod_decision.outcome).toBe("block");
    expect(
      blocked.sod_decision.findings.map((finding) => finding.code),
    ).toContain("maker_checker_runtime_condition_required");
  });

  test("a decision taken against a version that has moved is refused", async ({
    request,
  }) => {
    const stale = await biApi(
      request,
      "approver",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}/decision`,
      {
        method: "POST",
        data: {
          decision: "approve",
          reason: "Read a different version of this formula.",
          // A well-formed digest of text that is not what is stored.
          expression_digest: digestOf(`${FORMULA} + 0`),
        },
      },
    );
    expect(stale.status).toBe(409);
    const body = (
      stale.body as { error: { details: { error_code: string } } }
    ).error.details;
    expect(body.error_code).toBe("bi_measure_expression_moved");
  });

  test("a second person certifies the exact text, and it becomes the institution's", async ({
    request,
  }) => {
    // The checker can see the proposal — they have to be able to read what they
    // are being asked to certify — and the server tells them it is theirs to
    // decide.
    const queue = await biApi(
      request,
      "approver",
      `/banks/${SAMPLE_BANK_ID}/bi/measures`,
    );
    expect(queue.status).toBe(200);
    const waiting = (
      queue.body as {
        measures: {
          id: string;
          awaiting_caller_decision: boolean;
          owned_by_caller: boolean;
        }[];
      }
    ).measures.find((row) => row.id === measureId);
    expect(waiting).toBeTruthy();
    expect(waiting!.awaiting_caller_decision).toBe(true);
    expect(waiting!.owned_by_caller).toBe(false);

    const decided = await biApi(
      request,
      "approver",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}/decision`,
      {
        method: "POST",
        data: {
          decision: "approve",
          reason: "Checked against the classification engine's own exposure.",
          expression_digest: digestOf(FORMULA),
        },
      },
    );
    expect(decided.status).toBe(200);
    const body = decided.body as {
      measure: {
        state: string;
        badge: string;
        expression: string;
        approved_expression: string;
        approval_reason: string;
        approved_by_display_name: string | null;
        awaiting_caller_decision: boolean;
      };
      sod_decision: { outcome: string };
    };
    expect(body.sod_decision.outcome).toBe("allow");
    expect(body.measure.state).toBe("bank_certified");
    expect(body.measure.badge).toBe("bank_certified");
    // THE APPROVAL IS FROZEN AT A FORMULA. `approved_expression` is the text that
    // was certified, and it is what the surface shows as the record.
    expect(body.measure.approved_expression).toBe(FORMULA);
    expect(body.measure.approved_expression).toBe(body.measure.expression);
    expect(body.measure.approval_reason).toBe(
      "Checked against the classification engine's own exposure.",
    );
    expect(body.measure.approved_by_display_name).toBeTruthy();
    // Nothing is waiting on anybody now.
    expect(body.measure.awaiting_caller_decision).toBe(false);
  });

  test("a reader refused its figures is not told the formula exists", async ({
    request,
  }) => {
    // A CERTIFIED MEASURE IS THE INSTITUTION'S VOCABULARY, but only to whoever may
    // read the figures it names. A measure a reader cannot compute is ABSENT from
    // their list rather than shown as restricted — its label is authored text that
    // can describe the very figure they were refused.
    const list = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/measures`,
    );
    expect(list.status).toBe(200);
    const body = list.body as { measures: { id: string }[] };
    expect(body.measures.map((row) => row.id)).not.toContain(measureId);
    // And the whole payload names neither the measure nor the figure behind it.
    const payload = JSON.stringify(body);
    expect(payload).not.toContain(MEASURE_LABEL);
    expect(payload).not.toContain(MEASURE_KEY);
    expect(payload).not.toContain(DENOMINATOR);

    // Asked for by id it answers 404 — identical to one that does not exist, so a
    // private formula cannot be found by guessing at identifiers.
    const byId = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}`,
    );
    expect(byId.status).toBe(404);
  });

  test("the certified formula answers a question, and the fixture is left as it was", async ({
    request,
  }) => {
    // THE POINT OF CERTIFYING ONE: it can now be named in a query like any other
    // figure, and the server resolves it into the figures its text names.
    const answered = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/query`,
      {
        method: "POST",
        data: {
          measures: [MEASURE_KEY],
          dimensions: [],
          filters: [],
          time: { as_of: await asOf(request) },
        },
      },
    );
    expect(answered.status).toBe(200);
    const result = answered.body as {
      columns: { member_id: string | null; label: string }[];
      rows: unknown[][];
    };
    expect(result.columns.map((column) => column.member_id)).toContain(
      MEASURE_KEY,
    );
    expect(result.rows.length).toBeGreaterThan(0);

    // Cleanup. NOTE: the UI deliberately does NOT offer this for a certified
    // measure — certifying took two identities and deleting takes one, and the
    // approved text is the record of what they agreed (audit A9-07). The route
    // permits it, which is why this fixture can be removed at all.
    const removed = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}`,
      { method: "DELETE" },
    );
    expect(removed.status).toBe(204);
  });
});

/** The reporting date the fixture's marts were built at, read from the platform. */
async function asOf(
  request: Parameters<typeof biApi>[0],
): Promise<string> {
  const { status, body } = await biApi(
    request,
    "admin",
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
  );
  expect(status).toBe(200);
  const periods = (body as { periods: { period_end: string }[] }).periods;
  expect(periods.length).toBeGreaterThan(0);
  return periods[0].period_end.slice(0, 10);
}

// ---------------------------------------------------------------------------
// The screen half: what only a person can do
// ---------------------------------------------------------------------------

/**
 * Two formulas of their own, so nothing here collides with the wire fixture above
 * and the two outcomes a review has — certified, and sent back — are each shown on
 * a measure that really reached that state.
 */
const UI_KEY = "e2e_ui_npl_share_pct";
const UI_LABEL = "Non-performing share, defined on screen";
const SENT_BACK_KEY = "e2e_ui_sent_back_pct";
const SENT_BACK_LABEL = "A formula the reviewer sends back";

/** A formula the parser refuses: an unclosed call. */
const BROKEN_FORMULA = `SAFE_DIV([m:${DENOMINATOR}],`;

/**
 * Open the composer on `/explore/measures`.
 *
 * Every field below is addressed with `getByLabel(..., { exact: true })`. Without
 * the exactness `getByLabel("Id")` also matches the shell's own `aria-label`s —
 * "Liquidity" and "Collapse sidebar" both contain it — and Playwright's strict
 * mode fails on three elements. Measured, not guessed: that is how the first run
 * of this block failed.
 */
async function openComposer(page: Page): Promise<void> {
  await page.goto("/explore/measures");
  await expect(
    page.getByRole("heading", { name: "Calculated measures", level: 1 }),
  ).toBeVisible();
  await page.getByRole("button", { name: "New measure", exact: true }).click();
  await expect(page.getByLabel("The formula", { exact: true })).toBeVisible();
}

/**
 * The card for one measure in the list, found by its own heading.
 *
 * By the HEADING and not by page text: a card's title is a `<span>` carrying the
 * name, its standing pill and possibly "Written by …", so the enclosing element's
 * text is never exactly the name and an `exact` text match would find nothing —
 * which would make every `toHaveCount(0)` below pass vacuously. `getByRole` matches
 * the accessible name by substring, and this same locator is asserted PRESENT in
 * one journey and ABSENT in another, so neither reading can be vacuous.
 */
function measureCard(page: Page, label: string) {
  return page
    .locator("section.card")
    .filter({ has: measureHeading(page, label) })
    .first();
}

function measureHeading(page: Page, label: string) {
  return page.getByRole("heading", { level: 3, name: label });
}

/**
 * Write one formula through the composer and save it as a draft.
 *
 * Deliberately not a shortcut past the check: every caller of this helper needs a
 * measure that exists, and the only way this surface will make one is by passing
 * the server's verdict first. That is the property, so it is in the helper.
 */
async function writeDraft(
  page: Page,
  key: string,
  label: string,
): Promise<void> {
  await openComposer(page);
  await page.getByLabel("Name", { exact: true }).fill(label);
  await page.getByLabel("Id", { exact: true }).fill(key);
  await page
    .getByLabel("What it is for", { exact: true })
    .fill("What share of the loan book is non-performing.");
  await page.getByLabel("The formula", { exact: true }).fill(FORMULA);
  await page
    .getByRole("button", { name: "Check this formula", exact: true })
    .click();
  await expect(page.getByText("This formula is valid.")).toBeVisible();
  await page
    .getByLabel("What the answer is", { exact: true })
    .selectOption({ label: "A percentage" });
  await page
    .getByLabel("Which way is good", { exact: true })
    .selectOption({ label: "Lower is better" });
  await page
    .getByRole("button", { name: "Save as my draft", exact: true })
    .click();
  await expect(measureCard(page, label)).toBeVisible();
}

test.describe.serial("writing and certifying a formula on screen", () => {
  /**
   * THE AUTHOR'S HALF. `admin` holds organization-wide Analyst — `VIEW, CREATE,
   * EDIT, RUN, VALIDATE, EXPORT` and deliberately no `APPROVE` — so everything
   * below is done by an identity that genuinely cannot certify its own work.
   */
  test.describe("as the author", () => {
    test.use({ storageState: path.join(E2E_TMP, "admin.json") });

    test("the save will not fire until the SERVER has passed the exact text", async ({
      page,
    }) => {
      await openComposer(page);
      await page.getByLabel("Name", { exact: true }).fill(UI_LABEL);
      await page.getByLabel("Id", { exact: true }).fill(UI_KEY);

      // THE SENTENCE THAT SAYS THERE IS NO PARSER HERE. It is on screen before the
      // first keystroke in the formula field, not in a toast after a refusal.
      const save = page.getByRole("button", {
        name: "Save as my draft",
        exact: true,
      });
      await expect(save).toBeDisabled();

      // A FORMULA THE SERVER REFUSES. The wording and the character offset are
      // both the server's; the browser renders them and offers to put the cursor
      // there. A client-side parser would have had its own opinion by now.
      await page
        .getByLabel("The formula", { exact: true })
        .fill(BROKEN_FORMULA);
      await expect(save).toBeDisabled();
      await expect(
        page.getByText(
          "Check the formula before saving — the server decides whether it is one, and this page will not guess.",
        ),
      ).toBeVisible();
      await page
        .getByRole("button", { name: "Check this formula", exact: true })
        .click();
      const offset = page.getByRole("button", {
        name: /^Show me character \d+$/,
      });
      await expect(offset).toBeVisible();
      await expect(save).toBeDisabled();

      // EDITING AFTER A CHECK MAKES THE VERDICT STALE, and the surface says so
      // rather than carrying a verdict about text nobody is looking at.
      await page.getByLabel("The formula", { exact: true }).fill(FORMULA);
      await expect(
        page.getByText("The formula has changed since it was last checked."),
      ).toBeVisible();
      await expect(save).toBeDisabled();

      // Checked again, and now the server's own words — including the figures it
      // says the formula reads, which is its parse and not the request's claim.
      await page
        .getByRole("button", { name: "Check this formula", exact: true })
        .click();
      await expect(page.getByText("This formula is valid.")).toBeVisible();
      await expect(
        page.getByText(`It reads ${NUMERATOR_LABEL}, ${DENOMINATOR_LABEL}.`),
      ).toBeVisible();
      await expect(save).toBeEnabled();
    });

    test("a saved formula is a draft, is its author's to delete, and is nobody else's", async ({
      page,
    }) => {
      // The previous journey deliberately did not save, so there is nothing yet —
      // this is where the draft is actually created, through the same gate.
      await writeDraft(page, UI_KEY, UI_LABEL);

      const card = measureCard(page, UI_LABEL);
      await expect(card.getByText("Draft", { exact: true })).toBeVisible();
      await expect(
        card.getByText(FORMULA, { exact: true }).first(),
      ).toBeVisible();
      await expect(
        card.getByText(`Reads ${NUMERATOR_LABEL}, ${DENOMINATOR_LABEL}.`),
      ).toBeVisible();
      // A DRAFT IS DELETABLE BY ITS AUTHOR, and the control is armed rather than
      // immediate — the destructive act asks twice.
      await expect(card.getByRole("button", { name: "Delete" })).toBeVisible();
      await expect(card.getByRole("button", { name: "Change" })).toBeVisible();
      // And it is NOT offered for certification by anyone but its author, which is
      // asserted from the other side in the checker's journeys below.
      await expect(
        card.getByRole("heading", { name: "Send this for certification" }),
      ).toBeVisible();
    });

    test("deleting a draft asks twice, and both answers are honoured", async ({
      page,
    }) => {
      // A SECOND draft, so the deletion journey destroys something the rest of the
      // file does not need. It is written through the same composer gate, which is
      // the only way this surface makes one.
      await writeDraft(page, SENT_BACK_KEY, SENT_BACK_LABEL);
      const card = measureCard(page, SENT_BACK_LABEL);

      // Armed rather than immediate, and the safe answer is offered beside the
      // destructive one. Answering "Keep it" leaves the draft alone.
      await card.getByRole("button", { name: "Delete", exact: true }).click();
      await expect(
        card.getByRole("button", { name: "Delete it" }),
      ).toBeVisible();
      await card.getByRole("button", { name: "Keep it" }).click();
      await expect(card.getByRole("button", { name: "Delete it" })).toHaveCount(
        0,
      );
      await expect(measureHeading(page, SENT_BACK_LABEL)).toBeVisible();

      // And answering "Delete it" removes it — from the list, not merely from the
      // screen, which the reload proves.
      await card.getByRole("button", { name: "Delete", exact: true }).click();
      await card.getByRole("button", { name: "Delete it" }).click();
      await expect(measureHeading(page, SENT_BACK_LABEL)).toHaveCount(0);
      await page.reload();
      await expect(measureHeading(page, SENT_BACK_LABEL)).toHaveCount(0);
    });

    test("sending it for certification needs a reason, and then it is out of the author's hands", async ({
      page,
    }) => {
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      const send = card.getByRole("button", {
        name: "Send for certification",
        exact: true,
      });
      // A REASON IS REQUIRED, and the control says so by not being available.
      await expect(send).toBeDisabled();
      await card
        .getByLabel("Why it should be certified", { exact: true })
        .fill(
          "The board pack needs this figure stated once, the same way every month.",
        );
      await expect(send).toBeEnabled();
      await send.click();

      // The standing changes, and the badge does NOT: a proposal is not a
      // certification, so no surface may draw it as the institution's yet.
      await expect(
        card.getByText("Waiting for a review", { exact: true }),
      ).toBeVisible();
      // THE PROPOSER IS TOLD WHY THE DECISION IS NOT THEIRS, rather than shown a
      // control that has quietly disappeared.
      await expect(
        card.getByText(
          "You proposed this formula, so you cannot certify it. Someone whose access covers approving the figures it names has to review it.",
        ),
      ).toBeVisible();
      await expect(
        card.getByRole("button", { name: "Certify for the institution" }),
      ).toHaveCount(0);
      // And deletion is withheld while somebody is being asked to read it.
      await expect(card.getByRole("button", { name: "Delete" })).toHaveCount(0);
      await expect(
        card.getByText(/Withdraw it from review first/),
      ).toBeVisible();
    });
  });

  /**
   * THE CHECKER'S HALF. `approver` holds organization-wide Approver — `VIEW,
   * REVIEW, APPROVE` — so this is a real second identity with real approval
   * authority over the figures the formula names, not a flag.
   */
  test.describe("as the reviewer", () => {
    test.use({ storageState: path.join(E2E_TMP, "approver.json") });

    test("the reviewer is shown the exact text, and a draft they were never asked about is invisible", async ({
      page,
    }) => {
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      await expect(
        card.getByRole("heading", { name: "Review this formula" }),
      ).toBeVisible();
      await expect(
        card.getByText(FORMULA, { exact: true }).first(),
      ).toBeVisible();
      await expect(card.getByText(/^Proposed by /)).toBeVisible();

      // A DRAFT IS ITS AUTHOR'S ALONE — and a deleted one is nobody's. Neither
      // name has ever been on this reader's page, and there is nothing here for
      // them to certify by mistake.
      await expect(measureHeading(page, SENT_BACK_LABEL)).toHaveCount(0);
    });

    test("a decision needs a stated conclusion, and sending it back is one of the two", async ({
      page,
    }) => {
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      const back = card.getByRole("button", {
        name: "Send it back",
        exact: true,
      });
      const certify = card.getByRole("button", {
        name: "Certify for the institution",
        exact: true,
      });
      // NEITHER DECISION IS AVAILABLE WITHOUT A REASON. Both are refused by the
      // same rule, so both are asserted — a check on one would pass while the
      // other silently became free.
      await expect(back).toBeDisabled();
      await expect(certify).toBeDisabled();
      await card
        .getByLabel("What you concluded", { exact: true })
        .fill(
          "The denominator should be net of provisions; please restate it.",
        );
      await expect(back).toBeEnabled();
      await expect(certify).toBeEnabled();
      await back.click();

      // It is its author's again, so this identity has nothing left to decide and
      // the formula is off their page entirely. The same locator found the card in
      // the journey above, so this absence is a real absence.
      await expect(measureHeading(page, UI_LABEL)).toHaveCount(0);
    });
  });

  /**
   * THE AUTHOR AGAIN, because a rejection is not an ending. This is also where the
   * product's own choice about a rejection is visible: `content.decide_measure`
   * CLEARS the promotion on a reject, so the reviewer's words travel on the audit
   * event and are deliberately not left on the row as a state nobody can act on.
   * The measure is simply a draft again.
   */
  test.describe("after it is sent back", () => {
    test.use({ storageState: path.join(E2E_TMP, "admin.json") });

    test("it is a draft again, deletable again, and can be sent a second time", async ({
      page,
    }) => {
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      await expect(card.getByText("Draft", { exact: true })).toBeVisible();
      // The proposal is cleared: no "Proposed by" line survives a rejection, and
      // the withheld-delete notice is gone with it.
      await expect(card.getByText(/^Proposed by /)).toHaveCount(0);
      await expect(card.getByRole("button", { name: "Delete" })).toBeVisible();

      const send = card.getByRole("button", {
        name: "Send for certification",
        exact: true,
      });
      await card
        .getByLabel("Why it should be certified", { exact: true })
        .fill("Restated against gross exposure, as the reviewer asked.");
      await send.click();
      await expect(
        card.getByText("Waiting for a review", { exact: true }),
      ).toBeVisible();
    });
  });

  test.describe("the reviewer, the second time", () => {
    test.use({ storageState: path.join(E2E_TMP, "approver.json") });

    test("certifies the exact text, and the record says which text it was", async ({
      page,
    }) => {
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      await expect(
        card.getByRole("heading", { name: "Review this formula" }),
      ).toBeVisible();
      await card
        .getByLabel("What you concluded", { exact: true })
        .fill(
          "Checked against the classification engine's own exposure figures.",
        );
      await card
        .getByRole("button", {
          name: "Certify for the institution",
          exact: true,
        })
        .click();

      await expect(
        card.getByText("Certified by this institution", { exact: true }),
      ).toBeVisible();
      // THE RECORD IS THE TEXT THAT WAS CERTIFIED, shown as such, and it names the
      // person. " on " is load-bearing in this pattern: without it the standing
      // pill ("Certified by this institution") matches too and strict mode fails
      // on two elements — which is how the second run of this block failed.
      await expect(card.getByText(/^Certified by .+ on /)).toBeVisible();
      await expect(
        card.getByText(
          "That is the exact text that was certified. It is what the figure is worked out from, and any change to the formula has to be reviewed again.",
        ),
      ).toBeVisible();
    });
  });

  /** Back to the author: what a certified formula does, and what it will not do. */
  test.describe("once it is the institution's", () => {
    test.use({ storageState: path.join(E2E_TMP, "admin.json") });

    test("a certified formula offers NO delete control, and says why", async ({
      page,
    }) => {
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      await expect(
        card.getByText("Certified by this institution", { exact: true }),
      ).toBeVisible();
      // CERTIFYING TOOK TWO PEOPLE AND DELETING WOULD TAKE ONE. The route permits
      // it; the surface does not offer it, and the reason is on screen rather than
      // left as a missing button.
      await expect(card.getByRole("button", { name: "Delete" })).toHaveCount(0);
      await expect(card.getByRole("button", { name: "Delete it" })).toHaveCount(
        0,
      );
      await expect(
        card.getByText(
          /certifying it took two people, and the approved text is the record of what they agreed/,
        ),
      ).toBeVisible();
      // Changing it IS offered — and what the change costs is stated before the
      // form opens, not after the save.
      await card.getByRole("button", { name: "Change" }).click();
      await expect(
        page.getByText(
          "Changing this formula takes its certification away. An approver certified the exact text, not the name, so any edit returns it to a draft and it has to be reviewed again before it can be used in a chart or a grid.",
        ),
      ).toBeVisible();
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
    });

    test("and it appears in Explore's picker as one of the institution's own figures", async ({
      page,
    }) => {
      await page.goto("/explore");
      const measures = page.locator("section.card").filter({
        has: page.getByRole("heading", { name: "Measure", level: 3 }),
      });
      // ITS OWN HEADING, not a module's: a formula spans whatever modules its
      // figures need, so filing it under one would understate what it reads.
      await expect(
        measures.getByText("Certified by this institution", { exact: true }),
      ).toBeVisible();
      await measures
        .locator("label")
        .filter({ has: page.getByText(UI_LABEL, { exact: true }) })
        .first()
        .locator('input[type="checkbox"]')
        .check();

      // The question is answered — the compiler resolved the certified text into
      // the figures it names — and the answer carries a real percentage.
      const answer = page.locator("section.card").filter({
        has: page.getByRole("heading", { name: "Your question", level: 3 }),
      });
      await expect(answer.getByText(UI_LABEL).first()).toBeVisible();
      await expect(answer.getByText(/\d+\.\d\d%/).first()).toBeVisible();

      // AND THE PROVENANCE PANEL IS WITHHELD, WITH THE REASON SAID OUT LOUD.
      // `POST …/bi/explain` resolves against the STATIC catalogue and has no
      // answer for a bank's own formula, so the control is not offered rather than
      // offered and answered with an error.
      await expect(
        page.getByText(
          /One of the figures in this answer is a formula your institution defined/,
        ),
      ).toBeVisible();
    });
  });

  /**
   * Leave the fixture as it was found.
   *
   * Through the API rather than the screen, because the screen deliberately offers
   * no way to delete a certified measure — which is the property the journey above
   * asserts. `bi-subscriptions.spec.ts` runs after this file, so a measure left
   * behind would change what a later journey's catalogue holds.
   */
  test("the certified fixture formula is removed", async ({ request }) => {
    const list = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures`,
    );
    expect(list.status).toBe(200);
    const rows = (
      list.body as { measures: { id: string; measure_key: string }[] }
    ).measures;
    // The draft was deleted on screen, which is why only one is left here — and
    // asserting that is the second, independent proof that the delete journey
    // really deleted something.
    expect(rows.map((entry) => entry.measure_key)).not.toContain(SENT_BACK_KEY);
    const row = rows.find((entry) => entry.measure_key === UI_KEY);
    expect(row, `${UI_KEY} should still exist to be cleaned up`).toBeTruthy();
    const removed = await biApi(
      request,
      "admin",
      `/banks/${SAMPLE_BANK_ID}/bi/measures/${row!.id}`,
      { method: "DELETE" },
    );
    expect(removed.status).toBe(204);
  });
});
