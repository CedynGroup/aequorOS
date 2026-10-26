import {
  expect,
  type APIRequestContext,
  type Page,
  type TestInfo,
} from "@playwright/test";
import { randomUUID } from "node:crypto";
import { biApi } from "./bi";
import { E2E_USERS } from "./mint";
import { requireObjectStorage } from "./object-storage";
import { test as identities } from "./story-identities";
import {
  readWorkerJob,
  startJourneyWorker,
  unrelatedWorkerJobs,
  writeWorkerMarketWorkbook,
  writeWorkerWorkbook,
  type WorkerBookVariant,
} from "./worker-journey";

type Line = {
  line_code: string;
  description: string;
  exposure_amount: string | null;
  rate_pct: string | null;
  weighted_amount: string;
};
export type Liquidity = {
  metrics: {
    lcr_pct: string | null;
    lcr_status: string;
    hqla_total_ghs: string | null;
    net_outflows_30d_ghs: string | null;
  };
  hqla_composition: Line[];
  inflows: Line[];
  validations: { rule_code: string; passed: boolean; message: string }[];
};
type Summary = {
  is_stale: boolean;
  source_as_of_date: string;
  modules: {
    module: string;
    pipeline_state: string;
    computed_from_input_hash: string;
  }[];
};

/** Own the worker as well as the book; actor retirement runs after worker shutdown. */
class TreasuryBoundary {
  private worker?: ReturnType<typeof startJourneyWorker>;
  private readonly workerId = `treasury-boundary:${randomUUID()}`;
  private readonly log: string;
  private mappingReady = false;
  private sequence = 0;
  private previousHash?: string;
  asOf = "";

  constructor(
    private page: Page,
    private request: APIRequestContext,
    readonly bankId: string,
    private info: TestInfo,
    private module: "liquidity" | "capital",
  ) {
    this.log = info.outputPath("treasury-worker.log");
  }

  async read<T>(route: string): Promise<T> {
    const response = await biApi(
      this.request,
      "analyst",
      `/banks/${this.bankId}${route}`,
    );
    expect(response.status, JSON.stringify(response.body)).toBe(200);
    return response.body as T;
  }

  private async post(
    route: string,
    data: unknown,
    status: number,
    role = "analyst",
  ) {
    const response = await biApi(
      this.request,
      role,
      `/banks/${this.bankId}${route}`,
      { method: "POST", data },
    );
    expect(response.status, JSON.stringify(response.body)).toBe(status);
    return response.body as { id: string };
  }

  async prepare(returnCode: string) {
    requireObjectStorage();
    const initial = await this.read<Summary>("/live-summary");
    expect(initial.modules).toEqual([]);
    expect(readWorkerJob("pipeline_refresh", this.bankId)).toBeUndefined();
    const anchors = await this.read<{
      as_of: string;
      reporting_date_source: string;
      anchors: { reporting_date: string }[];
    }>(`/return-anchors?return_code=${returnCode}`);
    expect(anchors.reporting_date_source).toBe("regulator_anchor");
    const date = anchors.anchors
      .map((anchor) => anchor.reporting_date)
      .filter((date) => date <= anchors.as_of)
      .sort()
      .at(-1);
    expect(date).toBeDefined();
    this.asOf = date!;
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
    const version = await this.post(
      "/forecast/assumption-versions",
      {
        effective_from: this.asOf,
        change_note: "Synthetic treasury boundary assumptions",
        presets: {
          base: preset(["18", "16", "4.8", "48", "1", "0", "30"]),
          adverse: preset(["8", "6", "4.2", "54", "1.5", "15", "0"]),
          severely_adverse: preset(["-2", "-8", "3.6", "60", "2", "40", "0"]),
        },
      },
      201,
    );
    await this.post(
      `/forecast/assumption-versions/${version.id}/submit`,
      {},
      200,
    );
    await this.post(
      `/forecast/assumption-versions/${version.id}/approve`,
      { note: "Independent synthetic input approval" },
      200,
      "approver",
    );
  }

  async upload(variant: WorkerBookVariant): Promise<Liquidity> {
    await this.refresh(variant);
    return this.read<Liquidity>("/liquidity/dashboard");
  }

  async refresh(variant: WorkerBookVariant): Promise<void> {
    const oldJob = readWorkerJob("pipeline_refresh", this.bankId)?.id;
    const workbook = this.info.outputPath(
      `treasury-book-${++this.sequence}.xlsx`,
    );
    const { config } = await writeWorkerWorkbook(workbook, this.asOf, variant);
    if (!this.mappingReady) {
      await this.post(
        "/mapping-configs",
        {
          source_system: "EXCEL_CSV",
          name: "Treasury boundary book",
          config,
          activate: true,
          reason: "Map synthetic balance and security variants",
        },
        200,
      );
      this.mappingReady = true;
    }
    await this.page.goto("/data-engine/excel-csv");
    const upload = this.page.locator("section").filter({
      has: this.page.getByRole("heading", { name: "Upload & ingest" }),
    });
    await upload
      .getByLabel("Source files (.xlsx / .csv)")
      .setInputFiles(workbook);
    await upload.getByLabel("As-of date").fill(this.asOf);
    const accepted = this.page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(
          `/banks/${this.bankId}/ingestion-batches`,
        ),
    );
    await upload.getByRole("button", { name: "Upload & ingest" }).click();
    const response = await accepted;
    expect(response.status()).toBe(201);
    const { batch } = (await response.json()) as {
      batch: {
        status: string;
        records_extracted: number;
        records_accepted: number;
        records_error: number;
      };
    };
    expect(batch.status).toBe("accepted");
    expect(batch.records_error).toBe(0);
    expect(batch.records_accepted).toBe(batch.records_extracted);
    expect(batch.records_accepted).toBeGreaterThan(200);
    const queued = readWorkerJob("pipeline_refresh", this.bankId)!;
    expect(queued.id).not.toBe(oldJob);
    expect(JSON.parse(queued.payload)).toMatchObject({
      actor_user_id: E2E_USERS.analyst.id,
      as_of_date: this.asOf,
    });
    if (!this.worker) {
      expect(queued.status).toBe("queued");
      const market = this.info.outputPath("treasury-market.xlsx");
      await writeWorkerMarketWorkbook(market, this.asOf);
      await this.page.goto("/data-engine/market-data");
      await this.page.locator('input[type="file"]').setInputFiles(market);
      await this.page.locator('input[type="date"]').fill(this.asOf);
      const uploaded = this.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url().endsWith(`/banks/${this.bankId}/market-data/uploads`),
      );
      await this.page
        .getByRole("button", { name: "Upload", exact: true })
        .click();
      const result = await uploaded;
      expect(result.status()).toBe(200);
      expect(await result.json()).toMatchObject({
        status: "accepted",
        canonical_records_produced: 27,
        errors: [],
      });
      expect(
        unrelatedWorkerJobs(this.bankId),
        "Never claim another story's pending work",
      ).toBe(0);
      this.worker = startJourneyWorker(this.log, this.workerId);
    }
    await expect
      .poll(
        () => {
          if (
            this.worker!.process.exitCode !== null ||
            this.worker!.process.signalCode !== null
          )
            throw new Error(`Worker exited; see ${this.log}`);
          const job = readWorkerJob("pipeline_refresh", this.bankId);
          if (job?.error)
            throw new Error(`Worker failed: ${job.error}; ${job.progress}`);
          return job?.status;
        },
        { timeout: 90_000, intervals: [200, 500, 1000] },
      )
      .toBe("succeeded");
    expect(readWorkerJob("pipeline_refresh", this.bankId)).toMatchObject({
      id: queued.id,
      claimed_by: this.workerId,
      error: null,
      started_at: expect.any(String),
      completed_at: expect.any(String),
    });
    const live = await this.read<Summary>("/live-summary");
    expect(live).toMatchObject({
      is_stale: false,
      source_as_of_date: this.asOf,
    });
    const computed = live.modules.find(
      (module) => module.module === this.module,
    );
    expect(computed, `Fresh ${this.module} module`).toBeDefined();
    expect(computed!.pipeline_state).toBe("ready");
    expect(computed!.computed_from_input_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(computed!.computed_from_input_hash).not.toBe(this.previousHash);
    this.previousHash = computed!.computed_from_input_hash;
    const freshness = await this.read<{
      modules: { module: string; live_hash: string }[];
    }>("/freshness");
    expect(
      freshness.modules.find((module) => module.module === this.module)
        ?.live_hash,
    ).toBe(this.previousHash);
  }

  async close() {
    if (this.worker) {
      await this.worker.stop();
      if (this.info.status === "passed")
        await this.info.attach("treasury-boundary", {
          body: await this.page.screenshot({ fullPage: true }),
          contentType: "image/png",
        });
      await this.info.attach("treasury-worker", {
        path: this.log,
        contentType: "text/plain",
      });
    }
  }
}

export const test = identities.extend<{
  treasury: TreasuryBoundary;
  treasuryReturnCode: string;
  treasuryModule: "liquidity" | "capital";
}>({
  treasuryReturnCode: ["BSD2", { option: true }],
  treasuryModule: ["liquidity", { option: true }],
  treasury: async (
    { page, request, storyIdentity, treasuryReturnCode, treasuryModule },
    provide,
    info,
  ) => {
    const treasury = new TreasuryBoundary(
      page,
      request,
      storyIdentity.bankId,
      info,
      treasuryModule,
    );
    try {
      await treasury.prepare(treasuryReturnCode);
      await provide(treasury);
    } finally {
      await treasury.close();
    }
  },
});
export { expect };
