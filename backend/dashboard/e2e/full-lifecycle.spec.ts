/**
 * Filing journeys drive real certification, checker signatures and independent
 * transmission through the ORASS sandbox, including downtime, rejection and
 * send-back correction. Earlier snapshots, signatures and receipts remain
 * inspectable after corrected versions are signed and filed.
 *
 * Each story receives fresh officers, password proofs and software test keys.
 * The four filing stories claim reporting periods 1..4; the preparer-only story
 * uses index 5 and the signed BoG form uses index 6. Index 0 belongs to the
 * submission-lifecycle spec. Separate periods keep package chains independent.
 * A fixture operator prepares liquidity baselines and sandbox configuration;
 * finally-based fixtures restore sandbox behavior and retire story authority.
 *
 * Official BoG artifacts are downloaded, opened and compared with the figures
 * displayed in the browser, including signed PDF, XLSX, formula copy and CSV.
 */

import {
  expect,
  type APIRequestContext,
  type Browser,
  type Locator,
  type Page,
} from "@playwright/test";
import { test as identityTest } from "./support/story-identities";
import { ChannelConfigReadFromJSON } from "@aequoros/risk-service-api";
import type {
  PackageChainRead,
  RegulatoryPackageRead,
} from "@aequoros/risk-service-api";
import { readFile } from "fs/promises";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { readPdf, readWorkbook, readZip } from "./support/artifacts";
import { mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";
import { generateCurrentVersion } from "./support/generate";
import {
  currentFilingPackage,
  expectFilingCash,
  expectFreshFilingSignatures,
  expectPriorFilingFrozen,
  expectRepeatedApprovalSafe,
  expectRepeatedSubmissionRefused,
  expectStaleReviewRefused,
  filingAttestation,
  filingChain,
  filingEvents,
  filingPackage,
  filingSignatures,
  generateCorrectedFiling,
  uploadFilingBook,
  withdrawFilingBook,
} from "./support/filing-correction";
import {
  approveAndSignAsChecker,
  certifyAsPreparer,
  fmtDateGB,
  returnsUrl,
  sendBackAsChecker,
} from "./support/ceremony";

const SAMPLE_BANK_ID = "BK-SAMP0001";
/** The platform's liquidity return — the one the filing journeys transmit. */
const LIQUIDITY_RETURN = "LCR-NSFR";
/** BoG's own balance-sheet form, generated from the official template. */
const BOG_FORM = "BSD2";
// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const adminState = path.join(E2E_TMP, "admin.json");
const approverState = path.join(E2E_TMP, "approver.json");
const validatorState = path.join(E2E_TMP, "validator.json");
const test = identityTest.extend<{ sandboxConfig: void }>({
  sandboxConfig: [
    async ({}, provide) => {
      const response = await fetch(
        `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/orass_sandbox`,
        { headers: { Authorization: `Bearer ${fixtureOperatorToken}` } },
      );
      expect([200, 404]).toContain(response.status);
      const original =
        response.status === 200
          ? ChannelConfigReadFromJSON(await response.json()).config
          : {};
      try {
        await provide();
      } finally {
        await setSandboxConfig(original);
      }
    },
    { auto: true },
  ],
});
test.use({
  storyConfig: { roles: ["admin", "approver", "validator"], signing: true },
});
/** The committed official layouts — the sheet structure BoG published. */
const bogLayoutsDir = path.join(
  __dirname,
  "..",
  "..",
  "app",
  "reporting",
  "service",
  "bog_forms",
  "layouts",
);

// ---------------------------------------------------------------------------
// Backend API helpers (admin token minted the same way global-setup does).
// ---------------------------------------------------------------------------

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

// Administrative fixture preparation is separate from the fresh tested officers.
let fixtureOperatorToken: string;

/** One reporting date (ISO) per journey that mints a package — never the latest period. */
const journeyDates = {
  preparer: "",
  ack: "",
  downtime: "",
  reject: "",
  sendBack: "",
  bogForm: "",
};

test.beforeAll(async () => {
  requireObjectStorage();
  fixtureOperatorToken = await mintBackendToken("admin");
  const listing = await api(
    fixtureOperatorToken,
    "GET",
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
  );
  const periods: { id: string; period_end: string }[] = listing.periods;
  // Newest first; [0] belongs to the legacy submission-lifecycle journeys.
  const claims: (keyof typeof journeyDates)[] = [
    "ack",
    "downtime",
    "reject",
    "sendBack",
    "preparer",
  ];
  for (const [index, key] of claims.entries()) {
    const period = periods[index + 1];
    journeyDates[key] = String(period.period_end).slice(0, 10);
    // LCR-NSFR generation pulls from the latest succeeded liquidity baseline run
    // of its reporting period — mint one for each claimed period.
    await api(
      fixtureOperatorToken,
      "POST",
      `/banks/${SAMPLE_BANK_ID}/regulatory-runs`,
      {
        module: "liquidity",
        reporting_period_id: period.id,
        scenario_code: "baseline",
      },
    );
  }
  journeyDates.bogForm = String(periods[claims.length + 1].period_end).slice(
    0,
    10,
  );
});

/** Replace the ORASS sandbox channel config (behavior + downtime switch). */
function setSandboxConfig(config: Record<string, unknown>): Promise<unknown> {
  return api(
    fixtureOperatorToken,
    "PUT",
    `/banks/${SAMPLE_BANK_ID}/regulatory-reporting/channel-configs/orass_sandbox`,
    { config },
  );
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

/**
 * Generate a fresh package of `code` for `date`; the checks run with it.
 *
 * The checks must pass — failing checks keep certification locked, and that is a
 * product bug this helper would surface rather than paper over. What it will not
 * do is press a "Validate" button, because there is no longer one to press: the
 * assertion below is that the button is ABSENT and the checks ran regardless.
 */
async function generateAndClearChecks(
  page: Page,
  code: string,
  date: string,
): Promise<void> {
  await page.goto(returnsUrl(code, date));
  await expect(
    page.getByRole("heading", { name: /returns workspace/i }),
  ).toBeVisible();

  await expect(page.getByRole("button", { name: /^validate$/i })).toHaveCount(
    0,
  );

  await generateCurrentVersion(page);

  // Nobody pressed anything: the checks are part of generating.
  await expect(page.getByText("Checks passed").first()).toBeVisible({
    timeout: 60_000,
  });
}

/**
 * Walk a fresh package all the way to `approved` — through the ceremony.
 *
 * Two officers, two sessions, as maker-checker requires: the admin session
 * prepares and certifies, a separate approver session approves and signs. The
 * approval decision is written by that signature, in the same transaction, so
 * there is no separate approve step to perform afterwards.
 */
async function certifyAndApprove(
  page: Page,
  browser: Browser,
  code: string,
  date: string,
): Promise<void> {
  await generateAndClearChecks(page, code, date);
  await certifyAsPreparer(page, code, date);
  await approveAndSignAsChecker(browser, approverState, code, date);
}

type FilingSession = { page: Page; close: () => Promise<void> };

/**
 * A THIRD session: the officer who transmits.
 *
 * Until 2026-09-20 these journeys filed from the preparer's own admin session,
 * because the submit route required the same permission as the approval
 * decision and on an ungated family a scalar role satisfied it. Filing now
 * requires an explicit Regulatory Reporting / restricted `submit` binding — the
 * Validator — which neither the preparer nor the approver holds. The journeys
 * are therefore three sessions, not two, and they assert the new rule rather
 * than being relaxed to pass (docs/filing_workflow_redesign.md §1 finding 3).
 *
 * The Validator's stage is itself two acts: they approve the figures from the
 * queue, and only then release them to the regulator. The session approves
 * first and opens the return's workspace, where the release is offered.
 */
async function openFilingSession(
  browser: Browser,
  date: string,
): Promise<FilingSession> {
  const context = await browser.newContext({ storageState: validatorState });
  try {
    const page = await context.newPage();
    await page.goto("/submissions/approvals");
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: LIQUIDITY_RETURN, exact: true }),
      })
      .filter({
        has: page.getByRole("cell", { name: fmtDateGB(date), exact: true }),
      })
      .first()
      .click();
    await expect(page.getByTestId("validator-surface")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("validator-approve").click();
    // Approved, not yet filed: the release is now offered, and the approval
    // cannot be given twice.
    await expect(page.getByTestId("open-transmit")).toBeEnabled();
    await expect(page.getByTestId("validator-approve")).toBeDisabled();
    await page.goto(returnsUrl(LIQUIDITY_RETURN, date));
    return { page, close: () => context.close() };
  } catch (error) {
    await context.close();
    throw error;
  }
}

/** Correct one exact package, obtain new signatures and follow its own receipt. */
async function correctAndRefile(
  page: Page,
  browser: Browser,
  request: APIRequestContext,
  date: string,
  prior: RegulatoryPackageRead,
  signatures: ReturnType<typeof filingSignatures>,
  staleReview: PackageChainRead,
  control: Locator,
): Promise<void> {
  const priorEvents = await filingEvents(request, prior.id);
  expectFilingCash(prior, 2_000_000);
  const sourceRunId = await uploadFilingBook(
    request,
    date,
    "3000000",
    test.info().outputPath("corrected-filing-book.xlsx"),
  );
  const corrected = await generateCorrectedFiling(
    page,
    request,
    prior,
    control,
  );
  expectFilingCash(corrected, 3_000_000);
  expect(corrected.sourceRuns).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ module: "liquidity", run_id: sourceRunId }),
    ]),
  );
  expect(corrected.sourceRuns).not.toEqual(prior.sourceRuns);
  expect(corrected.snapshotSha256).not.toBe(prior.snapshotSha256);
  await expectPriorFilingFrozen(request, prior, signatures);
  await certifyAsPreparer(page, LIQUIDITY_RETURN, date);
  await expectStaleReviewRefused(request, corrected.id, staleReview);
  await approveAndSignAsChecker(browser, approverState, LIQUIDITY_RETURN, date);
  await expectFreshFilingSignatures(request, corrected.id, signatures);

  const filing = await openFilingSession(browser, date);
  try {
    await expectRepeatedApprovalSafe(request, corrected.id);
    await fileViaSandbox(filing.page);
    // ORASS revision counts granted resubmissions; these corrections have no grant.
    await expect(filing.page.getByText("Rev 1.0")).toBeVisible();
    await expectRepeatedSubmissionRefused(request, corrected.id);
    const poll = filing.page.getByTestId("primary-filing-action");
    await expect(poll).toHaveText(/check for .* decision/i);
    await expect(poll).toBeEnabled();
    await poll.click();
    await expect(
      filing.page.getByText("Acknowledged", { exact: true }).first(),
    ).toBeVisible();
    const acknowledged = await filingPackage(request, corrected.id);
    expect(acknowledged).toMatchObject({
      id: corrected.id,
      version: prior.version + 1,
      supersedesId: prior.id,
      status: "acknowledged",
      submissionRevision: "1.0",
    });
    expectFilingCash(acknowledged, 3_000_000);
    expect(acknowledged.snapshot).toEqual(corrected.snapshot);
    const events = (await filingEvents(request, corrected.id)).events;
    expect(events.filter((event) => event.event === "submitted")).toHaveLength(
      1,
    );
    expect(
      events.filter((event) => event.event === "acknowledged"),
    ).toHaveLength(1);
    expect(events.every((event) => event.packageId === corrected.id)).toBe(
      true,
    );
    await test.info().attach("Corrected filing acknowledged", {
      body: await filing.page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  } finally {
    await filing.close();
  }
  await expectPriorFilingFrozen(request, prior, signatures);
  expect(await filingEvents(request, prior.id)).toEqual(priorEvents);
}

/**
 * The command bar keeps the return's identity readable beside its artifacts.
 *
 * On one row (the `xl` layout) the identity column is the one that flexes, and
 * once a return is signed its artifacts carry a long signature line. That line
 * and the act used to squeeze the identity to a few pixels, its words spilling
 * over the artifacts. Asserted on the Validator's screen because it is the
 * widest case: a signed return and the filing act with its notice.
 */
async function expectReadableCommandBar(page: Page): Promise<void> {
  const bar = page.getByTestId("return-command-bar");
  await expect(bar).toBeVisible();
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    await expect
      .poll(() =>
        bar.evaluate((element) => {
          const [identity, artifacts] = Array.from(element.children);
          const own = identity.getBoundingClientRect();
          return {
            row: getComputedStyle(element).flexDirection === "row",
            readable: own.width >= 240,
            clear: own.right <= artifacts.getBoundingClientRect().left,
            contained: identity.scrollWidth <= identity.clientWidth,
          };
        }),
      )
      .toEqual({ row: true, readable: true, clear: true, contained: true });
  }
}

/**
 * File the approved, fully certified return through the ORASS sandbox.
 *
 * The filing act is the ONE primary action the Validator's screen offers. It is
 * reached by its role, not by hunting for a Submit card among four others —
 * which is the point: no other session has this control at all.
 */
async function fileViaSandbox(page: Page): Promise<void> {
  // Cleared to submit is a claim the screen makes only when the service says
  // so — and it says so because both signatures are on record.
  await expect(page.getByTestId("attestation-clearance").first()).toHaveText(
    /^Cleared to submit$/i,
  );
  const file = page.getByTestId("primary-filing-action");
  await expect(file).toBeEnabled();
  await expectReadableCommandBar(page);
  // Stated in words before it is offered as a button.
  await expect(page.getByTestId("transmission-notice")).toContainText(
    /cannot be recalled/i,
  );
  await page.getByLabel("Channel").selectOption("orass_sandbox");
  await file.click();
}

type TakenArtifact = {
  /** The name the browser saved the file under. */
  filename: string;
  /** The name the server's Content-Disposition gave it. */
  servedAs: string;
  contentType: string;
  bytes: Buffer;
};

/** The download route of every artifact a return's command bar produces. */
const EXPORTED_ARTIFACT = /\/regulatory-artifacts\/[^/]+\/download$/;

/**
 * Press one artifact button in the command bar and keep what the browser saved.
 *
 * The button is the file: it produces what does not exist yet, then fetches and
 * saves it through a blob link. A blob carries no HTTP headers, so the content
 * type and served filename are read off the download response it was built
 * from — the one whose path matches `servedFrom`.
 */
async function takeArtifact(
  page: Page,
  label: RegExp,
  servedFrom: RegExp,
): Promise<TakenArtifact> {
  const served = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      servedFrom.test(new URL(response.url()).pathname),
    { timeout: 60_000 },
  );
  const saved = page.waitForEvent("download", { timeout: 60_000 });
  await page
    .getByTestId("return-command-bar")
    .getByRole("button", { name: label })
    .click();
  const response = await served;
  expect(response.ok()).toBeTruthy();
  const download = await saved;
  const bytes = await readFile(await download.path());
  // What the officer keeps is exactly what the server serves. Read back
  // independently: the page consumed this response into a blob, so the
  // browser no longer holds its body.
  const independent = await fetch(response.url(), {
    headers: { Authorization: `Bearer ${await mintBackendToken("admin")}` },
  });
  expect(independent.ok).toBeTruthy();
  expect(bytes.equals(Buffer.from(await independent.arrayBuffer()))).toBe(true);
  const disposition = response.headers()["content-disposition"] ?? "";
  return {
    filename: download.suggestedFilename(),
    servedAs: /filename="([^"]+)"/.exec(disposition)?.[1] ?? "",
    contentType: response.headers()["content-type"] ?? "",
    bytes,
  };
}

/** A figure as the snapshot preview prints it: "1,234.5", "(1,234.5)", "—". */
function parseDisplayedFigure(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "—") return null;
  const magnitude = Number(trimmed.replace(/[(),]/g, ""));
  return trimmed.startsWith("(") ? -magnitude : magnitude;
}

/** Parse one CSV document into rows (RFC 4180 quoting, `\n` line ends). */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }
  if (field !== "" || row.length > 0) rows.push([...row, field]);
  return rows;
}

// ---------------------------------------------------------------------------
// Journeys
// ---------------------------------------------------------------------------

test.describe("full lifecycle", () => {
  test.use({ storageState: adminState });
  // Two real certifications per journey: pyHanko signs the document and the
  // export engine re-renders it, and pdf.js renders it again in the browser.
  test.describe.configure({ timeout: 300_000 });

  test("journey 0: a preparer is never shown a way to reach the regulator", async ({
    page,
  }) => {
    // The founder's complaint, as an assertion. The old screen rendered
    // Generate, Validate, Approve and Submit-to-ORASS for everybody, each
    // greyed out with a blocked reason, and asked a preparer to work out from
    // four dead controls that none of it was theirs. Absent, not disabled
    // (docs/filing_workflow_redesign.md §4b.1, §4b.6).
    await generateAndClearChecks(page, LIQUIDITY_RETURN, journeyDates.preparer);

    for (const forbidden of [/ORASS/i, /downtime/i, /re-upload/i]) {
      await expect(page.getByText(forbidden)).toHaveCount(0);
    }
    await expect(page.getByLabel("Channel")).toHaveCount(0);
    await expect(page.getByTestId("transmission-row")).toHaveCount(0);
    await expect(page.getByTestId("events-row")).toHaveCount(0);
    await expect(page.getByTestId("resubmission-row")).toHaveCount(0);

    // What they DO get: one act that is theirs, named, with what it does.
    const act = page.getByTestId("primary-filing-action");
    await expect(act).toHaveText(/certify and freeze/i);
    await expect(
      page.getByTestId("primary-filing-action-reason"),
    ).toContainText(/sends the return to the approver/i);
  });

  test("journey 1: certify → approve and sign → submit → poll acknowledges with Rev 1.0", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.ack;
    await setSandboxConfig({ sandbox_behavior: "ack" });

    await certifyAndApprove(page, browser, LIQUIDITY_RETURN, date);
    const filing = await openFilingSession(browser, date);
    try {
      await fileViaSandbox(filing.page);

      // Submission stamps the ORASS revision — 1.0 for a first submission.
      await expect(filing.page.getByText("Rev 1.0")).toBeVisible();

      const decision = filing.page.getByTestId("primary-filing-action");
      await expect(decision).toHaveText(/check for .* decision/i);
      await expect(decision).toBeEnabled();
      await decision.click();

      await expect(filing.page.getByText("Acknowledged").first()).toBeVisible();
      await expect(filing.page.getByText("Rev 1.0")).toBeVisible();
    } finally {
      await filing.close();
    }
  });

  test("journey 2: downtime → email fallback → ORASS re-upload → acknowledged", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.downtime;
    await setSandboxConfig({ sandbox_behavior: "ack", downtime: true });

    await certifyAndApprove(page, browser, LIQUIDITY_RETURN, date);
    const filing = await openFilingSession(browser, date);
    const filingPage = filing.page;
    try {
      await fileViaSandbox(filingPage);

      // The structured channel_downtime 409 surfaces as the guided fallback
      // panel, not a raw error.
      await expect(
        filingPage.getByText(/ORASS downtime — email fallback available/),
      ).toBeVisible();
      await filingPage
        .getByRole("button", { name: "Use email fallback" })
        .click();

      // Email submission is provisional (BG/FMD/2026/07): deemed complete only
      // after re-upload through ORASS once functionality is restored. Two
      // indicators: the actionable workspace panel (a <p> title) and the
      // events-feed chip on the email submission event (a <span>, which stays
      // on the historical event even after the re-upload completes).
      const reuploadPanel = filingPage.locator("p", {
        hasText: "Sent by the downtime bundle, and not yet complete",
      });
      await expect(reuploadPanel).toBeVisible();
      // The events feed lives in the Trail row, which stays collapsed until
      // it is opened.
      const trail = filingPage.getByTestId("events-row");
      await trail.getByRole("button", { expanded: false }).click();
      await expect(
        trail.locator("span", { hasText: "Pending ORASS re-upload" }),
      ).toBeVisible();
      // The re-upload is the Validator's one act while the filing is
      // incomplete — it is the primary action, not a button hidden in a card.
      const reupload = filingPage.getByTestId("primary-filing-action");
      await expect(reupload).toHaveText(/re-upload to the portal/i);
      await expect(reupload).toBeEnabled();

      await setSandboxConfig({ sandbox_behavior: "ack", downtime: false });
      await reupload.click();
      await expect(reuploadPanel).toBeHidden();

      const decision = filingPage.getByTestId("primary-filing-action");
      await expect(decision).toHaveText(/check for .* decision/i);
      await expect(decision).toBeEnabled();
      await decision.click();
      await expect(filingPage.getByText("Acknowledged").first()).toBeVisible();
    } finally {
      await filing.close();
    }
  });

  test("a rejected filing is corrected, re-signed and acknowledged without reusing stale approval", async ({
    page,
    browser,
    request,
  }) => {
    test.info().annotations.push({
      type: "critical-outcome",
      description: "filing.rejected-correction",
    });
    const date = journeyDates.reject;
    await setSandboxConfig({ sandbox_behavior: "reject" });
    try {
      await uploadFilingBook(
        request,
        date,
        "2000000",
        test.info().outputPath("original-filing-book.xlsx"),
      );
      await certifyAndApprove(page, browser, LIQUIDITY_RETURN, date);
      const original = await currentFilingPackage(request, date);
      const filing = await openFilingSession(browser, date);
      try {
        await fileViaSandbox(filing.page);
        const decision = filing.page.getByTestId("primary-filing-action");
        await expect(decision).toHaveText(/check for .* decision/i);
        await expect(decision).toBeEnabled();
        await decision.click();
        await expect(
          filing.page.getByText("Rejected", { exact: true }).first(),
        ).toBeVisible();
        await expect(
          filing.page.getByText("What the supervisor said about this return"),
        ).toBeVisible();
        await expect(filing.page.getByText(/SIM-LQ-104/).first()).toBeVisible();
      } finally {
        await filing.close();
      }
      const prior = await filingPackage(request, original.id);
      expect(prior.snapshot).toEqual(original.snapshot);
      expect(prior.snapshotSha256).toBe(original.snapshotSha256);
      expect(prior.sourceRuns).toEqual(original.sourceRuns);
      expect(prior).toMatchObject({
        status: "rejected",
        submissionRevision: "1.0",
      });
      const signatures = filingSignatures(prior.id);
      expect(signatures).toHaveLength(2);
      const staleReview = await filingChain(request, prior.id);

      await page.goto(returnsUrl(LIQUIDITY_RETURN, date));
      const rework = page.getByTestId("primary-filing-action");
      await expect(rework).toHaveText(/generate a corrected version/i);
      await expect(rework).toBeEnabled();
      await expect(
        page.getByText("What the supervisor said about this return"),
      ).toBeVisible();
      await expect(page.getByTestId("transmission-row")).toHaveCount(0);
      await expect(page.getByRole("button", { name: /ORASS/i })).toHaveCount(0);
      await expect(page.getByLabel("Channel")).toHaveCount(0);

      await setSandboxConfig({ sandbox_behavior: "ack" });
      await correctAndRefile(
        page,
        browser,
        request,
        date,
        prior,
        signatures,
        staleReview,
        rework,
      );
    } finally {
      try {
        await withdrawFilingBook(request, date);
      } finally {
        await setSandboxConfig({ sandbox_behavior: "ack" });
      }
    }
  });

  test("a sent-back filing retains its withdrawn signature and reaches acknowledgement with fresh signatures", async ({
    page,
    browser,
    request,
  }) => {
    test.info().annotations.push({
      type: "critical-outcome",
      description: "filing.sent-back-correction",
    });
    const date = journeyDates.sendBack;
    const note = "Cash vault omits a GHS 1M contribution to share capital.";
    await setSandboxConfig({ sandbox_behavior: "ack" });
    try {
      await uploadFilingBook(
        request,
        date,
        "2000000",
        test.info().outputPath("original-filing-book.xlsx"),
      );
      await generateAndClearChecks(page, LIQUIDITY_RETURN, date);
      await certifyAsPreparer(page, LIQUIDITY_RETURN, date);
      const original = await currentFilingPackage(request, date);
      const certified = await filingAttestation(request, original.id);
      const signatures = filingSignatures(original.id);
      expect(signatures).toHaveLength(1);
      const staleReview = await filingChain(request, original.id);
      // A different return on the same date must keep its place in the queue.
      // BSD2's landscape document needs the same viewport as the artifact journey.
      await page.setViewportSize({ width: 1920, height: 1080 });
      await generateAndClearChecks(page, BOG_FORM, date);
      await certifyAsPreparer(page, BOG_FORM, date);
      await sendBackAsChecker(
        browser,
        approverState,
        LIQUIDITY_RETURN,
        date,
        note,
      );
      const companion = await api(
        await mintBackendToken("admin"),
        "GET",
        `/banks/${SAMPLE_BANK_ID}/regulatory-packages?return_code=${BOG_FORM}&reporting_date=${date}&include_superseded=false`,
      );
      expect(companion.packages).toHaveLength(1);
      expect(companion.packages[0].status).toBe("pending_approval");

      await page.goto(returnsUrl(LIQUIDITY_RETURN, date));
      await expect(page.getByText("Unsigned").first()).toBeVisible();
      await expect(
        page.getByTestId("attestation-clearance").first(),
      ).toHaveText(/^Not cleared to submit/i);
      await expect(page.getByTestId("transmission-row")).toHaveCount(0);
      await expect(page.getByTestId("sent-back-notice")).toContainText(note);
      const certification = page.getByTestId("certification-row");
      await certification.getByRole("button", { expanded: false }).click();
      await expect(
        certification.getByRole("button", { expanded: true }),
      ).toBeVisible();
      await expect(
        page.getByText(/retained in the append-only trail/i),
      ).toBeVisible();
      const withdrawn = await filingAttestation(request, original.id);
      expect(withdrawn).toMatchObject({
        attestationCycle: certified.attestationCycle + 1,
        attestationState: "unsigned",
        canSubmit: false,
        signatures: [],
      });
      expect(filingSignatures(original.id)).toEqual(signatures);
      const prior = await filingPackage(request, original.id);
      expect(prior.snapshot).toEqual(original.snapshot);
      expect(prior.snapshotSha256).toBe(original.snapshotSha256);
      expect(prior.sourceRuns).toEqual(original.sourceRuns);
      expect(prior.workflowRound).toBe(staleReview.round + 1);
      const regenerate = page
        .getByRole("button", { name: /^regenerate$/i })
        .first();
      await expect(regenerate).toBeEnabled();
      await correctAndRefile(
        page,
        browser,
        request,
        date,
        prior,
        signatures,
        staleReview,
        regenerate,
      );
    } finally {
      try {
        await withdrawFilingBook(request, date);
      } finally {
        await setSandboxConfig({ sandbox_behavior: "ack" });
      }
    }
  });

  test("journey 5: institution register drives the LRT corporate pack", async ({
    page,
  }) => {
    await page.goto("/institution");
    await expect(
      page.getByRole("heading", { name: "Institution Profile" }),
    ).toBeVisible();
    // Seeded corporate register (global-setup PUT institution-profile).
    await expect(page.getByText("GH-UB-9001")).toBeVisible();

    // An LRT pack is event-driven: the regulator sets no reporting date, so
    // the link carries the institution's latest computed position and the
    // workspace opens on it, ready to generate.
    const register = page.getByRole("link", { name: /Generate LRT packs/ });
    await expect(register).toHaveAttribute(
      "href",
      /code=LRT-PROFILE&date=\d{4}-\d{2}-\d{2}$/,
    );
    await register.click();
    // First hit on the workspace route in a run may wait on the dev server's
    // compile, so this navigation gets the same allowance as the other
    // first-render waits in this file.
    await expect(page).toHaveURL(/code=LRT-PROFILE&date=\d{4}-\d{2}-\d{2}/, {
      timeout: 60_000,
    });
    // Scoped to the fidelity banner paragraph — the return <select> carries
    // the same text in its LRT-PROFILE option.
    await expect(
      page.locator("p", {
        hasText: "LRT-PROFILE — Corporate Profile Update pack",
      }),
    ).toBeVisible();
    await expect(page.getByTestId("reporting-date-source")).toContainText(
      "computed position dates",
    );
    await expect(page.getByLabel("Reporting date")).toBeEnabled();

    await generateCurrentVersion(page);
    // The pack pre-fills from the register (no engine runs), and the checks
    // run with it — there is no separate act to offer.
    await expect(page.getByText("Checks passed").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /^validate$/i })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("button", { name: "Re-run checks" }),
    ).toBeEnabled();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "lrt-profile-pack-generated.png"),
        fullPage: true,
      });
    }
  });

  test("journey 6: a signed BoG form hands over every artifact, and each file is the return on screen", async ({
    page,
    browser,
  }) => {
    const date = journeyDates.bogForm;
    // BSD2 is a landscape form. At the default width its page is wider than
    // the signing workspace's document pane, which centres it and clips its
    // left edge — where the signature fields go — under the field palette.
    await page.setViewportSize({ width: 1920, height: 1080 });
    await certifyAndApprove(page, browser, BOG_FORM, date);
    await page.goto(returnsUrl(BOG_FORM, date));

    // The figure an officer reads on screen, located through the package's own
    // snapshot: the preview deliberately hides each line's workbook cell, so
    // the snapshot is what says where that figure must sit in the files.
    const listing = await api(
      await mintBackendToken("admin"),
      "GET",
      `/banks/${SAMPLE_BANK_ID}/regulatory-packages?return_code=${BOG_FORM}&reporting_date=${date}&include_superseded=false&limit=1`,
    );
    const packagePath = `/banks/${SAMPLE_BANK_ID}/regulatory-packages/${listing.packages[0].id}`;
    const pkg = await api(await mintBackendToken("admin"), "GET", packagePath);
    type Line = {
      code: string;
      value: string | null;
      cell: string;
      status: string;
    };
    const balanceSheet: { code: string; rows: Line[] } =
      pkg.snapshot.sections.find(
        (section: { title: string }) => section.title === BOG_FORM,
      );
    const line = balanceSheet.rows.find(
      (row) => row.status === "mapped" && Number(row.value ?? 0) !== 0,
    );
    if (!line) throw new Error(`${BOG_FORM} has no mapped, non-zero line.`);
    const section = page
      .locator("div")
      .filter({ has: page.getByText(balanceSheet.code, { exact: true }) })
      .filter({ has: page.getByRole("table") })
      .last();
    const onScreen = section
      .getByRole("row")
      .filter({ has: page.getByText(line.code, { exact: true }) })
      .getByRole("cell")
      .last();
    const figure = parseDisplayedFigure(await onScreen.innerText());
    expect(figure).not.toBeNull();

    // An official form offers all four, the PDF already as the signed return.
    const artifacts = page
      .getByTestId("return-command-bar")
      .getByRole("button", { name: /download/i });
    await expect(artifacts).toHaveText([
      "Signed PDF",
      "XLSX",
      "Formula copy",
      "CSV",
    ]);

    // The Signed PDF is the revision both officers signed, not a fresh render.
    const chain = await api(
      await mintBackendToken("admin"),
      "GET",
      `${packagePath}/artifact-versions`,
    );
    const filed = chain.versions.find(
      (version: { is_filed: boolean }) => version.is_filed,
    );
    const pdf = await takeArtifact(
      page,
      /^download signed pdf$/i,
      new RegExp(`/regulatory-artifact-versions/${filed.id}/download$`),
    );
    expect(pdf.filename).toMatch(new RegExp(`^${BOG_FORM}.*\\.pdf$`));
    expect(pdf.servedAs).toBe(pdf.filename);
    expect(pdf.contentType).toBe("application/pdf");
    expect(pdf.bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
    const document = await readPdf(pdf.bytes);
    expect(document.pageCount).toBeGreaterThan(0);
    expect(document.text).toContain(BOG_FORM);
    expect(document.signatureFields).toEqual(
      expect.arrayContaining(["Sig_Preparer", "Sig_Approver"]),
    );
    expect(document.signatureCount).toBe(2);

    // Both workbooks carry BoG's sheets, in BoG's order, plus the notes.
    const layout = JSON.parse(
      await readFile(path.join(bogLayoutsDir, `${BOG_FORM}.json`), "utf8"),
    ) as { sheets: { name: string }[] };
    const officialSheets = [
      ...layout.sheets.map((sheet) => sheet.name.slice(0, 31)),
      "Completion notes",
    ];
    const spreadsheet =
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

    // The official copy is values only, and every form sheet is locked.
    const official = await takeArtifact(
      page,
      /^(produce and )?download xlsx$/i,
      EXPORTED_ARTIFACT,
    );
    expect(official.filename).toBe(`${BOG_FORM}.xlsx`);
    expect(official.servedAs).toBe(official.filename);
    expect(official.contentType).toBe(spreadsheet);
    const sealed = readWorkbook(official.bytes);
    expect(sealed.sheets.map((sheet) => sheet.name)).toEqual(officialSheets);
    expect(sealed.title).toBe(`${BOG_FORM} — official sealed export`);
    for (const sheet of sealed.sheets.slice(0, -1)) {
      expect(sheet.isProtected, sheet.name).toBe(true);
      expect(sheet.formulaCount, sheet.name).toBe(0);
    }
    expect(sealed.sheet(BOG_FORM).cell(line.cell)).toBeCloseTo(figure!, 2);
    expect(
      sealed
        .sheet("Completion notes")
        .strings.some((text) => text.includes("FORMULA COPY")),
    ).toBe(false);

    // The formula copy keeps the template's live formulas, says what it is in
    // its own title and notes, and holds the same input figure.
    const working = await takeArtifact(
      page,
      /^(produce and )?download formula copy$/i,
      EXPORTED_ARTIFACT,
    );
    expect(working.filename).toBe(`${BOG_FORM}.working.xlsx`);
    expect(working.servedAs).toBe(working.filename);
    expect(working.contentType).toBe(spreadsheet);
    const live = readWorkbook(working.bytes);
    expect(live.sheets.map((sheet) => sheet.name)).toEqual(officialSheets);
    expect(live.title).toMatch(new RegExp(`^${BOG_FORM} — FORMULA COPY`));
    expect(
      live
        .sheet("Completion notes")
        .strings.some((text) => text.startsWith("FORMULA COPY")),
    ).toBe(true);
    expect(live.sheet(BOG_FORM).formulaCount).toBeGreaterThan(0);
    expect(live.sheet(BOG_FORM).isProtected).toBe(false);
    expect(live.sheet(BOG_FORM).cell(line.cell)).toBeCloseTo(figure!, 2);

    // A multi-sheet form's CSV is a bundle: metadata, one file per sheet,
    // provenance — and it is served as the zip it is.
    const csv = await takeArtifact(
      page,
      /^(produce and )?download csv$/i,
      EXPORTED_ARTIFACT,
    );
    expect(csv.filename).toBe(`${BOG_FORM}.zip`);
    expect(csv.servedAs).toBe(csv.filename);
    expect(csv.contentType).toBe("application/zip");
    const bundle = readZip(csv.bytes);
    const names = Array.from(bundle.keys());
    expect(names[0]).toBe("00_metadata.csv");
    expect(names.at(-1)).toBe("99_provenance.csv");
    const sheetFile = names.find((name) =>
      name.endsWith(`_${balanceSheet.code}.csv`),
    );
    expect(sheetFile).toBeDefined();
    const csvRow = parseCsv(bundle.get(sheetFile!)!.toString("utf8")).find(
      (row) => row[0] === line.code,
    );
    expect(csvRow).toBeDefined();
    expect(
      csvRow!.some(
        (cell) => cell !== "" && Math.abs(Number(cell) - figure!) < 0.005,
      ),
    ).toBe(true);

    if (evidenceDir) {
      await page.getByTestId("return-command-bar").screenshot({
        path: path.join(evidenceDir, "bog-form-artifacts-downloaded.png"),
      });
    }
  });
});
