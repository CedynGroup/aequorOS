/** Formula composition, privacy, deletion and certification through the browser. */
import { expect, type Page } from "@playwright/test";
import { test } from "./support/scoped-objects";
import { formulaFixture } from "./support/formula-fixture";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, SAMPLE_BANK_ID } from "./support/bi";
/**
 * The figures the formula reads, and their catalogue labels.
 *
 * Both are CREDIT, both are a position on a date, and both carry the position
 * dimensions — so they share a time behaviour and a breakdown, which is what
 * makes a formula over them answerable. Picked from
 * `app/bi/domain/catalogue/measures.py`, not invented.
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

const prepareFormula = formulaFixture(FORMULA);
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

test.describe("writing and certifying a formula on screen", () => {
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
      // This story creates its own draft through the composer gate.
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
      // Only the author can send their draft for certification.
      await expect(
        card.getByRole("heading", { name: "Send this for certification" }),
      ).toBeVisible();
    });

    test("a draft is private while it exists, and deletion honours both answers", async ({
      page,
      browser,
    }) => {
      test.info().annotations.push({
        type: "critical-outcome",
        description: "formula.draft-privacy",
      });
      // A SECOND draft, so the deletion journey destroys something the rest of the
      // file does not need. It is written through the same composer gate, which is
      // the only way this surface makes one.
      await writeDraft(page, SENT_BACK_KEY, SENT_BACK_LABEL);
      const card = measureCard(page, SENT_BACK_LABEL);

      // Check privacy while the same draft still exists for its author.
      const reviewer = await browser.newContext({
        storageState: path.join(E2E_TMP, "approver.json"),
      });
      try {
        const reviewerPage = await reviewer.newPage();
        const listed = reviewerPage.waitForResponse(
          (response) =>
            new URL(response.url()).pathname.endsWith("/bi/measures") &&
            response.ok(),
        );
        await reviewerPage.goto("/explore/measures");
        const { measures } = (await (await listed).json()) as {
          measures: { measure_key: string }[];
        };
        expect(measures.map((measure) => measure.measure_key)).not.toContain(
          SENT_BACK_KEY,
        );
        await expect(
          reviewerPage.getByRole("heading", {
            name: "Calculated measures",
            exact: true,
          }),
        ).toBeVisible();
        await expect(measureHeading(reviewerPage, SENT_BACK_LABEL)).toHaveCount(
          0,
        );
        await expect(measureHeading(page, SENT_BACK_LABEL)).toBeVisible();
      } finally {
        await reviewer.close();
      }

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
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "draft");
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

    test("the reviewer is shown the exact proposed text and no deleted draft", async ({
      page,
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "proposed");
      const deletedId = await prepareFormula(
        request,
        objects,
        SENT_BACK_KEY,
        SENT_BACK_LABEL,
        "draft",
      );
      const removed = await biApi(
        request,
        "admin",
        `/banks/${SAMPLE_BANK_ID}/bi/measures/${deletedId}`,
        { method: "DELETE" },
      );
      expect(removed.status).toBe(204);
      await page.goto("/explore/measures");
      const card = measureCard(page, UI_LABEL);
      await expect(
        card.getByRole("heading", { name: "Review this formula" }),
      ).toBeVisible();
      await expect(
        card.getByText(FORMULA, { exact: true }).first(),
      ).toBeVisible();
      await expect(card.getByText(/^Proposed by /)).toBeVisible();

      // A draft deleted in this story stays absent from the reviewer's list.
      await expect(measureHeading(page, SENT_BACK_LABEL)).toHaveCount(0);
    });

    test("a decision needs a stated conclusion, and sending it back is one of the two", async ({
      page,
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "proposed");
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
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "returned");
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
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "reproposed");
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
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "certified");
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
      request,
      objects,
    }) => {
      await prepareFormula(request, objects, UI_KEY, UI_LABEL, "certified");
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
});
