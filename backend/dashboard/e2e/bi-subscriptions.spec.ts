/**
 * Scheduled report subscriptions — the create surface, the engine, and the read
 * side.
 *
 * TIMING, stated because it decides what this spec can assert. Both halves of
 * this feature landed DURING this task and are uncommitted at the time of
 * writing: `app/features/manage_bi_notifications.py` (14 tenant routes behind
 * `BANK_ROUTE_DEPENDENCIES` + `require_bi_enabled`) and the
 * `/explore/subscriptions` page with `SubscriptionComposer` / `RecipientField` /
 * `DeliveryHistory`. The generated client does not carry the operations yet, so
 * `lib/api/bi.ts` calls them directly against the wire contract — which is why
 * the page works at all today. `BI_SUBSCRIPTIONS_ENABLED` stays OFF in this
 * stack: it owns an hourly-tick branch and nothing here runs a worker, so no
 * delivery will ever be attempted and the send history is honestly empty.
 *
 * The browser journey covers the composer end to end. The API journey covers the
 * five properties a browser cannot reach from one signed-in identity — an
 * author's authority, a recipient's narrower view of the same row, reachability
 * before ownership, owner-only history, and a refused change saving nothing.
 *
 * WHAT THE API JOURNEY PROVES, none of which is available from the read routes
 * alone:
 *
 *  1. Creating a scheduled report CONFERS NOTHING. The author must hold every
 *     figure the report reads, and the stored row carries no trace of that
 *     decision — each delivery is authorized and rendered as its own recipient
 *     at send time. So a reader who may not ask the question may not instruct
 *     the platform to ask it for them, and that is asserted from the refused side.
 *  2. A DISTRIBUTION LIST IS A MEMBERSHIP DISCLOSURE. The owner sees who else
 *     receives the report; a recipient sees only themselves. Naming the others is
 *     not part of receiving it.
 *  3. REACHABILITY BEFORE OWNERSHIP. An identity nobody named cannot enumerate
 *     the report by id — 404, not 403 — while a named recipient who is not the
 *     owner gets a 403 that says only the author may change it.
 *  4. DELIVERY HISTORY IS OWNER-ONLY, and with no worker in this stack it is
 *     honestly empty rather than fabricated.
 *  5. The clock is the INSTITUTION's. `time_zone` comes from the jurisdictions
 *     registry through the bank, so "07:30" is never ambiguous.
 */

import { expect, test } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "./support/bi";
import { E2E_USERS } from "./support/mint";

const SUBSCRIPTIONS = `/banks/${SAMPLE_BANK_ID}/bi/subscriptions`;

/**
 * A question the fixture book can answer and the ORG-wide Analyst holds in full,
 * over a summary-class member set so the report would be attached rather than
 * linked.
 */
function summaryQuery(asOf: string) {
  return {
    measures: ["loans.balance_rc"],
    dimensions: ["loan.grade"],
    time: { as_of: asOf },
    filters: [],
  };
}

test.describe("scheduled report subscriptions", () => {
  test("the author's authority is checked, the stored row confers none of it, and the list is a disclosure", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);

    // 1. AN AUTHOR CANNOT SCHEDULE A QUESTION THEY MAY NOT ASK. The
    // Liquidity-only reader is refused the loan book interactively
    // (bi-authorization.spec.ts) and is refused it on a schedule too, with the
    // same error envelope so one client handler covers both.
    const refused = await biApi(request, "liquidity_viewer", SUBSCRIPTIONS, {
      method: "POST",
      data: {
        name: "Loan book, weekly",
        query: summaryQuery(asOf),
        artifact_format: "csv",
        cadence: "weekly",
        hour: 7,
        minute: 30,
        day_of_week: 1,
        recipient_emails: ["e2e.liquidity_viewer@aequoros.example"],
        reason: "attempting to schedule a question this reader cannot ask",
      },
    });
    expect(refused.status).toBe(403);
    expect(JSON.stringify(refused.body)).toContain("bi_authorization_denied");

    // 2. The organization-wide Analyst holds it, so the same report is created.
    const created = await biApi(request, "admin", SUBSCRIPTIONS, {
      method: "POST",
      data: {
        name: "Loan book by grade, Mondays",
        query: summaryQuery(asOf),
        artifact_format: "csv",
        cadence: "weekly",
        hour: 7,
        minute: 30,
        day_of_week: 1,
        // Named by ADDRESS, which is the path an author who cannot read the
        // tenant's user directory has to use.
        recipient_emails: [
          "e2e.analyst@aequoros.example",
          "e2e.liquidity_viewer@aequoros.example",
        ],
        reason: "e2e: the scheduled report journey",
      },
    });
    expect(created.status).toBe(201);
    const subscription = created.body as {
      id: string;
      name: string;
      cadence: string;
      hour: number;
      minute: number;
      day_of_week: number;
      time_zone: string;
      owner_user_id: string;
      owned_by_caller: boolean;
      recipient_user_ids: string[];
      recipients: { user_id: string; display_name: string | null }[];
      disclosure_class: string;
      delivery_note: string;
      is_active: boolean;
    };

    expect(subscription.owned_by_caller).toBe(true);
    expect(subscription.owner_user_id).toBe(E2E_USERS.admin.id);
    expect(subscription.is_active).toBe(true);
    // The clock is read where the bank is, from the jurisdictions registry —
    // not in UTC and not in the reader's browser zone.
    expect(subscription.time_zone).toBe("Africa/Accra");
    expect(subscription.hour).toBe(7);
    expect(subscription.minute).toBe(30);
    expect(subscription.day_of_week).toBe(1);
    // A summary report is attachable, and the copy says what will happen.
    expect(subscription.disclosure_class).toBe("summary");
    expect(subscription.delivery_note).toBe(
      "Each recipient is emailed this report as a file, prepared under their own access.",
    );
    // THE OWNER sees the whole distribution list, by name.
    expect(subscription.recipient_user_ids.sort()).toEqual(
      [E2E_USERS.analyst.id, E2E_USERS.liquidity_viewer.id].sort(),
    );
    expect(subscription.recipients).toHaveLength(2);
    expect(
      subscription.recipients.every((entry) => Boolean(entry.display_name)),
    ).toBe(true);

    try {
      // 3. A RECIPIENT WHO IS NOT THE OWNER sees the report and only their own
      // place on it. Who else receives it is not part of receiving it.
      const asRecipient = await biApi(
        request,
        "analyst",
        `${SUBSCRIPTIONS}/${subscription.id}`,
      );
      expect(asRecipient.status).toBe(200);
      const seen = asRecipient.body as {
        owned_by_caller: boolean;
        recipient_user_ids: string[];
        recipients: unknown[];
      };
      expect(seen.owned_by_caller).toBe(false);
      expect(seen.recipient_user_ids).toEqual([E2E_USERS.analyst.id]);
      expect(seen.recipients).toEqual([]);
      // And the other recipient is not named anywhere in the payload.
      expect(JSON.stringify(seen)).not.toContain(E2E_USERS.liquidity_viewer.id);

      // The same asymmetry through the list route.
      const ownerList = await biApi(request, "admin", SUBSCRIPTIONS);
      expect(ownerList.status).toBe(200);
      const owned = (
        ownerList.body as { subscriptions: { id: string; recipients: unknown[] }[] }
      ).subscriptions.find((entry) => entry.id === subscription.id)!;
      expect(owned.recipients).toHaveLength(2);

      const recipientList = await biApi(request, "analyst", SUBSCRIPTIONS);
      expect(recipientList.status).toBe(200);
      const listed = (
        recipientList.body as {
          subscriptions: { id: string; recipients: unknown[] }[];
        }
      ).subscriptions.find((entry) => entry.id === subscription.id)!;
      expect(listed.recipients).toEqual([]);

      // 4. REACHABILITY BEFORE OWNERSHIP. `approver` holds organization-wide
      // authority over every module, and still cannot enumerate a report nobody
      // named them on: it does not exist for them.
      const stranger = await biApi(
        request,
        "approver",
        `${SUBSCRIPTIONS}/${subscription.id}`,
      );
      expect(stranger.status).toBe(404);
      expect(JSON.stringify(stranger.body)).toContain(
        "bi_subscription_not_found",
      );

      // A named recipient CAN reach it and still may not change it. 403, and the
      // message names the rule rather than the missing grant — because there is
      // no grant that would help.
      const recipientEdit = await biApi(
        request,
        "analyst",
        `${SUBSCRIPTIONS}/${subscription.id}/deactivation`,
        { method: "POST", data: { reason: "a recipient trying to stop it" } },
      );
      expect(recipientEdit.status).toBe(403);
      expect(JSON.stringify(recipientEdit.body)).toContain(
        "bi_notification_owner_only",
      );

      // 5. DELIVERY HISTORY IS OWNER-ONLY, because it names people and what each
      // of them was or was not sent.
      const recipientHistory = await biApi(
        request,
        "analyst",
        `${SUBSCRIPTIONS}/${subscription.id}/deliveries`,
      );
      expect(recipientHistory.status).toBe(403);

      const ownerHistory = await biApi(
        request,
        "admin",
        `${SUBSCRIPTIONS}/${subscription.id}/deliveries`,
      );
      expect(ownerHistory.status).toBe(200);
      // Honestly empty: this stack runs no worker, so nothing has been sent.
      // Asserted as empty rather than skipped, so a fabricated delivery would
      // fail here.
      expect(
        (ownerHistory.body as { deliveries: unknown[] }).deliveries,
      ).toEqual([]);

      // An unknown address refuses the WHOLE change rather than dropping the
      // name, so a report can never deliver to fewer people than its author
      // believes.
      const badRecipient = await biApi(
        request,
        "admin",
        `${SUBSCRIPTIONS}/${subscription.id}`,
        {
          method: "PUT",
          data: {
            name: subscription.name,
            query: summaryQuery(asOf),
            artifact_format: "csv",
            cadence: "weekly",
            hour: 7,
            minute: 30,
            day_of_week: 1,
            recipient_emails: ["nobody.here@aequoros.example"],
            reason: "naming somebody who is not of this tenant",
          },
        },
      );
      expect(badRecipient.status).toBe(422);
      expect(JSON.stringify(badRecipient.body)).toContain(
        "bi_notification_recipient_unknown",
      );
      // The list is unchanged, which is what "nothing was saved" means.
      const afterRefusal = await biApi(
        request,
        "admin",
        `${SUBSCRIPTIONS}/${subscription.id}`,
      );
      expect(
        (afterRefusal.body as { recipient_user_ids: string[] })
          .recipient_user_ids.sort(),
      ).toEqual([E2E_USERS.analyst.id, E2E_USERS.liquidity_viewer.id].sort());

      // The owner can stop it, and stopping is not deleting.
      const stopped = await biApi(
        request,
        "admin",
        `${SUBSCRIPTIONS}/${subscription.id}/deactivation`,
        { method: "POST", data: { reason: "e2e: the owner stops it" } },
      );
      expect(stopped.status).toBe(200);
      expect((stopped.body as { is_active: boolean }).is_active).toBe(false);
    } finally {
      // The journeys share one disposable database in a fixed order; leave no
      // scheduled report behind for a later spec to trip over.
      const removed = await biApi(
        request,
        "admin",
        `${SUBSCRIPTIONS}/${subscription.id}`,
        { method: "DELETE" },
      );
      expect([200, 204]).toContain(removed.status);
    }
  });
});

test.describe("the scheduled reports workspace", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("builds a report from the reader's own catalogue and states when and to whom it goes", async ({
    page,
    request,
  }) => {
    await page.goto("/explore/subscriptions");

    // The two things a reader does with a question sit beside Explore, on its
    // own tab strip — not on the Alert Center, which is the platform's own
    // limit-breach findings and a different object with a different owner.
    const tabs = page.getByRole("navigation", { name: "Module sections" });
    for (const label of ["Explore", "Threshold alerts", "Scheduled reports"]) {
      await expect(tabs.getByRole("link", { name: label })).toBeVisible();
    }
    await expect(
      page.getByRole("heading", { name: "Scheduled reports" }),
    ).toBeVisible();

    // The honest empty state, by its own copy — not merely "the page rendered".
    await expect(
      page.getByText("No scheduled reports yet", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/each person's copy is prepared under their own access/i),
    ).toBeVisible();

    await page.getByRole("button", { name: "New report" }).click();
    const composer = page.locator("section.card").filter({
      has: page.getByRole("heading", {
        name: "New scheduled report",
        level: 3,
      }),
    });
    await expect(composer).toBeVisible();
    // The disclosure rule is stated while the question is still being built.
    await expect(
      composer.getByText(
        /Only the figures your own access covers are listed\. Each recipient's copy is prepared under their access, not yours\./,
      ),
    ).toBeVisible();

    // The composer cannot be submitted until it is a legal question with a
    // recipient and a reason — the button is the guard, and it starts disabled.
    const submit = composer.getByRole("button", { name: "Create this report" });
    await expect(submit).toBeDisabled();

    await composer.locator("#bi-sub-name").fill("Loan book by grade, Mondays");
    // The figures come from THIS reader's catalogue: a chip exists because the
    // server returned the member, not because the page hard-coded it.
    await composer
      .getByRole("button", { name: "Gross loans", exact: true })
      .click();
    await composer
      .getByRole("button", { name: "Classification grade", exact: true })
      .click();
    await composer.locator("#bi-recipients").fill(
      ["e2e.analyst@aequoros.example", "e2e.viewer@aequoros.example"].join("\n"),
    );
    await expect(composer.getByText("2 people named")).toBeVisible();
    await composer.locator("#bi-sub-reason").fill("e2e: the composer journey");

    // The default schedule is weekly, Monday, 07:30, and the sentence says so
    // with a zone attached — "07:30" alone is the sentence that makes somebody
    // expect a pack at half past seven their own time.
    // REPORTED AS DEFECT T20-D9, and asserted as it behaves. The composer takes
    // its zone from `rows[0]?.timeZone ?? "UTC"`, so before an institution has
    // saved its FIRST report the time control is labelled UTC while the server
    // reads the clock in the institution's own zone. Ghana is UTC+0 so no time is
    // actually wrong here, but a Nigerian or Kenyan tenant would set 07:30
    // believing UTC and be sent at 07:30 local. The zone is already on the bank
    // payload (`jurisdiction.timezone`), so it need not wait for a saved row.
    await expect(composer.getByText("Time (UTC)", { exact: true })).toBeVisible();

    await expect(composer.locator("#bi-sub-cadence")).toHaveValue("weekly");
    await expect(composer.locator("#bi-sub-time")).toHaveValue("07:30");
    await expect(composer.locator("#bi-sub-weekday")).toHaveValue("1");
    await expect(composer.getByText(/Sent every Monday at 07:30 /)).toBeVisible();

    await expect(submit).toBeEnabled();
    await submit.click();

    // The saved report, as the workspace lists it.
    const card = page
      .locator("section.card")
      .filter({ hasText: "Loan book by grade, Mondays" });
    await expect(card).toBeVisible();
    await expect(card.getByText("Sending", { exact: true })).toBeVisible();
    // The zone on the SAVED row is the institution's own, resolved server-side
    // from the jurisdictions registry — the same rule the delivery scan applies.
    await expect(
      card.getByText("Sent every Monday at 07:30 Africa/Accra."),
    ).toBeVisible();
    // A summary report is attachable, and the card says what will happen rather
    // than leaving the reader to assume.
    await expect(
      card.getByText(
        "Each recipient is emailed this report as a file, prepared under their own access.",
      ),
    ).toBeVisible();
    await expect(card.getByText(/Comma-separated values\./)).toBeVisible();
    // The owner sees the distribution list by name.
    await expect(card.getByText(/Sent to .*E2E Analyst/)).toBeVisible();

    // The send history is honestly empty: this stack runs no worker, and the
    // workspace says so rather than showing a blank panel.
    await card.getByRole("button", { name: "Show the send history" }).click();
    await expect(
      card.getByText("Nothing has been sent yet", { exact: true }),
    ).toBeVisible();
    await expect(
      card.getByText(
        /including anyone whose access did not cover the figures/,
      ),
    ).toBeVisible();

    // Stopping is not deleting.
    await card.getByRole("button", { name: "Stop", exact: true }).click();
    await expect(card.getByText("Stopped", { exact: true })).toBeVisible();

    // And the row the browser created is the row the API reads back.
    const listed = await biApi(request, "admin", SUBSCRIPTIONS);
    expect(listed.status).toBe(200);
    const saved = (
      listed.body as {
        subscriptions: {
          id: string;
          name: string;
          is_active: boolean;
          time_zone: string;
          recipients: unknown[];
        }[];
      }
    ).subscriptions.find(
      (entry) => entry.name === "Loan book by grade, Mondays",
    );
    expect(saved).toBeDefined();
    expect(saved!.is_active).toBe(false);
    expect(saved!.time_zone).toBe("Africa/Accra");
    expect(saved!.recipients).toHaveLength(2);

    // Leave the shared disposable database as it was found.
    await card.getByRole("button", { name: "Delete" }).click();
    await expect(
      page.getByText("No scheduled reports yet", { exact: true }),
    ).toBeVisible();
  });
});
