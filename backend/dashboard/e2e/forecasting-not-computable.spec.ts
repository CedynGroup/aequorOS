// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Forecasting on a fresh tenant: no financial book and no approved
 * forecast assumptions. Every Forecasting tool must show its documented
 * not-computable state, and every run the analyst attempts must be refused by
 * the server with its named reason. Nothing may substitute an unapproved
 * assumption or estimate around missing facts.
 */
import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "path";
import { E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import { apiGet, section } from "./support/figures";
import { E2E_USERS, writeStorageState } from "./support/mint";
import { openTab } from "./support/navigation";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const FRESH_BANK_ID = "BK-FRSH0001";
const FRESH_ROLE = "fresh_forecast_analyst";
const FRESH_ORG_ID = E2E_USERS[FRESH_ROLE].organizationId!;
const PERIOD_ID = randomUUID();
const MISSING_ASSUMPTIONS =
  "The 'base' forecast scenario parameters do not cover: loan_growth_pct, deposit_growth_pct, nim_pct, cost_to_income_pct, credit_loss_rate_pct, fx_depreciation_pct, dividend_payout_pct.";
const MISSING_FACTS = "The reporting period has no financial facts to analyze.";

function fixture(action: "create" | "period" | "remove") {
  execFileSync(
    path.join(__dirname, "../../.venv/bin/python"),
    [
      "-c",
      `
import sqlite3, sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.authorization import DataScope, GrantorType, InstitutionScope, ModuleScope, PrincipalType, RoleBundle, SensitivityScope
from app.models import Bank, Organization, User
from app.identity.service import authorization
from app.identity.service import membership

db_path, action, org, bank, period, user_id = sys.argv[1:7]
if action == "create":
    with Session(create_engine(f"sqlite+pysqlite:///{db_path}")) as session:
        session.add(Organization(id=org, name="Fresh Forecast Tenant"))
        session.flush()
        user = User(id=UUID(user_id), organization_id=org, email="e2e.fresh_forecast_analyst@aequoros.example", display_name="E2E Fresh Forecast Analyst", role="analyst")
        session.add(user)
        session.add(Bank(id=bank, organization_id=org, name="Tano Fresh Bank", short_name="Tano", currency="NGN", jurisdiction_code="NG", license_type="universal", institution_type="universal_bank"))
        session.flush()
        membership.ensure_baseline_membership(session, user=user, granted_by_id="e2e-forecast-fixture", commit=False)
        authorization.create_role_binding(
            session,
            organization_id=org,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(InstitutionScope.ORGANIZATION, None, ModuleScope.ALL, SensitivityScope.ALL, data_scope=DataScope.ALL),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "e2e-forecast-fixture"),
            reason="Exercise forecasting on a freshly provisioned tenant",
            commit=False,
        )
        assert user.authorization_version == 3
        session.commit()
    sys.exit(0)

with sqlite3.connect(db_path) as db:
    if action == "period":
        db.execute("INSERT INTO bank_reporting_periods (organization_id, bank_id, period_start, period_end, label, status, id, created_at, updated_at) VALUES (?, ?, '2026-09-01', '2026-09-30', '2026-09', 'open', ?, datetime('now'), datetime('now'))", (org, bank, period.replace('-', '')))
    else:
        runs = [row[0] for row in db.execute("SELECT id FROM regulatory_runs WHERE bank_id=?", (bank,))]
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if "run_id" in columns and runs:
                db.execute(f"DELETE FROM {table} WHERE run_id IN ({','.join('?' * len(runs))})", runs)
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if "organization_id" in columns:
                db.execute(f"DELETE FROM {table} WHERE organization_id=?", (org,))
        db.execute("DELETE FROM organizations WHERE id=?", (org,))
`,
      path.join(E2E_TMP, "e2e.db"),
      action,
      FRESH_ORG_ID,
      FRESH_BANK_ID,
      PERIOD_ID,
      E2E_USERS[FRESH_ROLE].id,
    ],
    { cwd: path.join(__dirname, "../..") },
  );
}

test.describe("Forecasting on a fresh tenant", () => {
  test.use({
    storageState: async ({}, provideState) => {
      try {
        fixture("create");
        await provideState(
          await writeStorageState(FRESH_ROLE, E2E_BASE_URL, E2E_TMP),
        );
      } finally {
        fixture("remove");
      }
    },
  });

  test("shows the not-computable state everywhere and the server refuses every run", async ({
    page,
  }) => {
    test.setTimeout(150_000);

    const identity = await apiGet(page, FRESH_ROLE, "/auth/me");
    expect(identity).toMatchObject({
      user_id: E2E_USERS[FRESH_ROLE].id,
      organization_id: FRESH_ORG_ID,
    });
    const directory = await apiGet(page, FRESH_ROLE, "/banks");
    expect(directory.banks.map((bank: { id: string }) => bank.id)).toEqual([
      FRESH_BANK_ID,
    ]);

    // ---- No book at all: no period, no live baseline, no saved results.
    await page.goto("/forecasting");
    await expect(
      page.getByText("A reporting period is required to run a forecast"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run forecast" }),
    ).toBeDisabled();
    await expect(
      page.getByText("Live forecast is not available yet"),
    ).toBeVisible();
    await openTab(page, "NII Forecast");
    await expect(
      page.getByText("No succeeded forecast runs yet"),
    ).toBeVisible();
    await openTab(page, "Assumptions");
    await expect(
      page.getByText("No succeeded forecast runs yet"),
    ).toBeVisible();
    // No approved preset for this jurisdiction: the catalogue offers none, only
    // the three documented engine defaults, which no preset is built from.
    await expect(
      section(page, "Preset catalogue").getByRole("columnheader"),
    ).toHaveText(["Assumption", "Engine default"]);
    await openTab(page, "Reverse Stress");
    await expect(
      page.getByText("No reverse-stress frontier yet"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run reverse stress" }).first(),
    ).toBeDisabled();

    // ---- A period with no facts: the controls open; the server refuses.
    fixture("period");
    await page.goto("/forecasting");
    await expect(
      page.getByText("A reporting period is required to run a forecast"),
    ).toHaveCount(0);
    const forecast = await refusedRun(page, "/forecast/runs", () =>
      page.getByRole("button", { name: "Run forecast" }).click(),
    );
    expect(forecast.assumptions).toBeNull();
    expect(forecast.path).toEqual([]);
    await expect(page.getByText("Run failed")).toBeVisible();
    await expect(page.getByText(MISSING_ASSUMPTIONS)).toBeVisible();

    await openTab(page, "What-if Lab");
    const whatif = await refusedRun(page, "/forecast/whatif", () =>
      page
        .getByRole("button", { name: "Run Interest rate shock +400bps" })
        .click(),
    );
    expect(whatif.shocked_path).toEqual([]);
    const shockAlert = page
      .getByRole("alert")
      .filter({ hasText: "This shock could not be projected" });
    await expect(shockAlert).toBeVisible();
    await expect(shockAlert).toContainText(MISSING_ASSUMPTIONS);
    await expect(shockAlert).toContainText(
      "Engine diagnostic missing_parameter",
    );

    await openTab(page, "Optimizer");
    const search = await refusedRun(page, "/forecast/optimizer", () =>
      page.getByRole("button", { name: "Run optimizer" }).first().click(),
    );
    expect(search.candidates_evaluated).toBe(0);
    const searchAlert = page
      .getByRole("alert")
      .filter({ hasText: "The optimizer could not search this period" });
    await expect(searchAlert).toBeVisible();
    await expect(searchAlert).toContainText(MISSING_ASSUMPTIONS);
    await expect(
      page.getByText("No feasible strategy in this search"),
    ).toHaveCount(0);

    // A persisted refusal must keep its diagnostic after reload; it is not a
    // completed search whose candidates all failed the feasibility floors.
    await page.reload();
    await expect(searchAlert).toBeVisible();
    await expect(searchAlert).toContainText(MISSING_ASSUMPTIONS);
    await expect(searchAlert).toContainText(
      "Engine diagnostic missing_parameter",
    );
    await expect(
      page.getByText("No feasible strategy in this search"),
    ).toHaveCount(0);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(
          evidenceDir,
          "forecasting-optimizer-reloaded-refusal.png",
        ),
        fullPage: true,
      });
    }

    await openTab(page, "Reverse Stress");
    const frontier = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/banks/${FRESH_BANK_ID}/reverse-stress/runs`) &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Run reverse stress" })
      .first()
      .click();
    const refusal = await frontier;
    expect(refusal.status()).toBe(409);
    expect((await refusal.json()).error.details).toEqual({
      error_code: "financial_facts_missing",
      message: MISSING_FACTS,
    });
    await expect(page.getByText("Reverse-stress search failed")).toBeVisible();
    await expect(page.getByText(MISSING_FACTS)).toBeVisible();
    await expect(
      page.getByText("No reverse-stress frontier yet"),
    ).toBeVisible();

    // Nothing was computed: no live result, and every saved run is a failure.
    const live = await apiGet(
      page,
      FRESH_ROLE,
      `/banks/${FRESH_BANK_ID}/live-summary`,
    );
    expect(live.modules).toEqual([]);
    const runs = await apiGet(
      page,
      FRESH_ROLE,
      `/banks/${FRESH_BANK_ID}/regulatory-runs`,
    );
    expect(
      runs.runs
        .map((r: { module: string; status: string }) => [r.module, r.status])
        .sort(),
    ).toEqual([
      ["forecast", "failed"],
      ["optimizer", "failed"],
      ["whatif", "failed"],
    ]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-not-computable.png"),
        fullPage: true,
      });
    }
  });
});

/**
 * Trigger a Forecasting run and return its body: persisted (201) as a FAILED
 * run whose named reason is the missing approved assumptions.
 */
async function refusedRun(
  page: Page,
  pathName: string,
  trigger: () => Promise<void>,
) {
  const ran = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/banks/${FRESH_BANK_ID}${pathName}`) &&
      r.request().method() === "POST",
  );
  await trigger();
  const response = await ran;
  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body.status).toBe("failed");
  expect(body.error).toMatchObject({
    code: "missing_parameter",
    message: MISSING_ASSUMPTIONS,
  });
  return body;
}
