/** HTTP ask authorization and existing-question ownership.
 * A queued translation job supplies ownership without an AI worker or vendor. */
import { expect, test } from "@playwright/test";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { E2E_TMP } from "../playwright.config";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "../e2e/support/bi";
import { E2E_ORG_ID, E2E_USERS } from "../e2e/support/mint";

const QUESTION = "gross loans by branch, largest first";

test.describe("bi-ask HTTP contracts", () => {
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
    test.info().annotations.push({
      type: "critical-outcome",
      description: "question.ownership",
    });
    const id = randomUUID();
    const db = new DatabaseSync(path.join(E2E_TMP, "e2e.db"));
    try {
      // No model or AI worker runs here. Persist only the queue record needed
      // for ownership, then exercise the real read and confirmation routes.
      db.prepare(
        `INSERT INTO jobs
        (id, organization_id, bank_id, job_type, status, payload, progress,
         attempts, max_attempts, queued_at)
        VALUES (?, ?, ?, 'bi_nlq_translate', 'queued', ?, '{}', 0, 3, ?)`,
      ).run(
        id.replaceAll("-", ""),
        E2E_ORG_ID,
        SAMPLE_BANK_ID,
        JSON.stringify({
          principal_user_id: E2E_USERS.admin.id,
          as_of: await fixtureAsOf(request),
          payload: { question: QUESTION },
          offered_member_ids: ["loans.balance_rc"],
        }),
        new Date().toISOString(),
      );
      const route = `/banks/${SAMPLE_BANK_ID}/bi/ask/${id}`;
      const owner = await biApi(request, "admin", route);
      expect(owner.status).toBe(200);
      expect(owner.body).toMatchObject({ question_id: id, question: QUESTION });
      // A same-bank colleague must not read or confirm this existing question.
      const read = await biApi(request, "approver", route);
      expect(read.status).toBe(404);
      const run = await biApi(request, "approver", `${route}/run`, {
        method: "POST",
        data: {
          query: {
            measures: ["loans.balance_rc"],
            time: { as_of: await fixtureAsOf(request) },
          },
        },
      });
      expect(run.status).toBe(404);
      const stillOwned = await biApi(request, "admin", route);
      expect(stillOwned.status).toBe(200);
      expect(stillOwned.body).toMatchObject({
        question_id: id,
        state: "translating",
      });
    } finally {
      db.prepare("DELETE FROM jobs WHERE id = ?").run(id.replaceAll("-", ""));
      db.close();
    }
  });
});
