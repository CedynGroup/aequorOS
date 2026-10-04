// Data ingestion through the Data Engine: every downstream figure starts here.
//
// Needs object storage (uploads are staged in the bank's encrypted temp tier);
// see `support/object-storage.ts`. Set E2E_EVIDENCE_DIR to keep screenshots.
//
// Isolation. These journeys write canonical data into the canonical test bank
// every other journey reads, so each upload is dated where no other journey
// looks. Position snapshots are read by EXACT as-of date, and 2026-03-15 is a
// Sunday — no return anchors on it — so the positions below never enter
// another journey's figures. The uploads carry no GL account or reference row,
// which are read as of "on or before" a date. Market data is also read "on or
// before", so the curve is dated after any date a journey can ask for.
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "fs";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";

const AS_OF = "2026-03-15";
const CURVE_AS_OF = "2099-12-31";
const FIXTURES = path.join(__dirname, "fixtures", "data-engine");
const BANK_API = `${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001`;
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

async function evidence(page: Page, name: string): Promise<void> {
  if (!evidenceDir) return;
  mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({
    path: path.join(evidenceDir, `data-engine-${name}.png`),
    fullPage: true,
  });
}

/** The value under one label of a batch's count strip (Extracted, Accepted, ...). */
function count(scope: Locator, label: string) {
  return scope
    .locator("p", { hasText: new RegExp(`^${label}$`) })
    .locator("xpath=following-sibling::p[1]");
}

async function expectCounts(
  scope: Locator,
  expected: Record<string, number>,
): Promise<void> {
  for (const [label, value] of Object.entries(expected)) {
    await expect(count(scope, label), label).toHaveText(String(value));
  }
}

async function uploadAndIngest(page: Page, file: string): Promise<void> {
  const panel = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Upload & ingest" }) });
  await panel.getByLabel("Source files (.xlsx / .csv)").setInputFiles(file);
  await panel.getByLabel("As-of date").fill(AS_OF);
  await panel.getByRole("button", { name: "Upload & ingest" }).click();
}

function uploadOutcome(page: Page, filename: string) {
  return page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Upload & ingest" }) })
    .locator("div.p-4")
    .filter({ hasText: filename });
}

test.describe("Data Engine ingestion", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });
  test.beforeAll(() => requireObjectStorage());

  test("an Excel/CSV upload is parsed into a batch, reviewed, corrected and reaches the position book", async ({
    page,
    request,
  }) => {
    test.setTimeout(150_000);

    await test.step("activate the canonical passthrough mapping", async () => {
      await page.goto("/data-engine/excel-csv");
      const template = page.locator("div.rounded.border").filter({
        has: page.getByText("Canonical passthrough (API field names)", {
          exact: true,
        }),
      });
      await template.getByRole("button", { name: "Activate mapping" }).click();
      await expect(
        template.getByRole("button", { name: "Currently active" }),
      ).toBeDisabled();
      await expect(
        page
          .getByText("Active mapping configuration")
          .locator("..")
          .getByText("Canonical passthrough (API field names)"),
      ).toBeVisible();
    });

    await test.step("a legacy .xls workbook is refused with the re-save guidance", async () => {
      // The refusal is decided by the format, not by the bytes: an .xls is
      // never opened, so placeholder bytes stand in for the old workbook.
      const panel = page.locator("section").filter({
        has: page.getByRole("heading", { name: "Upload & ingest" }),
      });
      await panel.getByLabel("Source files (.xlsx / .csv)").setInputFiles({
        name: "positions.xls",
        mimeType: "application/vnd.ms-excel",
        buffer: Buffer.from("legacy BIFF workbook"),
      });
      await panel.getByLabel("As-of date").fill(AS_OF);
      await panel.getByRole("button", { name: "Upload & ingest" }).click();

      const outcome = uploadOutcome(page, "positions.xls");
      await expect(outcome.getByText("failed", { exact: true })).toBeVisible();
      await expect(
        outcome.getByText(
          "Legacy .xls workbooks are not supported; save the file as .xlsx and retry.",
        ),
      ).toBeVisible();
      await expectCounts(outcome, { Extracted: 0, Accepted: 0 });
      await evidence(page, "legacy-xls-refused");
    });

    await test.step("a workbook with bad rows is parsed into a batch", async () => {
      await uploadAndIngest(page, path.join(FIXTURES, "positions.csv"));
      const outcome = uploadOutcome(page, "positions.csv");
      await expect(
        outcome.getByText("accepted with warnings", { exact: true }),
      ).toBeVisible();
      await expectCounts(outcome, {
        Extracted: 4,
        Translated: 3,
        Accepted: 2,
        Errors: 1,
      });
      await expect(outcome).toContainText("positions→ position (4)");
      await evidence(page, "upload-with-row-errors");
      await outcome.getByRole("link", { name: "Batch detail" }).click();
    });

    await test.step("the batch shows accepted and rejected rows with row-level errors", async () => {
      await expect(page).toHaveURL(/\/data-engine\/batches\/[^/]+$/);
      const header = page.getByRole("heading", { name: /Ingestion batch/ });
      await expect(header.getByText("accepted with warnings")).toBeVisible();
      await expect(page.getByText(`EXCEL_CSV · as of ${AS_OF}`)).toBeVisible();
      await expectCounts(page.locator("main"), {
        Extracted: 4,
        Accepted: 2,
        Errors: 1,
      });

      const table = page
        .locator(".card")
        .filter({
          has: page.getByRole("heading", { name: "Tables in this upload" }),
        })
        .getByRole("row", { name: /positions/ });
      await expect(table.getByRole("cell")).toHaveText([
        "positions",
        "position",
        "4",
        "2",
        "0",
        "1",
      ]);

      const findings = page.locator("section").filter({
        has: page.getByRole("heading", { name: "Validation findings" }),
      });
      const rateFinding = findings
        .locator("div.px-5")
        .filter({ hasText: "position_rate_bounds" });
      await expect(
        rateFinding.getByText("ERROR", { exact: true }),
      ).toBeVisible();
      await expect(
        rateFinding.getByText("interest_rate=-0.19 outside [0, 1]."),
      ).toBeVisible();
      await expect(rateFinding).toContainText(
        "position_rate_bounds · E2E-ING-003 · positions.csv#positions!R4",
      );

      const untranslatable = page.locator("section").filter({
        has: page.getByRole("heading", { name: "Untranslatable rows" }),
      });
      await expect(untranslatable.getByText("coercion_error")).toBeVisible();
      await expect(
        untranslatable.getByText("positions.csv#positions!R5"),
      ).toBeVisible();
      await expect(
        untranslatable.getByText(
          "balance (column 'balance'): Cannot read '37S000.00' as money: not a number after cleanup",
        ),
      ).toBeVisible();
      await expect(untranslatable.locator("pre")).toContainText(
        '"source_reference":"E2E-ING-004"',
      );
      await evidence(page, "batch-review");
    });

    await test.step("the corrected workbook is re-uploaded and accepted", async () => {
      await page.goto("/data-engine/excel-csv");
      await uploadAndIngest(
        page,
        path.join(FIXTURES, "positions-corrected.xlsx"),
      );
      const outcome = uploadOutcome(page, "positions-corrected.xlsx");
      // Still "with warnings": the passthrough mapping also expects GL,
      // counterparty and product tables, and their absence is reported.
      await expect(
        outcome.getByText("accepted with warnings", { exact: true }),
      ).toBeVisible();
      await expectCounts(outcome, {
        Extracted: 4,
        Translated: 4,
        Accepted: 4,
        Warnings: 0,
        Errors: 0,
      });

      const history = page
        .locator("section")
        .filter({
          has: page.getByRole("heading", { name: "File ingestion history" }),
        })
        .getByRole("row");
      await expect(history.filter({ hasText: "positions.csv" })).toHaveCount(1);
      await expect(
        history.filter({ hasText: "positions-corrected.xlsx" }),
      ).toHaveCount(1);
      // A refused file is never stored, so its row names the channel, not the file.
      await expect(
        history.filter({ hasText: AS_OF }).filter({ hasText: "failed" }),
      ).toHaveCount(1);
      await evidence(page, "corrected-upload");
    });

    await test.step("the ingested positions reach the canonical position book", async () => {
      await page.goto("/data-engine/positions");
      const corrected = page.getByRole("row", { name: /E2E-ING-003/ });
      await expect(corrected.getByRole("cell")).toHaveText([
        "",
        "E2E-ING-003",
        "LOAN",
        "GHS",
        "450,000",
        "19.00%",
        "2027-02-10",
        AS_OF,
        "accepted",
      ]);
      // E2E-ING-004 could not be read from the CSV, so it first entered the
      // book through the corrected workbook, and its lineage says so.
      const recovered = page.getByRole("row", { name: /E2E-ING-004/ });
      await expect(recovered.getByRole("cell").nth(4)).toHaveText("375,000");
      await recovered.click();
      await expect(page.getByText("ADAPTER EXTRACT")).toBeVisible();
      await expect(
        page.getByText(
          /^excel_csv_v1\.0\/temp:\/\/uploads\/.+\/positions-corrected\.xlsx/,
        ),
      ).toBeVisible();
      await evidence(page, "positions-downstream");

      const response = await request.get(`${BANK_API}/canonical-positions`, {
        headers: { Authorization: `Bearer ${await mintBackendToken("admin")}` },
        params: { q: "E2E-ING-", as_of_date: AS_OF },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      const book = await response.json();
      expect(
        book.positions.map(
          (position: {
            source_reference: string;
            validation_status: string;
            balance: string;
            interest_rate: string;
          }) => [
            position.source_reference,
            position.validation_status,
            Number(position.balance),
            Number(position.interest_rate),
          ],
        ),
      ).toEqual([
        ["E2E-ING-001", "accepted", 1_250_000, 0.215],
        ["E2E-ING-002", "accepted", 800_000, 0.08],
        ["E2E-ING-003", "accepted", 450_000, 0.19],
        ["E2E-ING-004", "accepted", 375_000, 0.175],
      ]);
    });
  });

  test("a manual market data upload lands its curve and names the rows it could not use", async ({
    page,
  }) => {
    test.setTimeout(120_000);

    await page.goto("/data-engine/market-data");
    const form = page
      .locator("form")
      .filter({ has: page.getByRole("button", { name: "Upload" }) });
    await form
      .getByLabel("File (.xlsx / .csv)")
      .setInputFiles(path.join(FIXTURES, "yield_curve.csv"));
    await form.getByLabel("As-of date").fill(CURVE_AS_OF);
    await form.getByRole("button", { name: "Upload" }).click();

    await expect(
      page.getByText("Batch accepted — 3 canonical records across 1 scope"),
    ).toBeVisible();
    await expect(
      page.getByText("YIELD_CURVE_GHS", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("yield_curve row 4: tenor_months must be positive, got 0"),
    ).toBeVisible();
    await evidence(page, "market-data-upload");

    await page.goto("/data-engine");
    const batch = page
      .getByRole("row")
      .filter({ hasText: "MANUAL_UPLOAD" })
      .filter({ hasText: CURVE_AS_OF });
    await expect(batch.getByText("accepted", { exact: true })).toBeVisible();
    await batch.getByRole("link", { name: "Detail" }).click();
    await expect(
      page.getByText(`MANUAL_UPLOAD · as of ${CURVE_AS_OF}`),
    ).toBeVisible();
    await expectCounts(page.locator("main"), { Accepted: 3, Errors: 0 });
  });

  test("a configured T24 core's close-of-business extract lands in the position book", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);

    await test.step("configure the core; live transport stays honestly unavailable", async () => {
      await page.goto("/data-engine/t24");
      await page
        .getByRole("button", { name: "Configure a core" })
        .first()
        .click();
      await page.getByRole("button", { name: /^OFS/ }).click();
      await page.getByLabel("Display name").fill("Journey T24 core");
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByLabel("Endpoint")).toHaveValue(
        "ofs://core.bank.internal",
      );
      await page.getByLabel("Companies / entities").fill("GH0010001");
      await page.getByRole("button", { name: "Continue" }).click();
      await page.getByLabel("Service user", { exact: true }).fill("AEQ.SVC");
      await page
        .getByLabel("Service user password")
        .fill("journey-only-secret");
      await page.getByRole("button", { name: "Continue" }).click();
      await page.getByRole("button", { name: "Continue" }).click();
      await page.getByRole("button", { name: "Save configuration" }).click();

      await expect(
        page.getByText(
          "OFS configuration saved. Live transport remains unavailable.",
        ),
      ).toBeVisible();
      await expect(
        page.getByText(
          /^Live Temenos connectivity is not available in this deployment\./,
        ),
      ).toBeVisible();
      await page.getByRole("button", { name: "Done" }).click();
      const card = page.locator("section.card").filter({
        has: page.getByRole("heading", { name: "Journey T24 core" }),
      });
      await expect(card.getByText("Live transport unavailable")).toBeVisible();
      await evidence(page, "t24-configured");
    });

    await test.step("the close-of-business extract runs through the T24 adapter", async () => {
      // Live T24 transport is blocked in every deployment, so a pull can never
      // fetch this bundle. It is the recorded extract a pull stages, and from
      // here the journey makes exactly the calls `pull_and_ingest` makes after
      // its transport returns: stage the bundle, then ingest it as T24 with the
      // mapping onboarding seeded for the core configured above. The bundle was
      // built by `fetch_domains`/`build_bundle` from the adapter's recorded OFS
      // contract fixtures, without the GL domain (see the isolation note).
      const headers = {
        Authorization: `Bearer ${await mintBackendToken("admin")}`,
      };
      const staged = await request.post(`${BANK_API}/ingestion-uploads`, {
        headers,
        multipart: {
          file: {
            name: "t24-ofs-2026-03-15.json",
            mimeType: "application/json",
            buffer: readFileSync(
              path.join(FIXTURES, "t24-ofs-2026-03-15.json"),
            ),
          },
        },
      });
      expect(staged.status(), await staged.text()).toBe(201);
      const ingested = await request.post(`${BANK_API}/ingestion-batches`, {
        headers,
        data: {
          source_system: "T24",
          as_of_date: AS_OF,
          location: (await staged.json()).location,
          reason: "T24 close-of-business extract (journey)",
        },
      });
      expect(ingested.status(), await ingested.text()).toBe(201);
    });

    await test.step("the batch and its positions are visible downstream", async () => {
      await page.goto("/data-engine");
      const batch = page
        .getByRole("row")
        .filter({ hasText: "t24-ofs-2026-03-15.json" });
      await expect(batch.getByText("T24", { exact: true })).toBeVisible();
      await expect(batch.getByText("accepted", { exact: true })).toBeVisible();
      await batch.getByRole("link", { name: "Detail" }).click();

      await expect(page.getByText(`T24 · as of ${AS_OF}`)).toBeVisible();
      await expectCounts(page.locator("main"), {
        Extracted: 8,
        Accepted: 8,
        Errors: 0,
      });
      const tables = page.locator(".card").filter({
        has: page.getByRole("heading", { name: "Tables in this upload" }),
      });
      for (const [source, entity, rows] of [
        ["COUNTERPARTY_MASTER", "counterparty", "2"],
        ["PRODUCT_MASTER", "product", "3"],
        ["POSITIONS_LOANS", "position", "2"],
        ["POSITIONS_DEPOSITS", "position", "1"],
      ]) {
        await expect(
          tables
            .getByRole("row", { name: new RegExp(source) })
            .getByRole("cell"),
        ).toHaveText([source, entity, rows, rows, "0", "0"]);
      }
      await evidence(page, "t24-batch");

      await page.goto("/data-engine/positions");
      const loan = page.getByRole("row", { name: /AA25001/ });
      await expect(loan.getByRole("cell")).toHaveText([
        "",
        "AA25001",
        "LOAN",
        "GHS",
        "1,200,000",
        "24.50%",
        "2044-01-15",
        AS_OF,
        "accepted",
      ]);
      await loan.click();
      await expect(
        page.getByText(
          /^temenos_t24_v1\.0\/temp:\/\/uploads\/.+\/t24-ofs-2026-03-15\.json/,
        ),
      ).toBeVisible();
      await evidence(page, "t24-positions-downstream");
    });
  });
});
