import { test, expect } from "@playwright/test";
import path from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import {
  instrumentIssuer,
  readableCalculation,
} from "../lib/regulatoryInstruments";
import { mintBackendToken } from "./support/mint";

const evidence = process.env.E2E_EVIDENCE_DIR;
const authorizationVersions: Record<string, number> = {};
test.use({
  storageState: path.join(E2E_TMP, "admin.json"),
  viewport: { width: 1440, height: 1000 },
});
test.setTimeout(120_000);

function fixture(action: string) {
  return execFileSync(
    path.join(__dirname, "../../.venv/bin/python"),
    [
      "dashboard/e2e/support/regulatory_fixture.py",
      path.join(E2E_TMP, "e2e.db"),
      action,
    ],
    {
      cwd: path.join(__dirname, "../.."),
      env: { ...process.env, PYTHONPATH: "." },
      encoding: "utf8",
    },
  ).trim();
}

async function readApi(route: string, role = "admin") {
  return fetch(`${E2E_API_ORIGIN}/api/v1${route}`, {
    headers: { Authorization: `Bearer ${await mintBackendToken(role)}` },
  });
}

async function api(
  method: string,
  route: string,
  body?: unknown,
  role = "admin",
) {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${await mintBackendToken(role, authorizationVersions[role])}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json();
  expect(response.ok, JSON.stringify(payload)).toBe(true);
  return payload;
}

test("register exposes every status, dates, source and calculation dependencies", async ({
  page,
}) => {
  const token = await mintBackendToken("admin");
  const response = await fetch(
    `${E2E_API_ORIGIN}/api/v1/regulatory-reporting/templates?bank_id=BK-SAMP0001`,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  expect(response.ok).toBeTruthy();
  const data = await response.json();
  await page.goto("/submissions/instruments");
  await expect(
    page.getByRole("heading", { name: "Regulatory instruments", exact: true }),
  ).toBeVisible();
  for (const item of data.templates) {
    const section = page.locator(`section[id="${item.code}"]`);
    await expect(section).toBeVisible();
    await expect(
      section.getByRole("heading", { name: item.title, exact: true }),
    ).toBeVisible();
    await section
      .getByText("Citation and calculations", { exact: true })
      .click();
    await expect(section).toContainText(item.directive_citation);
    await expect(section).toContainText(
      item.effective_from ??
        (item.instrument_status === "unpublished"
          ? "No filing commencement date"
          : `Start date not published by ${instrumentIssuer(item.regulator)}`),
    );
    for (const calculation of item.calculations)
      await expect(section).toContainText(readableCalculation(calculation));
    await section
      .getByText("Citation and calculations", { exact: true })
      .click();
  }
  const draft = page.locator('section[id="LMT"]');
  await draft.locator("summary").first().click();
  await expect(draft.getByRole("tooltip")).toContainText(
    /Proposed by BoG, not law yet. Prepare if useful, no deadline or penalty./i,
  );
  await expect(draft.getByRole("tooltip")).toContainText(
    "Expected from 2027-01-01 if made final.",
  );
  await expect(
    draft.getByRole("tooltip").getByRole("link", { name: "Read the source" }),
  ).toHaveAttribute("href", /^https:/);
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    writeFileSync(
      path.join(evidence, "regulatory-templates.json"),
      JSON.stringify(data, null, 2),
    );
    await page.screenshot({
      path: path.join(evidence, "instruments-register.png"),
      fullPage: true,
    });
    await draft.screenshot({
      path: path.join(evidence, "draft-status-source.png"),
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(evidence, "instruments-register-mobile.png"),
      fullPage: true,
    });
  }
});

test("upcoming rules isolate preparation and preserve navigation into a gated module", async ({
  page,
}) => {
  await page.goto("/submissions/upcoming");
  await expect(
    page.getByRole("heading", {
      name: "Preparing for upcoming rules",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator('section[id="LMT"]')).toBeVisible();
  await expect(page.locator('section[id="LE-MONTHLY"]')).toContainText(
    "Not yet in force",
  );
  await expect(page.locator('section[id="CAR-RWA"]')).toHaveCount(0);
  await expect(page.locator('section[id="LCR-NSFR"]')).toHaveCount(0);
  const irr = page.locator('section[id="IRRBB-PILOT"]');
  await irr.getByText("Citation and calculations", { exact: true }).click();
  await irr.getByRole("link", { name: "Open preparation workspace" }).click();
  await expect(page).toHaveURL(/\/irr\/standardised\?preparing=1/);
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "irr-preparation.png"),
      fullPage: true,
    });
  await page.goto("/irr/standardised");
  await expect(
    page.getByText(
      "This capability is available from Preparing for upcoming rules.",
    ),
  ).toBeVisible();
});

test("ordinary returns and calendar exclude draft, reference and deferred rules", async ({
  page,
}) => {
  await page.goto("/submissions/returns");
  await expect(
    page.getByRole("heading", { name: /Returns workspace/i }),
  ).toBeVisible();
  const picker = page.getByRole("combobox").first();
  for (const code of [
    "LMT",
    "LCR-NSFR",
    "IRRBB-PILOT",
    "ICAAP-REPORT",
    "LE-MONTHLY",
  ]) {
    await expect(
      picker.locator("option", { hasText: new RegExp(`^${code} `) }),
    ).toHaveCount(0);
  }
  await expect(picker.locator('option[value="CAR-RWA"]')).toHaveCount(1);
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "in-force-returns.png"),
      fullPage: true,
    });
  await page.goto("/submissions/calendar");
  await expect(page.getByRole("table")).toBeVisible();
  const table = page.getByRole("table");
  for (const code of [
    "LMT",
    "LCR-NSFR",
    "IRRBB-PILOT",
    "ICAAP-REPORT",
    "LE-MONTHLY",
  ]) {
    await expect(table.getByText(code, { exact: true })).toHaveCount(0);
  }
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "in-force-calendar.png"),
      fullPage: true,
    });
});

test("draft-based stress export discloses preparation status at the PDF decision", async ({
  page,
}) => {
  await page.goto("/reports/stress-board-pack");
  const print = page.getByRole("button", { name: /Print|PDF/i }).first();
  await expect(page.getByRole("note")).toHaveCount(1);
  await expect(page.getByRole("note")).not.toContainText("BOG");
  await expect(print).toBeVisible();
  await expect(
    page.getByText(/This pack is for preparation or monitoring/).first(),
  ).toBeVisible();
  await expect(
    page.getByText(/Proposed by BoG, not law yet/i).first(),
  ).toBeVisible();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "stress-export-warning.png"),
      fullPage: true,
    });
});

test("saved reference liquidity failures disappear from presentation while capital failures and stored evidence survive", async ({
  page,
}) => {
  fixture("forecast");
  const observations = [];
  for (const [id, capitalFailed] of [
    ["bbbbbbbb-1111-4111-8111-111111111111", false],
    ["bbbbbbbb-2222-4222-8222-222222222222", true],
  ] as const) {
    const response = await readApi(`/banks/BK-SAMP0001/forecast/runs/${id}`);
    expect(response.status).toBe(200);
    const run = await response.json();
    expect(
      run.validations
        .filter((v: any) => !v.passed)
        .map((v: any) => v.rule_code),
    ).toEqual(capitalFailed ? ["year5_car_above_minimum"] : []);
    expect(
      run.metric_results
        .filter((m: any) => m.metric_code !== "year5_car_pct")
        .every((m: any) => m.status === "na"),
    ).toBe(true);
    observations.push(run);
    await page.goto(`/forecasting?run=${id}`);
    await expect(
      page.getByText("Recorded CAR assessment", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Recorded LCR assessment", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Recorded NSFR assessment", { exact: true }),
    ).toHaveCount(0);
    if (evidence)
      await page.screenshot({
        path: path.join(
          evidence,
          `saved-forecast-${capitalFailed ? "capital-breach" : "reference-only"}.png`,
        ),
        fullPage: true,
      });
  }
  const alerts = await (await readApi("/banks/BK-SAMP0001/alerts")).json();
  expect(
    alerts.items.some(
      (item: any) => item.rule_id === "year5_car_above_minimum",
    ),
  ).toBe(true);
  expect(
    alerts.items.some((item: any) =>
      ["year5_lcr_above_minimum", "year5_nsfr_above_minimum"].includes(
        item.rule_id,
      ),
    ),
  ).toBe(false);
  const immutable = fixture("immutable");
  if (evidence)
    writeFileSync(
      path.join(evidence, "saved-forecast-presentation.json"),
      JSON.stringify(
        { observations, alerts, persisted: JSON.parse(immutable) },
        null,
        2,
      ),
    );
});

test("slow and failed initial metadata never affirm compliance", async ({
  page,
}) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let intercepted = 0;
  let aborted = 0;
  let fail = false;
  const metadataUrl = (url: URL) =>
    url.pathname.endsWith("/regulatory-reporting/templates");
  await page.route(metadataUrl, async (route) => {
    intercepted++;
    if (fail) {
      aborted++;
      await route.abort("connectionrefused");
      return;
    }
    await held;
    await route.continue();
  });
  await page.goto("/");
  await expect.poll(() => intercepted).toBeGreaterThan(0);
  await expect(page.getByLabel("Loading limit compliance")).toBeVisible();
  await expect(
    page.getByText("All limits compliant", { exact: true }),
  ).toHaveCount(0);
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "metadata-pending.png"),
      fullPage: true,
    });
  release();
  fail = true;
  await page.reload();
  await expect.poll(() => aborted).toBeGreaterThan(0);
  await expect(
    page.getByText("Limit compliance not assessed", { exact: true }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByText("All limits compliant", { exact: true }),
  ).toHaveCount(0);
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "metadata-unavailable.png"),
      fullPage: true,
    });
});

test("governed dates disclose approval and deny another bank's uncovered reader", async ({
  page,
}) => {
  fixture("governance");
  const own = await readApi(
    "/regulatory-reporting/templates?bank_id=BK-SAMP0001",
    "liquidity_viewer",
  );
  expect(own.status).toBe(200);
  const forbidden = await readApi(
    "/regulatory-reporting/templates?bank_id=BK-RULE0002",
    "liquidity_viewer",
  );
  expect(forbidden.status).toBe(404);
  const defaults = await readApi(
    "/regulatory-reporting/templates",
    "liquidity_viewer",
  );
  expect(defaults.status).toBe(200);
  const publicMetadata = await defaults.json();
  expect(
    publicMetadata.templates.every(
      (item: any) => item.commencement_approved_by === null,
    ),
  ).toBe(true);
  await page.goto("/submissions/instruments");
  const icaap = page.locator('section[id="ICAAP-REPORT"]');
  await icaap.getByText("Citation and calculations", { exact: true }).click();
  await expect(icaap).toContainText("Governed date differs from the default.");
  await expect(icaap).toContainText("2028-12-31");
  await expect(icaap).toContainText("Disposable commencement approver");
  await expect(icaap).toContainText("Draft");
  if (evidence) {
    await icaap.screenshot({
      path: path.join(evidence, "governed-commencement.png"),
    });
    writeFileSync(
      path.join(evidence, "bank-coverage.json"),
      JSON.stringify(
        {
          ownBankStatus: own.status,
          otherBankStatus: forbidden.status,
          otherBankResponse: await forbidden.json(),
          publicDefaults: publicMetadata,
        },
        null,
        2,
      ),
    );
  }
});

test("foreign active regulators do not relabel instrument evidence and final frameworks remain available", async ({
  page,
}) => {
  try {
    for (const [jurisdiction, regulator] of [
      ["KE", "CBK"],
      ["NG", "CBN"],
    ]) {
      fixture(jurisdiction);
      await page.goto("/submissions/instruments");
      const draft = page.locator('section[id="LMT"]');
      await draft.locator("summary").first().click();
      await expect(draft.getByRole("tooltip")).toContainText(
        /Proposed by BoG/i,
      );
      await expect(draft.getByRole("tooltip")).not.toContainText(
        `Proposed by ${regulator}`,
      );
      if (evidence)
        await draft.screenshot({
          path: path.join(evidence, `${jurisdiction}-active-bog-draft.png`),
        });
      await page.goto("/icaap");
      await expect(
        page.getByRole("button", { name: "New cycle" }),
      ).toBeEnabled();
      await expect(page.getByText("In force", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "New cycle" }).click();
      const dialog = page.getByRole("dialog", { name: "New ICAAP cycle" });
      await expect(dialog).toContainText(`Issued by ${regulator}`);
      await expect(dialog.getByText("In force", { exact: true })).toBeVisible();
      if (evidence)
        await page.screenshot({
          path: path.join(evidence, `${jurisdiction}-final-framework.png`),
          fullPage: true,
        });
    }
  } finally {
    fixture("GH");
  }
});

test("daily status scan delivers commencement and change notices once with a bank-preserving register link", async ({
  page,
}) => {
  const scan = JSON.parse(fixture("notify"));
  expect(scan.reminder_emitted).toBeGreaterThan(0);
  expect(scan.repeat_emitted).toBe(0);
  expect(scan.commencement_emitted).toBeGreaterThan(0);
  const response = await readApi("/notifications");
  expect(response.status).toBe(200);
  const feed = await response.json();
  const notices = feed.notifications.filter((item: any) =>
    item.type.includes(":LE-MONTHLY:"),
  );
  expect(
    notices.some((item: any) =>
      item.type.startsWith("reporting.instrument.commencement_30"),
    ),
  ).toBe(true);
  expect(
    notices.some((item: any) =>
      item.type.startsWith("reporting.instrument.changed"),
    ),
  ).toBe(true);
  await page.goto("/submissions/instruments");
  await page.getByRole("button", { name: /^Notifications/ }).click();
  const inbox = page.getByRole("dialog", {
    name: "Notifications",
    exact: true,
  });
  await inbox.getByRole("button", { name: /Inbox/ }).click();
  await expect(
    inbox.getByText(
      "Sample Bank Ltd · Large Exposures Return (Templates 1/1a/2/3/4): rule status changed",
      {
        exact: true,
      },
    ),
  ).toBeVisible();
  if (evidence) {
    await page.screenshot({
      path: path.join(evidence, "instrument-notifications.png"),
      fullPage: true,
    });
    writeFileSync(
      path.join(evidence, "instrument-notifications.json"),
      JSON.stringify({ scan, notices }, null, 2),
    );
  }
  await inbox
    .getByText(
      "Sample Bank Ltd · Large Exposures Return (Templates 1/1a/2/3/4): rule status changed",
      { exact: true },
    )
    .click();
  await expect(page).toHaveURL(
    /\/submissions\/instruments\?bankId=BK-SAMP0001/,
  );
});

test("a mounted calendar acquires the newly commenced obligation on metadata polling", async ({
  page,
}) => {
  const clockFile = path.join(E2E_TMP, "regulatory-clock.json");
  try {
    writeFileSync(clockFile, JSON.stringify({ today: "2026-12-31" }));
    await page.goto("/submissions/calendar");
    await expect(page.getByRole("table")).toBeVisible();
    const before = await (
      await readApi("/banks/BK-SAMP0001/reporting-obligations?horizon_months=3")
    ).json();
    expect(
      before.obligations.some((item: any) => item.return_code === "LE-MONTHLY"),
    ).toBe(false);
    writeFileSync(clockFile, JSON.stringify({ today: "2027-01-01" }));
    const response = await page.waitForResponse(
      (res) => res.url().includes("/reporting-obligations") && res.ok(),
      { timeout: 80_000 },
    );
    const after = await response.json();
    expect(after.as_of).toBe("2027-01-01");
    expect(after.total).toBeGreaterThan(before.total);
    const allAfter = await (
      await readApi("/banks/BK-SAMP0001/reporting-obligations?horizon_months=3")
    ).json();
    expect(
      allAfter.obligations.some(
        (item: any) => item.return_code === "LE-MONTHLY",
      ),
    ).toBe(true);
    if (evidence) {
      await page.screenshot({
        path: path.join(evidence, "calendar-after-commencement.png"),
        fullPage: true,
      });
      writeFileSync(
        path.join(evidence, "mounted-calendar-commencement.json"),
        JSON.stringify({ before, refreshedPage: after, allAfter }, null, 2),
      );
    }
  } finally {
    writeFileSync(clockFile, JSON.stringify({ today: "2026-12-31" }));
  }
});

test("ICAAP preparation preserves context and its own draft evidence at alternate Word and PDF downloads", async ({
  page,
}) => {
  const frameworks = await (
    await readApi("/banks/BK-SAMP0001/icaap/frameworks")
  ).json();
  const framework = frameworks.frameworks.find(
    (item: any) => item.code === "bog_icaap",
  );
  const response = await fetch(
    `${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001/icaap/cycles`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${await mintBackendToken("admin")}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        fiscal_year: 2029,
        cycle_kind: "rehearsal",
        basis: "solo",
        framework_code: framework.code,
        framework_version: framework.version,
        reason: "Regulatory legal-status export journey",
      }),
    },
  );
  expect(response.status).toBe(201);
  const cycle = await response.json();
  await page.goto(`/icaap/${cycle.id}/overview?preparing=1`);
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
  await page.getByRole("link", { name: "Sections", exact: true }).click();
  await expect(page).toHaveURL(/\/sections\?preparing=1/);
  await expect(
    page.getByText(/Proposed by BoG, not law yet/i).first(),
  ).toBeVisible();
  const pdf = page.getByRole("button", { name: "Draft PDF", exact: true });
  const word = page.getByRole("button", {
    name: "Draft Word (working copy)",
    exact: true,
  });
  await expect(pdf).toBeEnabled();
  await expect(word).toBeEnabled();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "icaap-sections-export-warning.png"),
      fullPage: true,
    });
  for (const [button, extension] of [
    [pdf, "pdf"],
    [word, "docx"],
  ] as const) {
    const download = page.waitForEvent("download", { timeout: 60_000 });
    await button.click();
    const file = await download;
    expect(file.suggestedFilename()).toContain("DRAFT");
    if (evidence)
      await file.saveAs(
        path.join(evidence, `icaap-legal-status-DRAFT.${extension}`),
      );
  }
  await page.getByRole("link", { name: "ICAAP", exact: true }).last().click();
  await expect(page).toHaveURL(/\/icaap\?preparing=1/);
  await expect(page.getByRole("button", { name: "New cycle" })).toBeVisible();
});

test("status tooltip and source stay readable inside the viewport", async ({
  page,
}) => {
  for (const width of [1440, 768, 390]) {
    // Resize before loading: a shell remount can close native disclosures.
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/submissions/instruments");
    const draft = page.locator('section[id="LMT"]');
    await draft.locator("summary").first().click();
    const tooltip = draft.getByRole("tooltip");
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText(/Proposed by BoG, not law yet\./i);
    const source = tooltip.getByRole("link", { name: "Read the source" });
    await expect(source).toHaveAttribute("href", /^https:/);
    await expect(async () => {
      for (const element of [tooltip, source]) {
        const bounds = await element.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      }
    }).toPass();
    if (evidence) {
      await page.screenshot({
        path: path.join(evidence, `status-tooltip-viewport-${width}.png`),
      });
      writeFileSync(
        path.join(evidence, `status-tooltip-bounds-${width}.json`),
        JSON.stringify(
          {
            viewport: page.viewportSize(),
            tooltip: await tooltip.boundingBox(),
          },
          null,
          2,
        ),
      );
    }
  }
});

test("an ICAAP rehearsal freeze decision warns that the practice report cannot become a filing", async ({
  page,
}) => {
  authorizationVersions.viewer = JSON.parse(fixture("freeze-officer")).authv;
  const frameworks = await api("GET", "/banks/BK-SAMP0001/icaap/frameworks");
  const framework = frameworks.frameworks.find(
    (item: any) => item.code === "bog_icaap",
  );
  const cycle = await api("POST", "/banks/BK-SAMP0001/icaap/cycles", {
    fiscal_year: 2030,
    cycle_kind: "rehearsal",
    basis: "solo",
    framework_code: framework.code,
    framework_version: framework.version,
    reason: "Rehearsal freeze legal-status journey",
  });
  const root = `/banks/BK-SAMP0001/icaap/cycles/${cycle.id}`;
  const sections = await api("GET", `${root}/sections`);
  for (const summary of sections.sections) {
    const section = await api("GET", `${root}/sections/${summary.key}`);
    const saved = await api("PUT", `${root}/sections/${summary.key}/working`, {
      doc: {
        type: "doc",
        content: [
          {
            type: "paragraph",
            content: [
              {
                type: "text",
                text: "The board has assessed capital needs in this disposable rehearsal.",
              },
            ],
          },
        ],
      },
      base_rev: section.working_rev,
    });
    await api("POST", `${root}/sections/${summary.key}/versions`, {
      base_rev: saved.working_rev,
      note: "Rehearsal evidence",
    });
  }
  const opening = await api("GET", `${root}/stages`);
  await api("POST", `${root}/submit-for-review`, {
    review_digest: opening.review_digest,
    note: null,
  });
  for (let step = 0; step < 20; step++) {
    const current = await api("GET", `${root}/stages`, undefined, "approver");
    if (current.awaiting_freeze) break;
    const stage = current.stages.find(
      (item: any) => item.seq === current.current_stage_seq,
    );
    expect(["review", "approve"]).toContain(stage.decision_kind);
    await api(
      "POST",
      `${root}/stages/${stage.seq}/decisions`,
      {
        decision: stage.decision_kind === "approve" ? "approved" : "reviewed",
        round: current.round,
        review_digest: current.review_digest,
      },
      stage.decision_kind === "approve"
        ? "viewer"
        : stage.seq === 2
          ? "board"
          : "approver",
    );
  }
  await page.goto(`/icaap/${cycle.id}/review?preparing=1`);
  await expect(
    page.getByRole("heading", {
      name: "Seal the report for filing",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText(/You are sealing a practice run/)).toBeVisible();
  await expect(
    page.getByText(
      /can never be sent to the regulator and never counts towards a filing deadline/,
    ),
  ).toBeVisible();
  await expect(
    page.getByText(/Proposed by BoG, not law yet/i).first(),
  ).toBeVisible();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "icaap-rehearsal-freeze-warning.png"),
      fullPage: true,
    });
});

test("a 16% SDI exposure uses the statutory 25% ceiling until the 15% directive commences", async ({
  page,
}) => {
  fixture("sdi");
  const beforeResponse = await readApi(
    "/banks/BK-RULESDI1/sdi/large-exposures?as_of=2026-12-31",
  );
  expect(beforeResponse.status).toBe(200);
  const before = await beforeResponse.json();
  expect(before.exposures).toHaveLength(1);
  expect(Number(before.exposures[0].pct_net_own_funds)).toBe(16);
  expect(Number(before.exposures[0].applicable_limit_pct)).toBe(25);
  expect(before.exposures[0].status).toBe("ok");
  await page.goto("/risk");
  await expect(
    page.getByRole("img", { name: "16% of 25% limit", exact: true }),
  ).toBeVisible();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "sdi-statutory-controls.png"),
      fullPage: true,
    });
  fixture("sdi-commence");
  const after = await (
    await readApi("/banks/BK-RULESDI1/sdi/large-exposures?as_of=2027-01-01")
  ).json();
  expect(Number(after.exposures[0].applicable_limit_pct)).toBe(15);
  expect(after.exposures[0].status).toBe("above_limit");
  if (evidence)
    writeFileSync(
      path.join(evidence, "sdi-exposure-commencement.json"),
      JSON.stringify({ before, after }, null, 2),
    );
});
