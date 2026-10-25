/** Server formula validation, privacy and independent certification contracts. */
import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { biApi, SAMPLE_BANK_ID } from "../e2e/support/bi";

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

const MEASURE_KEY = "e2e_npl_share_pct";
const MEASURE_LABEL = "Non-performing share of the loan book";

function digestOf(source: string): string {
  return createHash("sha256").update(Buffer.from(source, "utf8")).digest("hex");
}

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
  test.afterAll(async ({ playwright }) => {
    if (!measureId) return;
    const request = await playwright.request.newContext();
    try {
      const row = await biApi(
        request,
        "admin",
        `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}`,
      );
      if (row.status === 404) return;
      expect(row.status).toBe(200);
      const removed = await biApi(
        request,
        "admin",
        `/banks/${SAMPLE_BANK_ID}/bi/measures/${measureId}`,
        { method: "DELETE" },
      );
      expect(removed.status).toBe(204);
    } finally {
      await request.dispose();
    }
  });

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
    const body = (stale.body as { error: { details: { error_code: string } } })
      .error.details;
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
async function asOf(request: Parameters<typeof biApi>[0]): Promise<string> {
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
