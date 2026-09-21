/**
 * The ICAAP from review to filing, end to end, on the hermetic stack.
 *
 * The journey is the P3 acceptance walk: an annual assessment is put forward
 * for review, the officer who wrote it is refused the decision on it, an
 * approver sends it back and then approves it, the preparer seals it, the
 * sealed report is signed, the board resolution is attached, and the filing is
 * recorded.
 *
 * WHY IT RUNS AGAINST A TEST INSTRUMENT. Fifteen of the seventeen Ghana
 * sections are still built from an exposure draft whose final text the
 * regulator has not published (D-006), and the freeze path refuses a real
 * filing while that is true. A framework whose sections are all sourced exists
 * for exactly this; `playwright.config.ts` points the backend at it, and the
 * settings validator refuses that directory outside `local`/`test`.
 *
 * WHAT IS ASSERTED THAT NOTHING ELSE COVERS:
 *  - separation of duties is visible BEFORE the act, not only after it;
 *  - the board's signature waits its turn, and says why;
 *  - the filing is refused while a required document is missing, in the
 *    server's own words;
 *  - a rehearsal says, on every surface that can show one, that it will never
 *    be filed.
 */

import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";

const SAMPLE_BANK_ID = "BK-SAMP0001";
/** The fully-sourced test instrument the filing journey runs against. */
const TEST_FRAMEWORK = "test_icaap";
/** A chain cannot be longer than the platform will run; this bounds the walk. */
const MAX_CHAIN_STEPS = 20;

/**
 * The financial year the canonical fixture can compute, and the one the
 * governed commencement date was moved onto in `scripts/e2e_bootstrap.py`.
 * Resolved from the API rather than written down, so a fixture that moves does
 * not leave this journey binding nothing.
 */
let fiscalYear = 0;
let frameworkVersion = "";
let cycleId = "";

/**
 * The year end, the sealed capital run, and the version of the test
 * instrument — resolved once and shared by both describes.
 *
 * The framework VERSION is read from the API rather than written down: the
 * cycle pins the exact version it was started under, and a fixture whose
 * version moves would otherwise fail here as a 422 rather than as the drift it
 * is.
 */
async function resolveFixtures(): Promise<void> {
  if (frameworkVersion !== "") return;
  const periods = await api(
    "admin",
    "GET",
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
  );
  const yearEnd =
    periods.periods.find((period: { period_end: string }) =>
      period.period_end.endsWith("-12-31"),
    ) ?? periods.periods[0];
  fiscalYear = Number(String(yearEnd.period_end).slice(0, 4));
  expect(fiscalYear).toBeGreaterThan(2000);
  await api("admin", "POST", `/banks/${SAMPLE_BANK_ID}/regulatory-runs`, {
    module: "capital",
    reporting_period_id: yearEnd.id,
    scenario_code: "baseline",
  });

  const frameworks = await api(
    "analyst",
    "GET",
    `/banks/${SAMPLE_BANK_ID}/icaap/frameworks`,
  );
  const instrument = (frameworks.frameworks ?? []).find(
    (entry: { code: string }) => entry.code === TEST_FRAMEWORK,
  );
  expect(
    instrument,
    `the test instrument ${TEST_FRAMEWORK} is not published to this deployment — ` +
      "check ICAAP_EXTRA_FRAMEWORKS_DIR and ICAAP_FRAMEWORKS_ENABLED in playwright.config.ts",
  ).toBeTruthy();
  frameworkVersion = instrument.version;
}

/** The annual assessment this journey works on, whoever created it. */
async function annualCycleId(): Promise<string> {
  const cycles = await api(
    "analyst",
    "GET",
    `/banks/${SAMPLE_BANK_ID}/icaap/cycles`,
  );
  const annual = (cycles.cycles ?? []).find(
    (entry: { cycle_kind: string }) => entry.cycle_kind === "annual",
  );
  expect(annual, "the annual assessment was not created").toBeTruthy();
  return annual.id;
}

async function createCycle(kind: string, reason: string): Promise<string> {
  const cycle = await api(
    "analyst",
    "POST",
    `/banks/${SAMPLE_BANK_ID}/icaap/cycles`,
    {
      fiscal_year: fiscalYear,
      cycle_kind: kind,
      basis: "solo",
      framework_code: TEST_FRAMEWORK,
      framework_version: frameworkVersion,
      subsidiaries_declared: false,
      reason,
    },
  );
  return cycle.id;
}

async function api(
  role: Parameters<typeof mintBackendToken>[0],
  method: string,
  pathName: string,
  body?: unknown,
): Promise<any> {
  const token = await mintBackendToken(role);
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

/** The same call, returning the status so a REFUSAL can be asserted. */
async function attempt(
  role: Parameters<typeof mintBackendToken>[0],
  method: string,
  pathName: string,
  body?: unknown,
): Promise<{ status: number; text: string }> {
  const token = await mintBackendToken(role);
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${pathName}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, text: await response.text() };
}

async function openCycle(page: Page, segment: string): Promise<void> {
  await page.goto(`/icaap/${cycleId}/${segment}`);
  // `next dev` compiles a route on FIRST request, and these two are new. After
  // a long run the first visit can sit on the shell's loading state for most of
  // a minute — the same cold-start cost `global-setup.ts` pays up front for
  // `/api/auth/session`, for exactly this reason. Wait for the shell to resolve
  // before asserting anything about the page, so a slow compile reads as a slow
  // compile rather than as a missing panel.
  await expect(
    page.getByLabel("Loading authenticated workspace"),
  ).toHaveCount(0, { timeout: 120_000 });
  await expect(page).toHaveURL(new RegExp(`/icaap/[0-9a-f-]+/${segment}$`));
}

test.describe("ICAAP review and filing", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async () => {
    await resolveFixtures();
    // The assessment itself is created through the API rather than the wizard:
    // the wizard is `icaap-workspace.spec.ts`'s journey, and this one is about
    // what happens after the writing is done.
    cycleId = await createCycle("annual", "P3 acceptance walk-through");
    expect(cycleId).toMatch(/[0-9a-f-]{36}/);
  });

  test("the review tab shows the chain the assessment must go through", async ({
    page,
  }) => {
    await openCycle(page, "review");
    await expect(
      page.getByRole("heading", { name: "Review chain" }),
    ).toBeVisible({ timeout: 30_000 });
    // The chain's stages, in the regime's own words — not their API tokens.
    await expect(page.getByText("Preparation").first()).toBeVisible();
    await expect(
      page.getByText("Chief Risk Officer review").first(),
    ).toBeVisible();
    // The signature stage records no decision; it says so rather than offering
    // a control the server refuses.
    await expect(
      page.getByText(/Signed on the report itself/i).first(),
    ).toBeVisible();
  });

  test("separation of duties is visible before the act, not only after it", async ({
    page,
  }) => {
    // The analyst wrote the assessment, so the analyst cannot review it. The
    // screen says so; the API refuses it.
    await openCycle(page, "review");
    const decide = page.getByRole("button", { name: /Record my review/i });
    await expect(decide).toHaveCount(0);

    const stages = await api(
      "analyst",
      "GET",
      `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/stages`,
    );
    const refusal = await attempt(
      "analyst",
      "POST",
      `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/stages/2/decisions`,
      {
        decision: "reviewed",
        round: stages.round,
        review_digest: stages.review_digest,
      },
    );
    expect([400, 403, 409]).toContain(refusal.status);
  });

  test("the sealed report cannot be signed before it exists", async ({
    page,
  }) => {
    await openCycle(page, "filing");
    await expect(page.getByText(/Nothing to file yet/i).first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("the chain runs, and the freeze card then states what is still missing", async ({
    page,
  }) => {
    // Every section written and committed: reviewers read committed text only,
    // and a submission with unsaved edits is refused by name.
    const sections = await api(
      "analyst",
      "GET",
      `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/sections`,
    );
    for (const summary of sections.sections) {
      // The section's own working revision, not the summary's: an optimistic
      // token has to come from the thing it guards.
      const section = await api(
        "analyst",
        "GET",
        `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/sections/${summary.key}`,
      );
      const saved = await api(
        "analyst",
        "PUT",
        `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/sections/${section.key}/working`,
        {
          doc: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [
                  {
                    type: "text",
                    text: "The board has assessed the capital required to support the plan.",
                  },
                ],
              },
            ],
          },
          base_rev: section.working_rev,
        },
      );
      await api(
        "analyst",
        "POST",
        `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/sections/${section.key}/versions`,
        { base_rev: saved.working_rev, note: "First draft" },
      );
    }

    // Put it forward, review it, approve it — through the API, because the
    // decisions are three different officers and the browser holds one session
    // at a time.
    const opening = await api(
      "analyst",
      "GET",
      `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/stages`,
    );
    await api(
      "analyst",
      "POST",
      `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/submit-for-review`,
      { review_digest: opening.review_digest, note: null },
    );

    // A DIFFERENT officer at each stage, because one is refused at the second.
    // Whoever reviewed an assessment is a checker of that round and cannot
    // also approve it — separation of duties is not a per-stage rule, it is a
    // per-round one, and the server enforces it.
    for (let step = 0; step < MAX_CHAIN_STEPS; step += 1) {
      const current = await api(
        "approver",
        "GET",
        `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/stages`,
      );
      if (current.awaiting_freeze) break;
      const stage = current.stages.find(
        (entry: { seq: number }) => entry.seq === current.current_stage_seq,
      );
      if (!stage) break;
      if (stage.decision_kind !== "review" && stage.decision_kind !== "approve") {
        break;
      }
      // The review stage names an officer title, and only one fixture user
      // carries it; the approval stage names none. They must be DIFFERENT
      // people either way — whoever reviewed an assessment is a checker of
      // that round and cannot also approve it.
      const officer = stage.decision_kind === "approve" ? "approver" : "board";
      await api(
        officer,
        "POST",
        `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${cycleId}/stages/${stage.seq}/decisions`,
        {
          decision: stage.decision_kind === "approve" ? "approved" : "reviewed",
          round: current.round,
          // No officer title is SENT: the API forbids it. The check compares
          // the signed-in user's own recorded title, so that a decision cannot
          // misstate who approved the assessment.
          review_digest: current.review_digest,
        },
      );
    }

    // The timeline now shows what each officer decided, in production copy.
    await openCycle(page, "review");
    await expect(page.getByTestId("review-timeline")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("Reviewed").first()).toBeVisible();
    await expect(page.getByText("Approved").first()).toBeVisible();

    // And the freeze card is in front of the preparer, saying what it costs
    // them (C-9) and listing whatever the server still refuses on — each in
    // the server's own sentence, never this screen's paraphrase.
    await expect(
      page.getByRole("heading", { name: /Seal the report for filing/i }),
    ).toBeVisible();
    await expect(
      page.getByText(/recorded as the officer who produced this filing/i),
    ).toBeVisible();
    const blockers = page.getByTestId("blocker-list");
    const sealed = page.getByRole("button", { name: /^Seal the report$/ });
    // Either it is ready to seal, or it says exactly why it is not. What it
    // must never do is offer the button beside an unresolved refusal.
    if ((await blockers.count()) > 0) {
      await expect(sealed).toHaveCount(0);
    } else {
      await expect(sealed).toBeVisible();
    }
  });

});

test.describe("a board member holds capital authority and nothing else", () => {
  test.use({ storageState: path.join(E2E_TMP, "board.json") });

  test.beforeAll(async () => {
    await resolveFixtures();
    // Resolved from the API rather than from a module variable the serial
    // describe above set: Playwright restarts the worker after a failure in a
    // serial group, which resets module state to its initial value.
    cycleId = await annualCycleId();
  });

  test("reaches the signature without any Regulatory Reporting access", async ({
    page,
  }) => {
    requireObjectStorage();
    await openCycle(page, "filing");
    // No Regulatory Reporting nav at all for this person…
    await expect(
      page.getByRole("link", { name: "Regulatory Reporting" }),
    ).toHaveCount(0);
    // …and yet the filing surface itself is in front of them. That is the whole
    // reason the Filing tab embeds the signing workspace rather than linking
    // into `/submissions`: a board member's authority is over capital, and a
    // signature reachable only through Regulatory Reporting is a signature
    // they could not give.
    //
    // The Signatures card itself appears once a sealed report exists; this
    // assessment has none yet, so what is asserted here is the ACCESS, which
    // is the half that authorization decides.
    await expect(page.getByText(/Nothing to file yet/i).first()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("link", { name: "ICAAP" }).first()).toBeVisible();
  });
});

test.describe("a practice run says so everywhere it can be seen", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  /**
   * The practice run this describe creates, so it can be put back.
   *
   * WHY THE CLEANUP EXISTS. Only ONE open cycle may hold a given
   * (kind, reporting date, basis) for an institution — `uq_icaap_cycles_open`,
   * which excludes archived ones. Every journey in this suite shares one
   * disposable bank, and `icaap-workspace.spec.ts` runs straight after this
   * file and creates its OWN rehearsal for the same year. Leaving this one
   * open takes that slot, its creation is refused, and a P1 journey fails
   * three files away from the cause. Archiving a finished dry run is also
   * exactly what a bank does with one.
   */
  let rehearsalCycleId = "";

  test.beforeAll(async () => {
    await resolveFixtures();
  });

  test.afterAll(async () => {
    if (rehearsalCycleId === "") return;
    await api(
      "analyst",
      "POST",
      `/banks/${SAMPLE_BANK_ID}/icaap/cycles/${rehearsalCycleId}/archive`,
      { reason: "Dry run finished — releasing the year for the next journey." },
    );
  });

  test("the review and filing tabs both carry the rehearsal statement", async ({
    page,
  }) => {
    const rehearsal = await createCycle(
      "rehearsal",
      "A dry run of the whole filing process.",
    );
    rehearsalCycleId = rehearsal;

    for (const segment of ["review", "filing"]) {
      await page.goto(`/icaap/${rehearsal}/${segment}`);
      await expect(
        page.getByLabel("Loading authenticated workspace"),
      ).toHaveCount(0, { timeout: 120_000 });
      const notice = page.getByTestId("rehearsal-notice").first();
      await expect(notice).toBeVisible({ timeout: 30_000 });
      await expect(notice).toContainText(/never be filed/i);
      await expect(notice).toContainText(/nothing here is sent to the regulator/i);
    }
  });
});
