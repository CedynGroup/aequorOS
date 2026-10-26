/**
 * Run before shared-bank mutation stories: this real worker claims queued
 * refreshes, so the journey refuses to start it if another bank has pending
 * work. Its own tenant starts empty and every financial row arrives by upload.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";
import { rmSync } from "node:fs";
import path from "node:path";
import { E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import { biApi } from "./support/bi";
import { expectKpi } from "./support/figures";
import { generateCurrentVersion } from "./support/generate";
import { E2E_USERS, writeStorageState } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";
import {
  WORKER_APPROVER_ROLE,
  WORKER_BANK,
  WORKER_ID,
  WORKER_ROLE,
  createWorkerTenant,
  readWorkerJob,
  removeWorkerTenant,
  startJourneyWorker,
  unrelatedWorkerJobs,
  writeWorkerWorkbook,
  writeWorkerMarketWorkbook,
} from "./support/worker-journey";

type LiveSummary = {
  bank_id: string;
  is_stale: boolean;
  source_as_of_date: string | null;
  modules: {
    module: string;
    pipeline_state: string;
    computed_from_input_hash: string | null;
    metrics: Record<string, string>;
  }[];
};

async function bankRead<T>(
  request: APIRequestContext,
  route: string,
): Promise<T> {
  const read = await biApi(
    request,
    WORKER_ROLE,
    `/banks/${WORKER_BANK}${route}`,
  );
  expect(read.status, JSON.stringify(read.body)).toBe(200);
  return read.body as T;
}

test.use({
  storageState: async ({}, provideState) => {
    try {
      createWorkerTenant();
      await provideState(
        await writeStorageState(WORKER_ROLE, E2E_BASE_URL, E2E_TMP),
      );
    } finally {
      try {
        removeWorkerTenant();
      } finally {
        rmSync(path.join(E2E_TMP, `${WORKER_ROLE}.json`), { force: true });
      }
    }
  },
});
test.beforeAll(() => requireObjectStorage());

test("an uploaded book becomes fresh 645.16% liquidity coverage and the BSD2 cash figure through the real worker", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(180_000);
  const initial = await bankRead<LiveSummary>(request, "/live-summary");
  expect(initial).toMatchObject({
    bank_id: WORKER_BANK,
    modules: [],
    is_stale: false,
    source_as_of_date: null,
  });
  expect(readWorkerJob()).toBeUndefined();

  // The return definition supplies the reporting date even before data arrives.
  const anchors = await bankRead<{
    as_of: string;
    reporting_date_source: string;
    anchors: { reporting_date: string; data_status: string }[];
  }>(request, "/return-anchors?return_code=BSD2");
  expect(anchors.reporting_date_source).toBe("regulator_anchor");
  const asOf = anchors.anchors
    .map((anchor) => anchor.reporting_date)
    .filter((date) => date <= anchors.as_of)
    .sort()
    .at(-1);
  expect(asOf).toBeDefined();
  const reportingDate = asOf!;
  const workbook = testInfo.outputPath("worker-bank.xlsx");
  const { config } = writeWorkerWorkbook(workbook, reportingDate);
  const mapping = await biApi(
    request,
    WORKER_ROLE,
    `/banks/${WORKER_BANK}/mapping-configs`,
    {
      method: "POST",
      data: {
        source_system: "EXCEL_CSV",
        name: "Worker complete book",
        config,
        activate: true,
        reason: "Map the synthetic source book and market data",
      },
    },
  );
  expect(mapping.status, JSON.stringify(mapping.body)).toBe(200);
  expect(mapping.body).toMatchObject({ status: "active" });

  // Real maker/checker approval supplies the projection engine's governed inputs.
  const preset = (values: string[]) =>
    Object.fromEntries(
      [
        "loan_growth_pct",
        "deposit_growth_pct",
        "nim_pct",
        "cost_to_income_pct",
        "credit_loss_rate_pct",
        "fx_depreciation_pct",
        "dividend_payout_pct",
      ].map((key, index) => [key, values[index]]),
    );
  const proposed = await biApi(
    request,
    WORKER_ROLE,
    `/banks/${WORKER_BANK}/forecast/assumption-versions`,
    {
      method: "POST",
      data: {
        effective_from: reportingDate,
        change_note: "Synthetic worker journey projection assumptions",
        presets: {
          base: preset(["18", "16", "4.8", "48", "1", "0", "30"]),
          adverse: preset(["8", "6", "4.2", "54", "1.5", "15", "0"]),
          severely_adverse: preset(["-2", "-8", "3.6", "60", "2", "40", "0"]),
        },
      },
    },
  );
  expect(proposed.status, JSON.stringify(proposed.body)).toBe(201);
  const version = proposed.body as { id: string };
  const submitted = await biApi(
    request,
    WORKER_ROLE,
    `/banks/${WORKER_BANK}/forecast/assumption-versions/${version.id}/submit`,
    { method: "POST" },
  );
  expect(submitted.status, JSON.stringify(submitted.body)).toBe(200);
  const approved = await biApi(
    request,
    WORKER_APPROVER_ROLE,
    `/banks/${WORKER_BANK}/forecast/assumption-versions/${version.id}/approve`,
    {
      method: "POST",
      data: { note: "Approve synthetic inputs independently" },
    },
  );
  expect(approved.status, JSON.stringify(approved.body)).toBe(200);
  expect(approved.body).toMatchObject({ status: "approved" });
  expect(readWorkerJob()).toBeUndefined();

  await test.step("upload the balanced book through the Data Engine", async () => {
    await page.goto("/data-engine/excel-csv");
    const upload = page.locator("section").filter({
      has: page.getByRole("heading", { name: "Upload & ingest" }),
    });
    await upload
      .getByLabel("Source files (.xlsx / .csv)")
      .setInputFiles(workbook);
    await upload.getByLabel("As-of date").fill(reportingDate);
    const accepted = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(
          `/banks/${WORKER_BANK}/ingestion-batches`,
        ),
    );
    await upload.getByRole("button", { name: "Upload & ingest" }).click();
    const response = await accepted;
    expect(response.status()).toBe(201);
    const { batch } = (await response.json()) as {
      batch: {
        id: string;
        status: string;
        records_extracted: number;
        records_accepted: number;
        records_error: number;
      };
    };
    expect(batch, JSON.stringify(batch)).toMatchObject({
      status: "accepted",
      records_extracted: 227,
      records_accepted: 227,
      records_error: 0,
    });
    const queued = readWorkerJob();
    expect(queued).toMatchObject({
      status: "queued",
      claimed_by: null,
      started_at: null,
      completed_at: null,
    });
    expect(JSON.parse(queued!.payload)).toMatchObject({
      actor_user_id: E2E_USERS[WORKER_ROLE].id,
      as_of_date: reportingDate,
    });
    const waiting = await bankRead<LiveSummary>(request, "/live-summary");
    expect(waiting).toMatchObject({
      modules: [],
      is_stale: true,
      source_as_of_date: null,
    });
  });

  await test.step("upload the market curves through the bank's manual source", async () => {
    const queuedId = readWorkerJob()!.id;
    const marketWorkbook = testInfo.outputPath("worker-market.xlsx");
    writeWorkerMarketWorkbook(marketWorkbook, reportingDate);
    await page.goto("/data-engine/market-data");
    await page.locator('input[type="file"]').setInputFiles(marketWorkbook);
    await page.locator('input[type="date"]').fill(reportingDate);
    const uploaded = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/banks/${WORKER_BANK}/market-data/uploads`) &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const response = await uploaded;
    expect(response.status()).toBe(200);
    expect(await response.json()).toMatchObject({
      bank_id: WORKER_BANK,
      status: "accepted",
      canonical_records_produced: 27,
      quota_consumed: 0,
      errors: [],
    });
    expect(readWorkerJob()).toMatchObject({
      id: queuedId,
      status: "queued",
      claimed_by: null,
    });
  });

  // No helper calls a handler or completes a job: the real poll loop does both.
  expect(
    unrelatedWorkerJobs(),
    "The worker must not consume another story's refresh",
  ).toBe(0);
  const log = testInfo.outputPath("worker.log");
  const worker = startJourneyWorker(log);
  try {
    await test.step("the worker claims the queued batch and makes its figures current", async () => {
      await expect
        .poll(
          () => {
            if (
              worker.process.exitCode !== null ||
              worker.process.signalCode !== null
            ) {
              throw new Error(
                `Worker exited before completing the refresh; see ${log}`,
              );
            }
            const job = readWorkerJob();
            if (job?.error) {
              throw new Error(
                `Worker refresh failed: ${job.error}; progress=${job.progress}`,
              );
            }
            return job?.status;
          },
          { timeout: 90_000, intervals: [200, 500, 1000] },
        )
        .toBe("succeeded");
      const completed = readWorkerJob()!;
      expect(completed.claimed_by).toBe(WORKER_ID);
      expect(completed.started_at).not.toBeNull();
      expect(completed.completed_at).not.toBeNull();
      expect(completed.error).toBeNull();
      expect(JSON.parse(completed.progress).modules_ok).toEqual(
        expect.arrayContaining([
          "liquidity",
          "capital",
          "credit",
          "irr",
          "fx",
          "ftp",
          "rating",
          "forecast",
        ]),
      );

      const current = await bankRead<LiveSummary>(request, "/live-summary");
      expect(current).toMatchObject({
        is_stale: false,
        source_as_of_date: reportingDate,
      });
      const liquidity = current.modules.find(
        (module) => module.module === "liquidity",
      )!;
      expect(liquidity.pipeline_state).toBe("ready");
      expect(liquidity.computed_from_input_hash).toMatch(/^[a-f0-9]{64}$/);
      expect(Number(liquidity.metrics.hqla_total_ghs)).toBe(2_000_000);
      // 60% operational at 25% runoff + 40% non-operational at 40% = 310,000.
      expect(Number(liquidity.metrics.net_outflows_30d_ghs)).toBe(310_000);
      expect(Number(liquidity.metrics.lcr_pct)).toBe(645.16129);
      const freshness = await bankRead<{
        modules: {
          module: string;
          live_hash: string | null;
          official_run_hash: string | null;
          computed_at: string | null;
        }[];
      }>(request, "/freshness");
      // Current computation is distinct from an official filing, which is absent.
      expect(
        freshness.modules.find((module) => module.module === "liquidity"),
      ).toMatchObject({
        live_hash: liquidity.computed_from_input_hash,
        official_run_hash: null,
      });
      expect(
        freshness.modules.find((module) => module.module === "liquidity")
          ?.computed_at,
      ).toEqual(expect.any(String));
    });

    await test.step("the treasury cockpit shows the uploaded book's exact figures", async () => {
      await page.goto("/liquidity");
      await expectKpi(page, "HQLA stock", "GHS 2.0M", "Post-haircut weighted");
      await expectKpi(
        page,
        "30-day net outflows",
        "GHS 310.0K",
        "Outflows − capped inflows",
      );
      await expectKpi(page, "Liquidity Coverage Ratio", "645.16%");
      await testInfo.attach("worker-liquidity", {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    });

    await test.step("mint an official filing run through the same real worker", async () => {
      expect(readWorkerJob("official_run")).toBeUndefined();
      await page.goto("/data-engine");
      const flow = page.locator("section").filter({
        has: page.getByRole("heading", { name: "Data flow", exact: true }),
      });
      await flow
        .getByRole("button", { name: "Advanced actions", exact: true })
        .click();
      await flow.locator('input[type="date"]').fill(reportingDate);
      const enqueued = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname.endsWith(
            `/banks/${WORKER_BANK}/official-runs`,
          ),
      );
      await flow
        .getByRole("button", { name: "Mint official run", exact: true })
        .click();
      const response = await enqueued;
      expect(response.status()).toBe(202);
      const queued = (await response.json()) as {
        job_id: string;
        job_type: string;
      };
      expect(queued.job_type).toBe("official_run");
      await expect
        .poll(
          () => {
            const job = readWorkerJob("official_run");
            if (job?.error)
              throw new Error(`Official worker failed: ${job.error}`);
            return job?.status;
          },
          { timeout: 90_000, intervals: [200, 500, 1000] },
        )
        .toBe("succeeded");
      expect(readWorkerJob("official_run")).toMatchObject({
        id: queued.job_id.replaceAll("-", ""),
        claimed_by: WORKER_ID,
        error: null,
      });
      await expect(flow.getByText(/Official run minted/)).toBeVisible();
      const freshness = await bankRead<{
        modules: {
          module: string;
          live_hash: string | null;
          official_run_hash: string | null;
          is_stale: boolean;
        }[];
      }>(request, "/freshness");
      const liquidity = freshness.modules.find(
        (module) => module.module === "liquidity",
      )!;
      expect(liquidity.live_hash).toMatch(/^[a-f0-9]{64}$/);
      expect(liquidity.official_run_hash).toBe(liquidity.live_hash);
      expect(liquidity.is_stale).toBe(false);
    });

    await test.step("the generated return freezes the same uploaded cash amount", async () => {
      await page.goto(`/submissions/returns?code=BSD2&date=${reportingDate}`);
      await expect(
        page.getByRole("combobox", { name: "Return", exact: true }),
      ).toHaveValue("BSD2");
      const minted = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname.endsWith(
            `/banks/${WORKER_BANK}/regulatory-packages`,
          ) &&
          response.ok(),
      );
      expect(await generateCurrentVersion(page)).toBe(1);
      const generated = (await (await minted).json()) as {
        bank_id: string;
        return_code: string;
        reporting_date: string;
        snapshot: {
          sections: {
            rows: { code: string; value: string | null; status: string }[];
          }[];
        };
      };
      expect(generated).toMatchObject({
        bank_id: WORKER_BANK,
        return_code: "BSD2",
        reporting_date: reportingDate,
      });
      const rows = generated.snapshot.sections.flatMap(
        (section) => section.rows,
      );
      const cash = rows.find((row) => row.code === "BSD2.R14.domestic")!;
      expect(cash.status).toBe("mapped");
      expect(cash.value).toEqual(expect.any(String));
      expect(Number(cash.value)).toBe(2);
      const foreignCash = rows.find((row) => row.code === "BSD2.R14.foreign")!;
      expect(foreignCash.status).toBe("input_required");
      expect(foreignCash.value).toBeNull();
      await expect(page.getByText("Checks passed").first()).toBeVisible();
      await testInfo.attach("worker-generated-return", {
        body: await page.screenshot({ fullPage: true }),
        contentType: "image/png",
      });
    });
  } finally {
    await worker.stop();
    await testInfo.attach("worker-runtime", {
      path: log,
      contentType: "text/plain",
    });
  }
});
