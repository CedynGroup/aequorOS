/**
 * The submissions workspace around a single return.
 *
 * `submission-lifecycle` and `full-lifecycle` walk ONE return through its chain.
 * These journeys cover the pages an officer uses around that walk, each through
 * the real hermetic stack and each asserting on what the server persisted, not
 * only on what the page drew:
 *
 *  1. the calendar's deadline board is the registry's obligations — overdue and
 *     upcoming — row for row, and a row opens that return's workspace
 *  2. the templates page is the return registry, card for card
 *  3. a register correction between two versions of a return shows up on the
 *     Compare page as exactly the lines that moved
 *  4. an Org Owner's signing exemption saved in Settings changes what the
 *     preparer is offered, and the return goes for approval unsigned
 *  5. the signatures queue routes a certified return to the approver the
 *     preparer named, who signs from it until nothing is owed
 *  6. a sandbox behaviour saved in Settings governs the next filing, and the
 *     filed return's History record carries its artifacts and audit trail
 *
 * Independence: each LCR-NSFR journey claims its OWN reporting period, past the
 * indices `submission-lifecycle` (0), `full-lifecycle` (1..5) and `sso-sign-in`
 * (5) already hold, so no journey shares a package version chain with another.
 * BSD13 and LRT-PROFILE are the only other returns touched; nothing else here
 * generates BSD13, and every LRT-PROFILE assertion addresses the versions this
 * file minted rather than whichever happens to be newest.
 */

import { test, expect, type Browser, type Page } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_PASSWORD, mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";
import { generateCurrentVersion } from "./support/generate";
import {
  adoptTypedMark,
  approveAndSignAsChecker,
  certifyAsPreparer,
  fmtDateGB,
  returnsUrl,
} from "./support/ceremony";

const SAMPLE_BANK_ID = "BK-SAMP0001";
const BANK = `/banks/${SAMPLE_BANK_ID}`;
const adminState = path.join(E2E_TMP, "admin.json");
const analystState = path.join(E2E_TMP, "analyst.json");
const approverState = path.join(E2E_TMP, "approver.json");
const validatorState = path.join(E2E_TMP, "validator.json");
// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** How long a ceremony may take: pyHanko signs and re-renders the document. */
const CEREMONY_TIMEOUT = 120_000;
/** The calendar renders this many obligations per page (calendar/page.tsx). */
const CALENDAR_PAGE_SIZE = 25;

async function api(
  token: string,
  method: string,
  pathName: string,
  body?: unknown,
): Promise<any> {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${pathName}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} -> ${response.status}: ${await response.text()}`,
    );
  }
  return response.json();
}

type Obligation = {
  return_code: string;
  title: string;
  frequency: string;
  reporting_date: string;
  due_date: string;
  rag: "overdue" | "due_soon" | "on_track";
};

type ReturnTemplate = {
  code: string;
  title: string;
  frequency: string;
  template_id: string;
  regulator: string;
  generator: string;
};

const RAG_LABELS: Record<Obligation["rag"], string> = {
  overdue: "Overdue",
  due_soon: "Due soon",
  on_track: "On track",
};

let adminToken: string;
/** Reporting dates (ISO) this file owns, one per LCR-NSFR journey. */
const ownDates = { signatures: "", history: "" };
/** The newest reporting period end — a computed position for LRT-PROFILE. */
let latestPeriodEnd = "";
/** An older period end for the BSD13 exemption journey. */
let exemptionDate = "";

test.beforeAll(async () => {
  requireObjectStorage();
  adminToken = await mintBackendToken("admin");
  const listing = await api(adminToken, "GET", `${BANK}/reporting-periods`);
  const periods: { id: string; period_end: string }[] = listing.periods;
  latestPeriodEnd = String(periods[0].period_end).slice(0, 10);
  // Newest first; 0..5 belong to the other lifecycle and SSO journeys.
  const claims: [keyof typeof ownDates, number][] = [
    ["signatures", 6],
    ["history", 7],
  ];
  for (const [key, index] of claims) {
    const period = periods[index];
    ownDates[key] = String(period.period_end).slice(0, 10);
    // LCR-NSFR draws on the period's latest succeeded liquidity baseline run.
    await api(adminToken, "POST", `${BANK}/regulatory-runs`, {
      module: "liquidity",
      reporting_period_id: period.id,
      scenario_code: "baseline",
    });
  }
  exemptionDate = String(periods[8].period_end).slice(0, 10);
});

/** Generate LCR-NSFR for `date` from the workspace; the checks run with it. */
async function generateWithCleanChecks(
  page: Page,
  date: string,
): Promise<void> {
  await page.goto(returnsUrl("LCR-NSFR", date));
  await generateCurrentVersion(page);
  await expect(page.getByText("Checks passed").first()).toBeVisible({
    timeout: 60_000,
  });
}

type StoredPackage = {
  id: string;
  status: string;
  version: number;
  attestation_state: string;
  current_stage_title: string | null;
  submission_revision: string | null;
};

/** The current (not superseded) version of one return for one date, as stored. */
async function storedPackage(
  code: string,
  date: string,
): Promise<StoredPackage> {
  const listing = await api(
    adminToken,
    "GET",
    `${BANK}/regulatory-packages?return_code=${code}&reporting_date=${date}&limit=1`,
  );
  expect(listing.packages).toHaveLength(1);
  return listing.packages[0];
}

test.describe("submissions workspace", () => {
  // The preparer's session; journeys that act as another officer open their own.
  test.use({ storageState: adminState });
  test.describe.configure({ timeout: 300_000 });

  test("journey 1: the calendar board is the registry's obligations, overdue and upcoming", async ({
    browser,
  }) => {
    const analyst = await mintBackendToken("analyst");
    const registry: ReturnTemplate[] = (
      await api(analyst, "GET", "/regulatory-reporting/templates")
    ).templates;
    const frequencyOf = new Map(registry.map((t) => [t.code, t.frequency]));
    // The whole window the board summarises, in the board's own order. Every
    // obligation must be a registered return carrying its registered cadence —
    // that is what makes a deadline the regulator's rather than the bank's.
    const window = await api(
      analyst,
      "GET",
      `${BANK}/reporting-obligations?horizon_months=3&limit=100&offset=0`,
    );
    const obligations: Obligation[] = [...window.obligations];
    while (obligations.length < window.total) {
      const next = await api(
        analyst,
        "GET",
        `${BANK}/reporting-obligations?horizon_months=3&limit=100&offset=${obligations.length}`,
      );
      expect(next.obligations.length).toBeGreaterThan(0);
      obligations.push(...next.obligations);
    }
    for (const obligation of obligations) {
      expect(frequencyOf.get(obligation.return_code)).toBe(
        obligation.frequency,
      );
    }
    const firstUpcoming = obligations.findIndex((o) => o.rag !== "overdue");
    // The fixture's window looks back six months, so obligations are already
    // owed, and forward three, so others are not yet due. A board with only
    // one half would not exercise what it exists to show.
    expect(window.summary.overdue).toBeGreaterThan(0);
    expect(firstUpcoming).toBeGreaterThan(0);

    const context = await browser.newContext({ storageState: analystState });
    const page = await context.newPage();
    try {
      await page.goto("/submissions/calendar");
      const board = page.getByRole("table");
      const pager = page.getByRole("navigation", {
        name: "Reporting obligations pages",
      });
      await expect(pager).toContainText(
        `1–${CALENDAR_PAGE_SIZE} of ${window.total}`,
      );
      for (const [label, count] of [
        ["Overdue", window.summary.overdue],
        ["Due soon", window.summary.due_soon],
        ["On track", window.summary.on_track],
        ["Pending ORASS re-upload", window.summary.pending_reupload],
      ] as const) {
        await expect(
          page
            .getByText(label, { exact: true })
            .first()
            .locator("xpath=ancestor::div[contains(@class,'card')][1]"),
        ).toContainText(String(count));
      }

      /** Every row on the current page, against the registry's same slice. */
      const expectPage = async (offset: number) => {
        const slice = obligations.slice(offset, offset + CALENDAR_PAGE_SIZE);
        const rows = board.getByRole("row");
        await expect(rows).toHaveCount(slice.length + 1);
        for (const [index, obligation] of slice.entries()) {
          const row = rows.nth(index + 1);
          await expect(row).toContainText(obligation.return_code);
          await expect(row).toContainText(obligation.frequency);
          await expect(row).toContainText(fmtDateGB(obligation.reporting_date));
          await expect(row).toContainText(fmtDateGB(obligation.due_date));
          await expect(row).toContainText(RAG_LABELS[obligation.rag]);
        }
      };

      // The first page is all past due, and each one is costed.
      await expectPage(0);
      const exposure = page.getByText(
        "Indicative penalty exposure — overdue returns on this page",
      );
      await expect(exposure).toBeVisible();
      const overdueOnPage = obligations
        .slice(0, CALENDAR_PAGE_SIZE)
        .filter((o) => o.rag === "overdue");
      const asOf = new Date(`${window.as_of}T00:00:00Z`).getTime();
      for (const obligation of overdueOnPage) {
        const days = Math.floor(
          (asOf - new Date(`${obligation.due_date}T00:00:00Z`).getTime()) /
            86_400_000,
        );
        await expect(
          page.getByRole("listitem").filter({
            hasText: `${obligation.return_code} · ${fmtDateGB(obligation.reporting_date)} — due ${fmtDateGB(obligation.due_date)}`,
          }),
        ).toContainText(`(${days} ${days === 1 ? "day" : "days"} overdue)`);
      }

      // Page forward, one deterministic step at a time, to the first
      // obligation that is not yet late.
      const upcomingOffset =
        Math.floor(firstUpcoming / CALENDAR_PAGE_SIZE) * CALENDAR_PAGE_SIZE;
      for (
        let offset = CALENDAR_PAGE_SIZE;
        offset <= upcomingOffset;
        offset += CALENDAR_PAGE_SIZE
      ) {
        await pager.getByRole("button", { name: "Next" }).click();
        await expect(pager).toContainText(
          `${offset + 1}–${Math.min(offset + CALENDAR_PAGE_SIZE, window.total)} of ${window.total}`,
        );
      }
      await expectPage(upcomingOffset);

      // A row is a way in: it opens that return's workspace on its date.
      const upcoming = obligations[firstUpcoming];
      await board
        .getByRole("row")
        .nth(firstUpcoming - upcomingOffset + 1)
        .click();
      await expect(page).toHaveURL(
        `/submissions/returns?code=${encodeURIComponent(upcoming.return_code)}&date=${upcoming.reporting_date}`,
      );
      await expect(page.getByLabel("Reporting date")).toHaveValue(
        upcoming.reporting_date,
      );
    } finally {
      await context.close();
    }
  });

  test("journey 2: the templates page is the return registry, card for card", async ({
    browser,
  }) => {
    const analyst = await mintBackendToken("analyst");
    const registry: ReturnTemplate[] = (
      await api(analyst, "GET", "/regulatory-reporting/templates")
    ).templates;
    expect(registry.length).toBeGreaterThan(0);

    const context = await browser.newContext({ storageState: analystState });
    const page = await context.newPage();
    try {
      await page.goto("/submissions/templates");
      await expect(
        page.getByRole("heading", { name: "Return templates" }),
      ).toBeVisible();
      // The provenance line is unique per card and is the registry's own
      // identifiers verbatim, so it addresses each card without guessing.
      const provenance = page.getByText(/ · regulator .+ · generator /);
      await expect(provenance).toHaveCount(registry.length);
      for (const template of registry) {
        const line = page.getByText(
          `${template.template_id} · regulator ${template.regulator} · generator ${template.generator}`,
          { exact: true },
        );
        const card = page.locator(".card").filter({ has: line });
        await expect(card).toHaveCount(1);
        await expect(card).toContainText(template.code);
        await expect(card).toContainText(template.title);
        await expect(card).toContainText(template.frequency);
      }
    } finally {
      await context.close();
    }
  });

  test("journey 3: a register correction shows on Compare as exactly the lines that moved", async ({
    page,
  }) => {
    const profileUrl = `${BANK}/institution-profile`;
    // The register write is a full replacement, so every change starts from
    // the stored register and moves only the ownership split.
    const { profile } = await api(adminToken, "GET", profileUrl);
    const { id, bank_id, warnings, created_at, updated_at, ...seeded } =
      profile;
    const register = (localPct: string, foreignPct: string, reason: string) =>
      api(adminToken, "PUT", profileUrl, {
        ...seeded,
        ownership_local_pct: localPct,
        ownership_foreign_pct: foreignPct,
        reason,
      });

    await register("60", "40", "e2e compare: the register as first filed");
    try {
      await page.goto(returnsUrl("LRT-PROFILE", latestPeriodEnd));
      await expect(
        page.getByRole("heading", { name: /returns workspace/i }),
      ).toBeVisible({ timeout: 60_000 });
      const first = await generateCurrentVersion(page);
      await register("55", "45", "e2e compare: ownership corrected");
      const second = await generateCurrentVersion(page);
      expect(second).toBe(first + 1);

      await page.goto("/submissions/compare");
      await page.getByRole("tab", { name: "Return version" }).click();
      await page.getByLabel("Return").selectOption("LRT-PROFILE");
      await page.getByLabel("Reporting date").selectOption(latestPeriodEnd);
      await page.getByLabel("Baseline version").selectOption({
        label: `v${first}`,
      });
      await page.getByLabel("Compared version").selectOption({
        label: `v${second}`,
      });

      // The verdict is the server's diff of the two immutable snapshots: two
      // figures changed, nothing added or removed, all in one section.
      await expect(
        page.getByText(
          `Against v${second}: 2 changed, 0 added, 0 removed across 1 section.`,
        ),
      ).toBeVisible();
      const local = page.getByRole("row", { name: /ownership_local_pct/ });
      await expect(local).toContainText("Local ownership (%)");
      await expect(local).toContainText("(-8.33%)");
      const foreign = page.getByRole("row", { name: /ownership_foreign_pct/ });
      await expect(foreign).toContainText("Foreign ownership (%)");
      await expect(foreign).toContainText("(12.50%)");

      // And the same pair, reversed, is the opposite movement — the page is
      // reading the two snapshots, not repeating a cached answer.
      await page.getByLabel("Baseline version").selectOption({
        label: `v${second}`,
      });
      await page.getByLabel("Compared version").selectOption({
        label: `v${first}`,
      });
      await expect(
        page.getByRole("row", { name: /ownership_local_pct/ }),
      ).toContainText("(9.09%)");
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, "compare-return-versions.png"),
          fullPage: true,
        });
      }
    } finally {
      // Leave the register as the bootstrap seeded it for later specs.
      await register(
        seeded.ownership_local_pct,
        seeded.ownership_foreign_pct,
        "e2e compare: restore the seeded register",
      );
    }
  });

  test("journey 4: an Owner's signing exemption in Settings changes what the preparer is offered", async ({
    page,
    browser,
  }) => {
    // Before: the platform default demands the ceremony for every return.
    await page.goto(returnsUrl("BSD13", exemptionDate));
    await expect(
      page.getByRole("heading", { name: /returns workspace/i }),
    ).toBeVisible({ timeout: 60_000 });
    await generateCurrentVersion(page);
    await expect(page.getByText("Checks passed").first()).toBeVisible({
      timeout: 60_000,
    });
    const act = page.getByTestId("primary-filing-action");
    await expect(act).toHaveText(/certify and freeze/i);

    // The Owner exempts this one return, through the Settings form. The policy
    // is resolved as at the reporting date, so it reaches back far enough to
    // cover the date this journey generated.
    const reason = "e2e: BSD13 is filed on the maker-checker record alone";
    await page.goto("/submissions/settings");
    const policies = page.getByRole("heading", { name: "Signing policies" });
    await expect(policies).toBeVisible({ timeout: 60_000 });
    await page.locator("#policy-return-code").selectOption("BSD13");
    await page
      .getByLabel("A signature is required for returns in this scope")
      .uncheck();
    await page.locator("#policy-effective-from").fill("2020-01-01");
    await page.locator("#policy-reason").fill(reason);
    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname.endsWith(
          "/attestation/signing-policies",
        ),
    );
    await page.getByRole("button", { name: "Save policy" }).click();
    expect((await saved).ok()).toBe(true);
    await expect(
      page.getByRole("status").filter({ hasText: /^Saved\./ }),
    ).toBeVisible();
    const row = page
      .getByRole("listitem")
      .filter({ hasText: `${SAMPLE_BANK_ID} · BSD13 · any basis` });
    await expect(row).toContainText("Signature not required");
    await expect(row).toContainText("In force");
    await expect(row).toContainText(`Reason: ${reason}`);

    // After: the same version offers the bare decision, and taking it sends
    // the return for approval with no signature on it.
    await page.goto(returnsUrl("BSD13", exemptionDate));
    await expect(act).toHaveText(/send for approval/i, { timeout: 30_000 });
    await expect(act).toBeEnabled();
    const requested = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/request-approval"),
    );
    await act.click();
    expect((await requested).ok()).toBe(true);
    const sent = await storedPackage("BSD13", exemptionDate);
    expect(sent).toMatchObject({
      status: "pending_approval",
      attestation_state: "unsigned",
      current_stage_title: "Approver",
    });

    // The checker is offered the decision itself, not a ceremony to sign.
    const approver = await browser.newContext({ storageState: approverState });
    try {
      const queue = await approver.newPage();
      await queue.goto("/submissions/approvals");
      await queue
        .getByRole("row", {
          name: new RegExp(`BSD13.*${fmtDateGB(exemptionDate)}`),
        })
        .first()
        .click();
      await expect(
        queue.getByRole("button", { name: "Approve", exact: true }),
      ).toBeEnabled({ timeout: 30_000 });
      await expect(queue.getByTestId("review-and-sign")).toHaveCount(0);
    } finally {
      await approver.close();
    }
  });

  test("journey 5: the signatures queue holds a certified return for the approver it names", async ({
    page,
    browser,
  }) => {
    const date = ownDates.signatures;
    await generateWithCleanChecks(page, date);
    await certifyAsPreparer(page, "LCR-NSFR", date);

    // Routing is to a PERSON: an officer the preparer did not name owes nothing.
    const validator = await browser.newContext({
      storageState: validatorState,
    });
    try {
      const desk = await validator.newPage();
      await desk.goto("/submissions/signatures");
      await expect(desk.getByText("Nothing is waiting on you")).toBeVisible({
        timeout: 30_000,
      });
    } finally {
      await validator.close();
    }

    const approver = await browser.newContext({ storageState: approverState });
    try {
      const desk = await approver.newPage();
      await desk.goto("/submissions/signatures");
      const row = desk.getByRole("row", {
        name: new RegExp(`LCR-NSFR.*${fmtDateGB(date)}`),
      });
      await expect(row).toHaveCount(1, { timeout: 30_000 });
      await expect(row).toContainText("Approver");
      await expect(row).toContainText("Preparer certified");
      if (evidenceDir) {
        await desk.screenshot({
          path: path.join(evidenceDir, "signatures-queue.png"),
          fullPage: true,
        });
      }

      // The queue's one act opens the ceremony on the document itself.
      const open = row.getByRole("link", { name: "Open and sign" });
      await expect(open).toHaveAttribute(
        "href",
        `${returnsUrl("LCR-NSFR", date)}&sign=approver`,
      );
      await open.click();
      const workspace = desk.getByTestId("signing-workspace");
      await expect(workspace).toBeVisible({ timeout: 60_000 });
      await expect(
        workspace.getByText(/identical figures the preparer certified/i),
      ).toBeVisible({ timeout: 30_000 });
      await adoptTypedMark(workspace);
      await workspace.getByLabel("Your password").fill(E2E_PASSWORD);
      await workspace.getByRole("button", { name: "Approve and sign" }).click();
      await expect(workspace).toBeHidden({ timeout: CEREMONY_TIMEOUT });

      // Signed, so this return is owed no more. Asserted on this return's row
      // rather than on an empty queue: the approver is shared, and a journey
      // elsewhere may have routed them a return they have not reached yet.
      const queue = desk.waitForResponse(
        (response) =>
          new URL(response.url()).pathname.endsWith(
            "/attestation/awaiting-my-signature",
          ) && response.ok(),
      );
      await desk.goto("/submissions/signatures");
      const owed: { package_id: string }[] = (await (await queue).json()).items;
      const signed = await storedPackage("LCR-NSFR", date);
      expect(owed.map((item) => item.package_id)).not.toContain(signed.id);
      await expect(
        desk.getByText(`${owed.length} outstanding`, { exact: false }),
      ).toBeVisible({ timeout: 30_000 });
      await expect(row).toHaveCount(0);
    } finally {
      await approver.close();
    }
    expect(await storedPackage("LCR-NSFR", date)).toMatchObject({
      attestation_state: "fully_certified",
    });
  });

  test("journey 6: a sandbox behaviour saved in Settings governs the filing that History records", async ({
    page,
    browser,
  }) => {
    const date = ownDates.history;
    const sandbox = `${BANK}/regulatory-reporting/channel-configs/orass_sandbox`;
    try {
      // Settings: the sandbox answers "pending" twice before acknowledging.
      await page.goto("/submissions/settings");
      const behavior = page.getByLabel("Sandbox behavior");
      await expect(behavior).toBeVisible({ timeout: 60_000 });
      await behavior.selectOption("slow");
      await page
        .getByLabel("Principal user name")
        .fill("E2E Principal Officer");
      await page.getByRole("button", { name: "Save configuration" }).click();
      await expect(page.getByText("Configuration saved.")).toBeVisible();
      await page.reload();
      await expect(page.getByLabel("Sandbox behavior")).toHaveValue("slow", {
        timeout: 30_000,
      });
      await expect(page.getByLabel("Principal user name")).toHaveValue(
        "E2E Principal Officer",
      );
      const stored = await api(adminToken, "GET", sandbox);
      expect(stored.config).toMatchObject({
        sandbox_behavior: "slow",
        principal_user_name: "E2E Principal Officer",
      });

      await generateWithCleanChecks(page, date);
      await certifyAsPreparer(page, "LCR-NSFR", date);
      await approveAndSignAsChecker(browser, approverState, date);
      await fileFromApprovalsQueue(browser, date);

      const filed = await storedPackage("LCR-NSFR", date);
      expect(filed).toMatchObject({
        status: "acknowledged",
        submission_revision: "1.0",
      });
      const artifacts: { kind: string; checksum_sha256: string }[] = (
        await api(
          adminToken,
          "GET",
          `${BANK}/regulatory-packages/${filed.id}/artifacts`,
        )
      ).artifacts;
      expect(artifacts.length).toBeGreaterThan(0);

      // History: the filed record, found the way an officer would look for it.
      await page.goto("/submissions/history");
      await page.getByLabel("Reporting date from").fill(date);
      await page.getByLabel("Reporting date to").fill(date);
      await page.getByLabel("Filter by status").selectOption("acknowledged");
      const ledger = page.getByRole("row", {
        name: new RegExp(`LCR-NSFR.*${fmtDateGB(date)}`),
      });
      await expect(ledger).toHaveCount(1, { timeout: 30_000 });
      await ledger.click();

      const record = page.locator(".card").filter({
        hasText: `Package ${filed.id.slice(0, 8)}`,
      });
      await expect(record).toBeVisible();
      await expect(record).toContainText("Fully certified");
      const audit = record.getByRole("table").first();
      await expect(audit).toContainText("Generated v1 · rev 1.0");
      await expect(audit).toContainText("Submitted");
      // Three polls: two "still processing" from the slow sandbox, then the
      // acknowledgement — the behaviour saved in Settings, on the record.
      await expect(
        audit.getByRole("row").filter({ hasText: "Status Poll" }),
      ).toHaveCount(3);
      for (const artifact of artifacts) {
        await expect(record).toContainText(
          `sha256 ${artifact.checksum_sha256.slice(0, 12)}`,
        );
      }
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, "history-filed-record.png"),
          fullPage: true,
        });
      }
    } finally {
      await api(adminToken, "PUT", sandbox, {
        config: { sandbox_behavior: "ack" },
      });
    }
  });
});

/**
 * The Validator's two acts from the queue: approve the figures, then transmit
 * them, and pull the regulator's decision until it arrives.
 *
 * Filed from the Approvals page rather than the return's workspace, which is
 * where `full-lifecycle` files from, so both of the Validator's ways in are
 * walked. The sandbox in force answers pending twice, so the third pull is the
 * first that can acknowledge.
 */
async function fileFromApprovalsQueue(
  browser: Browser,
  date: string,
): Promise<void> {
  const context = await browser.newContext({ storageState: validatorState });
  try {
    const page = await context.newPage();
    await page.goto("/submissions/approvals");
    await page
      .getByRole("row", { name: new RegExp(`LCR-NSFR.*${fmtDateGB(date)}`) })
      .first()
      .click();
    await expect(page.getByTestId("validator-surface")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("validator-approve").click();
    const transmit = page.getByTestId("open-transmit");
    await expect(transmit).toBeEnabled();
    await transmit.click();
    const confirm = page.getByTestId("transmit-confirm");
    await expect(confirm).toBeVisible({ timeout: 30_000 });
    // The filing set is named before it is sent: the signed record is the
    // document both officers certified.
    await expect(confirm).toContainText("LCR-NSFR.signed");
    const send = confirm.getByTestId("transmit-confirm-submit");
    await expect(send).toBeDisabled();
    await confirm.getByRole("checkbox").check();
    await send.click();

    const check = page.getByTestId("check-regulator-status");
    await expect(check).toBeVisible({ timeout: 60_000 });
    for (const [expected, sentence] of [
      ["pending", /has not yet decided/],
      ["pending", /has not yet decided/],
      ["acknowledged", /has accepted this return/],
    ] as const) {
      const polled = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname.endsWith("/poll"),
      );
      await check.click();
      const answer = await polled;
      expect(answer.ok()).toBe(true);
      expect((await answer.json()).poll_status).toBe(expected);
      await expect(page.getByText(sentence)).toBeVisible();
    }
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "filed-receipt-acknowledged.png"),
        fullPage: true,
      });
    }
  } finally {
    await context.close();
  }
}
