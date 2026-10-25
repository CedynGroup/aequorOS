import {
  execFile,
  execFileSync,
  spawn,
  type ChildProcess,
} from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import { closeSync, openSync } from "node:fs";
import { once } from "node:events";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { E2E_TMP } from "../../playwright.config";
import { E2E_USERS } from "./mint";

export const WORKER_BANK = "BK-WRKR0001";
export const WORKER_ORG = "OR-WRKR0001";
export const WORKER_ROLE = "worker_ingestion";
export const WORKER_APPROVER_ROLE = "worker_approver";
const APPROVER_ID = randomUUID();
export const WORKER_ID = `ingestion-journey:${randomUUID()}`;
const USER_ID = randomUUID();
const BACKEND = path.resolve(__dirname, "../../..");
const PYTHON = path.join(BACKEND, ".venv/bin/python");
const DATABASE = path.join(E2E_TMP, "e2e.db");

/** Provision setup and authority only; the workbook supplies every financial row. */
export function createWorkerTenant(): void {
  execFileSync(
    PYTHON,
    [
      "-c",
      `
import sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.authorization import DataScope, GrantorType, InstitutionScope, ModuleScope, PrincipalType, RoleBundle, SensitivityScope
from app.db.base import utc_now
from app.identity.service import authorization, membership
from app.models import Bank, Organization, User
from app.services.parameter_register import seed_tenant_register

database, org, bank_id, user_id, approver_id = sys.argv[1:6]
with Session(create_engine(f"sqlite+pysqlite:///{database}")) as session:
    session.add(Organization(id=org, name="Worker Journey Tenant"))
    session.flush()
    bank = Bank(id=bank_id, organization_id=org, name="Worker Journey Bank", short_name="Worker Journey", currency="GHS", jurisdiction_code="GH", license_type="universal", institution_type="universal_bank")
    session.add(bank)
    for identity, role in ((user_id, RoleBundle.ANALYST), (approver_id, RoleBundle.APPROVER)):
        user = User(id=UUID(identity), organization_id=org, email=f"{identity}@aequoros.example", display_name=f"Worker Journey {role.value}", role=role.value)
        session.add(user)
        session.flush()
        membership.ensure_baseline_membership(session, user=user, granted_by_id="e2e-worker-journey", commit=False)
        authorization.create_role_binding(
            session, organization_id=org, principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN, role_bundle=role,
            scope=authorization.BindingScope(InstitutionScope.ORGANIZATION, None, ModuleScope.ALL, SensitivityScope.ALL, data_scope=DataScope.ALL),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "e2e-worker-journey"),
            reason="Exercise ingestion through the real worker", commit=False,
        )
        assert user.authorization_version == 3
    seed_tenant_register(session, bank=bank, approved_by="e2e-worker-journey", approved_at=utc_now())
    session.commit()
`,
      DATABASE,
      WORKER_ORG,
      WORKER_BANK,
      USER_ID,
      APPROVER_ID,
    ],
    { cwd: BACKEND },
  );
  E2E_USERS[WORKER_ROLE] = {
    id: USER_ID,
    organizationId: WORKER_ORG,
    roles: ["analyst"],
    authv: 3,
  };
  E2E_USERS[WORKER_APPROVER_ROLE] = {
    id: APPROVER_ID,
    organizationId: WORKER_ORG,
    roles: ["approver"],
    authv: 3,
  };
}

export function removeWorkerTenant(): void {
  delete E2E_USERS[WORKER_ROLE];
  delete E2E_USERS[WORKER_APPROVER_ROLE];
  execFileSync(
    PYTHON,
    [
      "-c",
      `
import sqlite3, sys
database, org, bank, worker = sys.argv[1:5]
with sqlite3.connect(database) as db:
    packages = [row[0] for row in db.execute("SELECT id FROM regulatory_packages WHERE organization_id=?", (org,))]
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        for column in ("package_id", "regulatory_package_id"):
            if column in columns and packages:
                placeholders = ','.join('?' for _ in packages)
                db.execute(f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders})', packages)
        if "organization_id" in columns:
            db.execute(f'DELETE FROM "{table}" WHERE organization_id=?', (org,))
    db.execute("DELETE FROM worker_heartbeats WHERE worker_id=?", (worker,))
    db.execute("DELETE FROM organizations WHERE id=?", (org,))
    assert db.execute("SELECT COUNT(*) FROM banks WHERE id=?", (bank,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM jobs WHERE bank_id=?", (bank,)).fetchone()[0] == 0
`,
      DATABASE,
      WORKER_ORG,
      WORKER_BANK,
      WORKER_ID,
    ],
    { cwd: BACKEND },
  );
}

export type WorkerBookVariant = {
  cash?: string;
  deposits?: string;
  equity?: string;
  loanMaturityDays?: number;
  loanReportingBalance?: string;
  securitiesBalance?: string;
  securities?: {
    reference: string;
    amount: string;
    level?: "L1" | "L2A" | "L2B";
  }[];
};

export async function writeWorkerWorkbook(
  filename: string,
  asOf: string,
  variant: WorkerBookVariant = {},
) {
  const dateOffset = (days: number) => {
    const date = new Date(`${asOf}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
  };
  const sheets: Record<string, Record<string, string>[]> = {
    gl_accounts: [
      {
        source_reference: "WORKER-CASH",
        account_code: "1001",
        name: "Cash vault",
        account_class: "ASSET",
        currency: "GHS",
        balance: variant.cash ?? "2000000",
      },
      {
        source_reference: "WORKER-DEPOSITS",
        account_code: "2001",
        name: "Corporate deposits",
        account_class: "LIABILITY",
        currency: "GHS",
        balance: variant.deposits ?? "1000000",
      },
      {
        source_reference: "WORKER-EQUITY",
        account_code: "3000",
        name: "Share capital",
        account_class: "EQUITY",
        currency: "GHS",
        balance: variant.equity ?? "1200000",
      },
      {
        source_reference: "WORKER-LOANS",
        account_code: "1301",
        name: "Corporate loans",
        account_class: "ASSET",
        currency: "USD",
        balance: "20000",
      },
    ],
    counterparties: [
      {
        source_reference: "WORKER-CORPORATE",
        name: "Synthetic Corporate Depositor",
        counterparty_type: "CORPORATE",
        country_code: "GH",
      },
    ],
    products: [
      {
        source_reference: "DEP.CORP.CUR",
        product_code: "DEP.CORP.CUR",
        name: "Corporate current deposit",
        regulatory_category: "CORPORATE_DEPOSIT",
      },
      {
        source_reference: "LN.CORP.1Y",
        product_code: "LN.CORP.1Y",
        name: "Corporate loan",
        regulatory_category: "CORPORATE_UNRATED",
      },
    ],
    positions: [
      {
        source_reference: "WORKER-CURRENT-DEPOSIT",
        position_type: "DEPOSIT",
        currency: "GHS",
        balance: variant.deposits ?? "1000000",
        interest_rate: "0.05",
        rate_type: "FIXED",
        counterparty_reference: "WORKER-CORPORATE",
        product_code: "DEP.CORP.CUR",
        gl_account_code: "2001",
        "attributes.branch_id": "WORKER-BRANCH",
      },
      {
        ...(variant.loanReportingBalance
          ? { "attributes.balance_ghs": variant.loanReportingBalance }
          : {}),
        source_reference: "WORKER-USD-LOAN",
        position_type: "LOAN",
        currency: "USD",
        balance: "20000",
        interest_rate: "0.05",
        rate_type: "FIXED",
        contractual_maturity: dateOffset(variant.loanMaturityDays ?? 365),
        origination_date: dateOffset(-365),
        ifrs9_stage: "1",
        counterparty_reference: "WORKER-CORPORATE",
        product_code: "LN.CORP.1Y",
        gl_account_code: "1301",
        "attributes.branch_id": "WORKER-BRANCH",
        risk_weight_code: "RW100",
        "attributes.balance_ghs": "200000",
        "attributes.ecl_provision_ghs": "0",
      },
    ],
    business_units: [
      { business_unit_id: "WORKER-BRANCH", business_unit_name: "Head Office" },
    ],
    behavioral_assumptions: [
      {
        assumption_type: "DEPOSIT_STABILITY",
        product_code: "DEP.CORP.CUR",
        value: "0.6",
        unit: "FRACTION",
      },
      {
        assumption_type: "NMD_DURATION",
        product_code: "DEP.CORP.CUR",
        value: "12",
        unit: "MONTHS",
      },
    ],
    capital_structure: [
      {
        capital_component: "CET1_SHARE_CAPITAL",
        amount_ghs: variant.equity ?? "1200000",
        tier: "CET1",
      },
      {
        capital_component: "REGULATORY_ADJ_GOODWILL",
        amount_ghs: "0",
        tier: "CET1_DEDUCTION",
      },
      {
        capital_component: "TIER2_SUBORDINATED_DEBT",
        amount_ghs: "0",
        tier: "TIER2",
      },
    ],
    yield_curve: ["GHS", "USD"].flatMap((currency) =>
      [1, 3, 6, 12, 24, 36, 60, 120, 180, 240, 300, 360].map((months) => ({
        curve_name: `${currency}_SOVEREIGN`,
        currency,
        tenor_months: String(months),
        rate: "0.05",
        quote_date: asOf,
      })),
    ),
    fx_rates_current: [
      {
        currency: "USD",
        quote_currency: "GHS",
        spot_rate: "10",
        quote_date: asOf,
      },
      {
        currency: "GHS",
        quote_currency: "GHS",
        spot_rate: "1",
        quote_date: asOf,
      },
    ],
    fx_rates_historical: Array.from({ length: 150 }, (_, index) => ({
      date: dateOffset(index - 150),
      currency: "USD",
      quote_currency: "GHS",
      spot_rate: ((850 + index) / 100).toFixed(2),
    })),
    historical_financials: Array.from({ length: 36 }, (_, index) => {
      const date = new Date(`${asOf}T00:00:00Z`);
      date.setUTCDate(1);
      date.setUTCMonth(date.getUTCMonth() - index + 1);
      date.setUTCDate(0);
      return {
        period_end: date.toISOString().slice(0, 10),
        net_interest_income_ghs: "1000",
        non_interest_income_ghs: "250",
        net_income_ghs: "500",
        operating_expenses_ghs: "600",
        provisions_ghs: "150",
      };
    }),
  };
  if (variant.securities?.length) {
    if (variant.securitiesBalance === undefined)
      throw new Error("Declare the securities control-account balance");
    sheets.gl_accounts.push({
      source_reference: "WORKER-SECURITIES",
      account_code: "1201",
      name: "Public-sector securities",
      account_class: "ASSET",
      currency: "GHS",
      balance: variant.securitiesBalance,
    });
    sheets.counterparties.push({
      source_reference: "WORKER-PUBLIC-ISSUER",
      name: "Synthetic Public Issuer",
      counterparty_type: "GOVERNMENT_ENTITY",
      country_code: "GH",
    });
    sheets.products.push({
      source_reference: "SEC.PUBLIC",
      product_code: "SEC.PUBLIC",
      name: "Public-sector holding",
      regulatory_category: "CORPORATE_UNRATED",
    });
    sheets.positions.push(
      ...variant.securities.map((holding) => ({
        source_reference: holding.reference,
        position_type: "SECURITY_HOLDING",
        currency: "GHS",
        balance: holding.amount,
        interest_rate: "0.05",
        rate_type: "FIXED",
        contractual_maturity: dateOffset(730),
        origination_date: dateOffset(-365),
        counterparty_reference: "WORKER-PUBLIC-ISSUER",
        product_code: "SEC.PUBLIC",
        gl_account_code: "1201",
        "attributes.issuer_class": "public_enterprise",
        "attributes.hqla_level": holding.level ?? "",
        "attributes.branch_id": "WORKER-BRANCH",
      })),
    );
  }
  const core = {
    gl_account: "gl_accounts",
    counterparty: "counterparties",
    product: "products",
    position: "positions",
  };
  const config = {
    field_mappings: Object.fromEntries(
      Object.entries(core).map(([entity, table]) => [
        entity,
        {
          source_table: table,
          fields: Object.fromEntries(
            [...new Set(sheets[table].flatMap((row) => Object.keys(row)))]
              .filter((key) => !key.startsWith("attributes."))
              .map((key) => [key, key]),
          ),
        },
      ]),
    ),
    product_mappings: {
      "DEP.CORP.CUR": "CORPORATE_DEPOSIT",
      "LN.CORP.1Y": "CORPORATE_UNRATED",
      ...(variant.securities?.length
        ? { "SEC.PUBLIC": "CORPORATE_UNRATED" }
        : {}),
    },
    reference_mappings: Object.fromEntries(
      Object.keys(sheets)
        .filter((table) => !Object.values(core).includes(table))
        .map((table) => [table, { source_table: table, dataset_kind: table }]),
    ),
  };
  await writeWorkbook(filename, sheets);
  return { config };
}

export async function writeWorkerMarketWorkbook(
  filename: string,
  asOf: string,
): Promise<void> {
  await writeWorkbook(filename, {
    yield_curves: ["GHS", "USD"].flatMap((currency) =>
      [1, 3, 6, 12, 24, 36, 60, 120, 180, 240, 300, 360].map((months) => ({
        currency,
        curve_name: `${currency}_SOVEREIGN`,
        as_of_date: asOf,
        tenor_months: String(months),
        rate_percent: "5",
      })),
    ),
    fx_rates: [
      {
        base_currency: "USD",
        quote_currency: "GHS",
        rate_type: "spot",
        tenor_months: "",
        rate: "10",
        as_of_date: asOf,
      },
    ],
  });
}

async function writeWorkbook(
  filename: string,
  sheets: Record<string, Record<string, string>[]>,
): Promise<void> {
  await promisify(execFile)(
    PYTHON,
    [
      "-c",
      `
import json, sys
from openpyxl import Workbook
workbook = Workbook()
workbook.remove(workbook.active)
for name, rows in json.loads(sys.argv[2]).items():
    sheet = workbook.create_sheet(name)
    keys = list(dict.fromkeys(key for row in rows for key in row))
    sheet.append(keys)
    for row in rows:
        sheet.append([row.get(key) for key in keys])
workbook.save(sys.argv[1])
`,
      filename,
      JSON.stringify(sheets),
    ],
    { cwd: BACKEND },
  );
}

export type RefreshJob = {
  id: string;
  status: string;
  claimed_by: string | null;
  started_at: string | null;
  completed_at: string | null;
  payload: string;
  progress: string;
  error: string | null;
};

/** Read durable worker evidence; never complete, enqueue or dispatch work here. */
export function readWorkerJob(
  jobType: "pipeline_refresh" | "official_run" = "pipeline_refresh",
  bankId = WORKER_BANK,
): RefreshJob | undefined {
  const db = new DatabaseSync(DATABASE, { readOnly: true });
  try {
    db.exec("PRAGMA busy_timeout = 5000");
    return db
      .prepare(
        "SELECT id, status, claimed_by, started_at, completed_at, payload, progress, error FROM jobs WHERE bank_id=? AND job_type=? ORDER BY queued_at DESC LIMIT 1",
      )
      .get(bankId, jobType) as RefreshJob | undefined;
  } finally {
    db.close();
  }
}

/** The worker must not consume another story's shared-fixture mutations. */
export function unrelatedWorkerJobs(bankId = WORKER_BANK): number {
  const db = new DatabaseSync(DATABASE, { readOnly: true });
  try {
    db.exec("PRAGMA busy_timeout = 5000");
    return Number(
      db
        .prepare(
          "SELECT COUNT(*) AS total FROM jobs WHERE job_type IN ('pipeline_refresh','official_run') AND status IN ('queued', 'running') AND bank_id<>?",
        )
        .get(bankId)?.total,
    );
  } finally {
    db.close();
  }
}

export function startJourneyWorker(
  log: string,
  workerId = WORKER_ID,
): {
  process: ChildProcess;
  stop: () => Promise<void>;
} {
  const fd = openSync(log, "w");
  const process = spawn(PYTHON, ["-m", "app.worker"], {
    cwd: BACKEND,
    env: {
      ...globalThis.process.env,
      DATABASE_URL: `sqlite+pysqlite:///${DATABASE}`,
      WORKER_DATABASE_URL: "",
      APP_ENV: "test",
      TLS_ALLOW_PLAINTEXT: "1",
      WORKER_JOB_TYPES: "pipeline_refresh,official_run",
      WORKER_ID: workerId,
      WORKER_POLL_SECONDS: "0.2",
      RUN_INPROCESS_WORKER: "0",
    },
    stdio: ["ignore", fd, fd],
  });
  return {
    process,
    stop: async () => {
      try {
        if (process.exitCode === null && process.signalCode === null) {
          const exited = once(process, "exit");
          const forceStop = setTimeout(() => process.kill("SIGKILL"), 5000);
          forceStop.unref();
          try {
            process.kill("SIGTERM");
            await exited;
          } finally {
            clearTimeout(forceStop);
          }
        }
      } finally {
        closeSync(fd);
      }
    },
  };
}
