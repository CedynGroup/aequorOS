/** Browser subscription composition and persisted scheduling.
 * Delivery is disabled in this hermetic stack; no email is sent.
 * Recipient disclosure and ownership contracts live in contracts/bi-subscriptions.spec.ts. */
import { expect, test } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { biApi, SAMPLE_BANK_ID } from "./support/bi";

const SUBSCRIPTIONS = `/banks/${SAMPLE_BANK_ID}/bi/subscriptions`;

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
    // Delivery is off on this stack, so the empty state says that nothing goes
    // out yet instead of describing how a sent copy is prepared.
    await expect(
      page.getByText("No scheduled reports yet", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /who receives them\. Scheduled report delivery is not enabled in this deployment\./,
      ),
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
    await composer
      .locator("#bi-recipients")
      .fill(
        ["e2e.analyst@aequoros.example", "e2e.viewer@aequoros.example"].join(
          "\n",
        ),
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
    // The label must name the INSTITUTION's own zone, which for this fixture is
    // Ghana's. It read "UTC" until the composer stopped falling back to a literal
    // and took the zone from the jurisdiction registry, so this assertion was
    // pinning the defect: a user in Lagos or Nairobi set a send time believing one
    // zone while the scan used another. Both halves are asserted, because the
    // absence of "UTC" is what fails if the fallback ever comes back.
    await expect(
      composer.getByText("Time (Africa/Accra)", { exact: true }),
    ).toBeVisible();
    await expect(composer.getByText("Time (UTC)", { exact: true })).toHaveCount(
      0,
    );

    await expect(composer.locator("#bi-sub-cadence")).toHaveValue("weekly");
    await expect(composer.locator("#bi-sub-time")).toHaveValue("07:30");
    await expect(composer.locator("#bi-sub-weekday")).toHaveValue("1");
    await expect(
      composer.getByText(/Sent every Monday at 07:30 /),
    ).toBeVisible();

    await expect(submit).toBeEnabled();
    await submit.click();

    // The saved report, as the workspace lists it.
    const card = page
      .locator("section.card")
      .filter({ hasText: "Loan book by grade, Mondays" });
    await expect(card).toBeVisible();
    // Delivery is off on this stack, so the row does not claim to be sending.
    await expect(
      card.getByText("Not sending here", { exact: true }),
    ).toBeVisible();
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
      card.getByText(/including anyone whose access did not cover the figures/),
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
