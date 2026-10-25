/** HTTP subscription authority, recipient disclosure and owner-only history.
 * Delivery is disabled in this hermetic stack; no email is sent. */
import { expect } from "@playwright/test";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "../e2e/support/bi";
import { test } from "../e2e/support/scoped-objects";
import { E2E_USERS } from "../e2e/support/mint";

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

test.describe("bi-subscriptions HTTP contracts", () => {
  test("the author's authority is checked, the stored row confers none of it, and the list is a disclosure", async ({
    request,
    objects,
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

    objects.register(SUBSCRIPTIONS, subscription.id);

    expect(subscription.owned_by_caller).toBe(true);
    expect(subscription.owner_user_id).toBe(E2E_USERS.admin.id);
    expect(subscription.is_active).toBe(true);
    // The clock is read where the bank is, from the jurisdictions registry —
    // not in UTC and not in the reader's browser zone.
    expect(subscription.time_zone).toBe("Africa/Accra");
    expect(subscription.hour).toBe(7);
    expect(subscription.minute).toBe(30);
    expect(subscription.day_of_week).toBe(1);
    // A summary report is attachable, and the copy says what will happen. This
    // stack runs with `BI_SUBSCRIPTIONS_ENABLED` off, as every deployment does
    // today, so the deployment fact follows the disclosure sentence rather than
    // the report claiming it is being sent.
    expect(subscription.disclosure_class).toBe("summary");
    expect(subscription.delivery_note).toBe(
      "Each recipient is emailed this report as a file, prepared under their own access. " +
        "Nothing is sent from this deployment yet: scheduled report delivery is not enabled here. " +
        "The report is kept and will go out once your platform operator enables it.",
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
        ownerList.body as {
          subscriptions: { id: string; recipients: unknown[] }[];
        }
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
        (
          afterRefusal.body as { recipient_user_ids: string[] }
        ).recipient_user_ids.sort(),
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
