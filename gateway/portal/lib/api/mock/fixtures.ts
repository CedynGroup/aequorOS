/**
 * Mock-mode fixtures. They reproduce the approved design's data
 * (`gateway/design/*.dc.html`, the `renderVals()` of each screen) in the
 * exact SPEC Part 3 shapes, and fill in consistent synthetic records for
 * everything the design links to but does not draw (other filings, threads,
 * institutions). Fictional institutions and people only.
 */

import { createHash } from "node:crypto";
import type {
  AccessOverview,
  AttentionItem,
  AuditEntry,
  AuditList,
  CalendarMonth,
  ContentLine,
  HeatState,
  InstitutionDetail,
  InstitutionListItem,
  OnboardingOverview,
  Overview,
  QueryDetail,
  ReturnDefinitionDetail,
  ReturnListItem,
  SubmissionDetail,
  SubmissionList,
  SubmissionRow,
  SubmissionState,
} from "../types";

export function sha256Hex(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

/** The cycle every design screen shows. */
export const CYCLE_DATE = "2026-10-31";

// ---------------------------------------------------------------------------
// Returns
// ---------------------------------------------------------------------------

export const RETURN_NAMES: Record<string, string> = {
  "DBK-DAILY": "Daily Bank Return",
  BSD1: "Liquidity Reserve Return",
  BSD2: "Statement of Assets and Liabilities",
  BSD3A: "Large Exposures — Advances and Deposits",
  BSD5A: "Capital Adequacy Return",
  BSD6: "Maturity Analysis of Assets and Liabilities",
  BSD7A: "Current Year Results",
  BSD8: "Advances Subject to Adverse Classification",
  BSD13: "Net Open Position",
  "CAR-RWA": "Capital Adequacy (CAR & RWA)",
  LMT: "Liquidity Monitoring Tools",
  "LE-MONTHLY": "Large Exposures",
  "NPL-MONTHLY": "Monthly Non-Performing Loans",
  "IRRBB-PILOT": "IRRBB Pilot Return",
  "ICAAP-REPORT": "ICAAP Report",
};

const RETURN_ROWS: [string, ReturnListItem["cadence"], number, ReturnListItem["status"]][] = [
  ["DBK-DAILY", "daily", 12, "published"],
  ["BSD1", "weekly", 16, "published"],
  ["BSD2", "monthly", 58, "published"],
  ["BSD3A", "monthly", 20, "published"],
  ["BSD5A", "monthly", 44, "draft_pending"],
  ["BSD6", "monthly", 40, "published"],
  ["BSD7A", "monthly", 26, "published"],
  ["BSD8", "monthly", 24, "published"],
  ["BSD13", "monthly", 18, "published"],
  ["CAR-RWA", "monthly", 62, "published"],
  ["LMT", "monthly", 71, "published"],
  ["LE-MONTHLY", "monthly", 33, "published"],
  ["NPL-MONTHLY", "monthly", 31, "published"],
  ["IRRBB-PILOT", "quarterly", 27, "published"],
  ["ICAAP-REPORT", "annual", 9, "published"],
];

const ALL_TYPES = ["universal_bank", "rural_bank", "savings_and_loans", "microfinance"];

export function buildReturns(): ReturnListItem[] {
  return RETURN_ROWS.map(([code, cadence, rule_count, status]) => ({
    code,
    name: RETURN_NAMES[code] ?? code,
    cadence,
    current_version: "2026.1",
    applies_to: code === "IRRBB-PILOT" ? ["universal_bank"] : ALL_TYPES,
    rule_count,
    status,
  }));
}

export function buildReturnDetail(item: ReturnListItem): ReturnDefinitionDetail {
  const { rule_count, status, ...summary } = item;
  void status;
  const detail: ReturnDefinitionDetail = {
    ...summary,
    versions: [
      {
        version: "2026.1",
        status: "published",
        first_reporting_date: item.cadence === "annual" ? "2026-12-31" : "2026-01-31",
        last_reporting_date: null,
        schema_sha256: sha256Hex(`schema:${item.code}:2026.1`),
        rule_count,
        published_at: "2025-11-28T10:00:00.000Z",
      },
      {
        version: "2025.1",
        status: "retired",
        first_reporting_date: "2025-01-31",
        last_reporting_date: "2025-12-31",
        schema_sha256: sha256Hex(`schema:${item.code}:2025.1`),
        rule_count: Math.max(1, rule_count - 4),
        published_at: "2024-11-29T10:00:00.000Z",
      },
    ],
    draft: null,
  };
  if (item.code === "BSD5A") {
    detail.draft = {
      version: "2026.2",
      change_summary: "Adds line 4.7, operational risk RWA",
      first_reporting_date: "2027-01-31",
      notice_days: 60,
      breaking: false,
      approvals: [
        { step: "drafted", by: "E. Asante", role_label: "Policy", at: "2026-11-18T10:12:00.000Z" },
        { step: "approved", by: "M. Adjei", role_label: "Policy", at: "2026-11-19T14:40:00.000Z" },
        { step: "pending", by: null, role_label: "Supervision", at: null },
      ],
    };
  }
  return detail;
}

// ---------------------------------------------------------------------------
// Institutions
// ---------------------------------------------------------------------------

type InstitutionSeed = {
  code: string;
  name: string;
  type: string;
  status: InstitutionListItem["status"];
  channel: InstitutionListItem["channel"];
  software: string | null;
  supervisor: string | null;
  client_id: string | null;
  cert: string | null;
  users: number;
  register_status: string;
  contacts: [string, string];
};

const AEQUOR = "AequorOS 2026.11";
const INHOUSE = "In-house system";

export const INSTITUTION_SEEDS: InstitutionSeed[] = [
  { code: "0104", name: "Meridian Coast Bank", type: "universal_bank", status: "live", channel: "api", software: AEQUOR, supervisor: "K. Owusu", client_id: "mcb-prod-01", cert: "2027-06-14T23:59:59.000Z", users: 4, register_status: "live", contacts: ["Adwoa Sarpong", "Kwame Asare"] },
  { code: "0109", name: "Ridgeway Commercial Bank", type: "universal_bank", status: "live", channel: "api", software: INHOUSE, supervisor: "K. Owusu", client_id: "rcb-prod-02", cert: "2027-09-02T23:59:59.000Z", users: 2, register_status: "live", contacts: ["Yaa Ofori", "Kojo Badu"] },
  { code: "0117", name: "Palmgrove Bank", type: "universal_bank", status: "live", channel: "api", software: AEQUOR, supervisor: "A. Boateng", client_id: "pgb-prod-01", cert: "2027-03-14T23:59:59.000Z", users: 3, register_status: "live", contacts: ["Efua Quansah", "Nana Ampofo"] },
  { code: "0121", name: "Harbourline Bank", type: "universal_bank", status: "live", channel: "api", software: INHOUSE, supervisor: "K. Owusu", client_id: "hbl-prod-01", cert: "2027-01-19T23:59:59.000Z", users: 5, register_status: "live", contacts: ["Akua Frimpong", "Kofi Antwi"] },
  { code: "0126", name: "Lakeside Trust Bank", type: "universal_bank", status: "live", channel: "portal_upload", software: null, supervisor: "E. Asante", client_id: null, cert: null, users: 2, register_status: "live", contacts: ["Esi Nyarko", "Yaw Donkor"] },
  { code: "0133", name: "Kestrel Bank", type: "universal_bank", status: "live", channel: "api", software: AEQUOR, supervisor: "K. Owusu", client_id: "kes-prod-01", cert: "2026-11-30T23:59:59.000Z", users: 3, register_status: "live", contacts: ["Abla Agyeman", "Kwesi Boakye"] },
  { code: "0138", name: "Goldfield Commercial Bank", type: "universal_bank", status: "live", channel: "api", software: INHOUSE, supervisor: "E. Asante", client_id: "gcb-prod-01", cert: "2027-04-08T23:59:59.000Z", users: 4, register_status: "live", contacts: ["Afua Osei", "Kwabena Amoako"] },
  { code: "0142", name: "Savanna Union Bank", type: "universal_bank", status: "live", channel: "api", software: AEQUOR, supervisor: "A. Boateng", client_id: "sub-prod-01", cert: "2027-05-21T23:59:59.000Z", users: 3, register_status: "live", contacts: ["Ama Opoku", "Fiifi Arthur"] },
  { code: "0147", name: "Cedarwood Bank", type: "universal_bank", status: "live", channel: "api", software: AEQUOR, supervisor: "E. Asante", client_id: "cdw-prod-01", cert: "2027-02-11T23:59:59.000Z", users: 2, register_status: "live", contacts: ["Akosua Danso", "Kojo Mensa"] },
  { code: "0153", name: "Summit Mutual Bank", type: "universal_bank", status: "live", channel: "api", software: INHOUSE, supervisor: "K. Owusu", client_id: "smb-prod-01", cert: "2027-07-30T23:59:59.000Z", users: 2, register_status: "live", contacts: ["Esi Takyi", "Yaw Acheampong"] },
  { code: "0304", name: "Riverbend Rural Bank", type: "rural_bank", status: "live", channel: "portal_upload", software: null, supervisor: "K. Owusu", client_id: null, cert: null, users: 2, register_status: "live", contacts: ["Adjoa Kyei", "Kwame Ofosu"] },
  { code: "0231", name: "Northgate Savings & Loans", type: "savings_and_loans", status: "live", channel: "portal_upload", software: null, supervisor: "A. Boateng", client_id: null, cert: null, users: 3, register_status: "live", contacts: ["Abena Darko", "Yaw Mensah"] },
  { code: "0412", name: "Equinox Microfinance", type: "microfinance", status: "sandbox", channel: "api", software: INHOUSE, supervisor: null, client_id: "eqx-sbx-01", cert: "2027-05-31T23:59:59.000Z", users: 1, register_status: "enrolled", contacts: ["Mansa Owusu", "Kobina Eshun"] },
  { code: "0236", name: "Crestview Savings & Loans", type: "savings_and_loans", status: "sandbox", channel: "api", software: AEQUOR, supervisor: null, client_id: "cvs-sbx-01", cert: "2027-06-30T23:59:59.000Z", users: 2, register_status: "enrolled", contacts: ["Araba Essel", "Ekow Quaye"] },
  { code: "0161", name: "Brightwater Bank", type: "universal_bank", status: "requested", channel: null, software: null, supervisor: null, client_id: null, cert: null, users: 0, register_status: "enrolled", contacts: ["Dzifa Adzo", "Senyo Kumah"] },
];

export function seedByName(name: string): InstitutionSeed {
  const seed = INSTITUTION_SEEDS.find((s) => s.name === name);
  if (!seed) throw new Error(`Unknown fixture institution ${name}`);
  return seed;
}

// ---------------------------------------------------------------------------
// The overview heatmap (Main.dc.html)
// ---------------------------------------------------------------------------

export const HEAT_RETURN_CODES = ["BSD2", "BSD3A", "BSD5A", "BSD6", "BSD7A", "BSD8", "BSD13", "CAR-RWA", "LMT", "NPL-MONTHLY"];

const HEAT_PATTERNS: [string, string][] = [
  ["Northgate Savings & Loans", "OXXLOXOOLX"],
  ["Harbourline Bank", "OOXOOOLOXO"],
  ["Riverbend Rural Bank", "OLOOXOOOOL"],
  ["Kestrel Bank", "OOOOOLOXOO"],
  ["Palmgrove Bank", "LOOOOOOOXO"],
  ["Savanna Union Bank", "OOOLOOOOOX"],
  ["Cedarwood Bank", "OOOOOOLOOO"],
  ["Meridian Coast Bank", "OOOOOOOOOL"],
  ["Ridgeway Commercial Bank", "OOOOOOOOOO"],
  ["Lakeside Trust Bank", "OOLOOOOOOO"],
  ["Goldfield Commercial Bank", "OOOOOOOOOO"],
  ["Summit Mutual Bank", "OOOOONOOON"],
];

const MARK: Record<string, HeatState> = { O: "on_time", L: "late", X: "outstanding", N: "not_due" };

/** Cells that open a filing the design draws elsewhere. */
const DESIGNED_RECEIPTS: Record<string, string> = {
  "Northgate Savings & Loans|BSD2": "SRG-26-0114480",
  "Harbourline Bank|LMT": "SRG-26-0114771",
  "Riverbend Rural Bank|BSD8": "SRG-26-0114702",
  "Kestrel Bank|BSD5A": "SRG-26-0114512",
  "Palmgrove Bank|BSD2": "SRG-26-0114806",
  "Savanna Union Bank|BSD13": "SRG-26-0114655",
  "Cedarwood Bank|NPL-MONTHLY": "SRG-26-0114577",
  "Meridian Coast Bank|BSD2": "SRG-26-0114822",
  "Ridgeway Commercial Bank|CAR-RWA": "SRG-26-0114819",
  "Lakeside Trust Bank|BSD6": "SRG-26-0114760",
  "Goldfield Commercial Bank|BSD3A": "SRG-26-0114455",
};

// ---------------------------------------------------------------------------
// Submissions (Inbox.dc.html, Main.dc.html "Latest submissions")
// ---------------------------------------------------------------------------

type RowSeed = [string, string, string, string, string, number, SubmissionState, string | null, string, SubmissionRow["validation"]];

const v = (passed: number, total: number): SubmissionRow["validation"] => ({ passed, total, failed: total - passed });

/** [received_at, institution, return_code, return_name, reporting_date, version, status, reviewer, receipt, validation] */
const INBOX_SEEDS: RowSeed[] = [
  ["2026-11-21T16:38:04.000Z", "Meridian Coast Bank", "BSD2", "Statement of Assets and Liabilities", CYCLE_DATE, 1, "validating", null, "SRG-26-0114822", null],
  ["2026-11-21T16:21:12.000Z", "Ridgeway Commercial Bank", "CAR-RWA", "Capital Adequacy (CAR & RWA)", CYCLE_DATE, 1, "accepted", "K. Owusu", "SRG-26-0114819", v(62, 62)],
  ["2026-11-21T15:57:12.418Z", "Palmgrove Bank", "BSD2", "Statement of Assets and Liabilities", CYCLE_DATE, 2, "in_review", "A. Boateng", "SRG-26-0114806", v(58, 58)],
  ["2026-11-21T14:12:09.000Z", "Harbourline Bank", "LMT", "Liquidity Monitoring Tools", CYCLE_DATE, 1, "rejected", "Automatic", "SRG-26-0114771", v(68, 71)],
  ["2026-11-21T13:40:51.000Z", "Lakeside Trust Bank", "BSD6", "Maturity Analysis", CYCLE_DATE, 1, "accepted", "E. Asante", "SRG-26-0114760", v(40, 40)],
  ["2026-11-21T11:02:30.000Z", "Riverbend Rural Bank", "BSD8", "Advances Subject to Adverse Classification", CYCLE_DATE, 1, "queried", "K. Owusu", "SRG-26-0114702", v(24, 24)],
  ["2026-11-21T09:05:41.000Z", "Savanna Union Bank", "BSD13", "Net Open Position", CYCLE_DATE, 1, "accepted", "A. Boateng", "SRG-26-0114655", v(18, 18)],
  ["2026-11-21T08:47:19.000Z", "Goldfield Commercial Bank", "DBK-DAILY", "Daily Bank Return", "2026-11-20", 1, "accepted", "Automatic", "SRG-26-0114649", v(12, 12)],
  ["2026-11-20T17:55:03.000Z", "Palmgrove Bank", "BSD2", "Statement of Assets and Liabilities", CYCLE_DATE, 1, "superseded", "A. Boateng", "SRG-26-0114590", v(58, 58)],
  ["2026-11-20T16:30:27.000Z", "Cedarwood Bank", "NPL-MONTHLY", "Monthly Non-Performing Loans", CYCLE_DATE, 1, "in_review", "E. Asante", "SRG-26-0114577", v(31, 31)],
  ["2026-11-20T15:12:44.000Z", "Summit Mutual Bank", "LE-MONTHLY", "Large Exposures", CYCLE_DATE, 1, "rejected", "Automatic", "SRG-26-0114541", v(32, 33)],
  ["2026-11-20T14:03:38.000Z", "Kestrel Bank", "BSD5A", "Capital Adequacy Return", CYCLE_DATE, 1, "accepted", "K. Owusu", "SRG-26-0114512", v(44, 44)],
  ["2026-11-20T11:48:15.000Z", "Northgate Savings & Loans", "BSD2", "Statement of Assets and Liabilities", CYCLE_DATE, 1, "queried", "A. Boateng", "SRG-26-0114480", v(56, 58)],
  ["2026-11-20T10:15:52.000Z", "Goldfield Commercial Bank", "BSD3A", "Large Exposures — Advances and Deposits", CYCLE_DATE, 1, "accepted", "E. Asante", "SRG-26-0114455", v(20, 20)],
];

function row(seed: RowSeed): SubmissionRow {
  const [received_at, institution, return_code, return_name, reporting_date, version, status, reviewer_name, receipt_id, validation] = seed;
  const inst = seedByName(institution);
  return {
    receipt_id,
    received_at,
    institution_code: inst.code,
    institution_name: inst.name,
    return_code,
    return_name,
    reporting_date,
    version,
    channel: inst.channel ?? "api",
    software_declared: inst.channel === "portal_upload" ? null : inst.software,
    validation,
    status,
    reviewer_name,
  };
}

export function buildInboxRows(): SubmissionRow[] {
  return INBOX_SEEDS.map(row);
}

/** The design's tab counts for the October 2026 cycle. */
export const INBOX_COUNTS: SubmissionList["counts"] = {
  all: 571,
  needs_review: 21,
  validating: 4,
  rejected: 12,
  queried: 7,
  accepted: 538,
};

const REVIEWERS = ["K. Owusu", "A. Boateng", "E. Asante", "Automatic"];

/**
 * The overview heatmap plus every filing its cells open. Cells the design
 * does not tie to a drawn filing get synthetic, accepted filings received
 * before the design's inbox page, so the inbox's first page stays exact.
 */
export function buildHeatmap(rows: SubmissionRow[]): { heatmap: Overview["heatmap"]; generated: SubmissionRow[] } {
  const generated: SubmissionRow[] = [];
  let sequence = 113_001;
  const rowsOut = HEAT_PATTERNS.map(([name, pattern], r) => {
    const inst = seedByName(name);
    const cells = pattern.split("").map((mark, i) => {
      const return_code = HEAT_RETURN_CODES[i] ?? "BSD2";
      const state = MARK[mark] ?? "not_due";
      const designed = DESIGNED_RECEIPTS[`${name}|${return_code}`];
      let receipt_id: string | null = designed ?? null;
      if (!receipt_id && (state === "on_time" || state === "late")) {
        receipt_id = `SRG-26-0${sequence}`;
        sequence += 1;
        const day = state === "late" ? 14 + ((r + i) % 5) : 10 + ((r + i) % 4);
        const hour = 8 + ((r * 3 + i) % 9);
        const total = 18 + ((r * 7 + i * 5) % 50);
        generated.push({
          receipt_id,
          received_at: `2026-11-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String((r * 11 + i * 7) % 60).padStart(2, "0")}:00.000Z`,
          institution_code: inst.code,
          institution_name: inst.name,
          return_code,
          return_name: RETURN_NAMES[return_code] ?? return_code,
          reporting_date: CYCLE_DATE,
          version: 1,
          channel: inst.channel ?? "api",
          software_declared: inst.channel === "portal_upload" ? null : inst.software,
          validation: v(total, total),
          status: "accepted",
          reviewer_name: REVIEWERS[(r + i) % REVIEWERS.length] ?? "Automatic",
        });
      }
      return { return_code, state, receipt_id };
    });
    const due = cells.filter((c) => c.state !== "not_due").length;
    const received = cells.filter((c) => c.state === "on_time" || c.state === "late").length;
    return { institution_code: inst.code, institution_name: inst.name, cells, received, due };
  });
  void rows;
  return { heatmap: { return_codes: [...HEAT_RETURN_CODES], rows: rowsOut }, generated };
}

export function buildOverview(inbox: SubmissionRow[], heatmap: Overview["heatmap"]): Overview {
  const latestIds = [
    "SRG-26-0114822",
    "SRG-26-0114819",
    "SRG-26-0114806",
    "SRG-26-0114771",
    "SRG-26-0114760",
    "SRG-26-0114702",
    "SRG-26-0114655",
    "SRG-26-0114649",
  ];
  const attention: AttentionItem[] = [
    { kind: "outstanding", when: "8 days past due", title: "Northgate Savings & Loans — BSD5A Capital Adequacy", detail: "Not received for 31 Oct 2026. Three other returns also outstanding.", link: { type: "institution", ref: "0231" } },
    { kind: "rejected", when: "Today, 14:12", title: "Harbourline Bank — LMT failed 3 validation rules", detail: "Totals in Table 4 do not reconcile to Table 1. Refile requested automatically.", link: { type: "submission", ref: "SRG-26-0114771" } },
    { kind: "queried", when: "Reply due 24 Nov", title: "Riverbend Rural Bank — BSD8 classification query", detail: "Supervisor asked for the basis of two upgraded facilities.", link: { type: "query", ref: "QRY-2026-0318" } },
    { kind: "late", when: "Today, 09:05", title: "Savanna Union Bank — BSD13 filed 3 days late", detail: "Accepted. Lateness recorded against the institution.", link: { type: "submission", ref: "SRG-26-0114655" } },
    { kind: "credential", when: "Expires 30 Nov", title: "Kestrel Bank — client certificate expires in 9 days", detail: "Renewal request sent to the institution’s technical contact.", link: { type: "onboarding", ref: "0133" } },
  ];
  return {
    reporting_date: CYCLE_DATE,
    totals: { due: 612, received: 571, accepted: 538, outstanding_past_due: 41, filed_late: 24, open_queries: 7, institutions: 48, returns: 14 },
    heatmap,
    attention,
    channel: { api_availability_30d: 99.97, median_receipt_seconds: 1.8, filings_today: 214, certificates_expiring_30d: 3 },
    latest: latestIds.map((id) => {
      const found = inbox.find((r) => r.receipt_id === id);
      if (!found) throw new Error(`Missing fixture row ${id}`);
      return found;
    }),
  };
}

// ---------------------------------------------------------------------------
// Submission detail (Submission.dc.html for SRG-26-0114806; others built alike)
// ---------------------------------------------------------------------------

const BSD2_LINES: [string, string, number, number, number, boolean, boolean][] = [
  ["1.1", "Cash and balances with the central bank", 412350, 412350, 398120, false, false],
  ["1.2", "Due from banks", 286400, 290600, 301200, true, false],
  ["1.3", "Investments", 1184900, 1180700, 1121400, true, false],
  ["1.4", "Loans and advances (net)", 2031770, 2031770, 1998240, false, false],
  ["1.5", "Other assets", 143210, 143210, 139880, false, false],
  ["1", "Total assets", 4058630, 4058630, 3958840, false, true],
  ["2.1", "Deposits", 3112480, 3112480, 3031900, false, false],
  ["2.2", "Borrowings", 214900, 214900, 220400, false, false],
  ["2.3", "Other liabilities", 186050, 186050, 168140, false, false],
  ["2.4", "Shareholders’ funds", 545200, 545200, 538400, false, false],
  ["2", "Total liabilities and equity", 4058630, 4058630, 3958840, false, true],
];

const TEMPLATES: Record<string, [string, string, boolean][]> = {
  BSD2: BSD2_LINES.map(([code, label, , , , , total]) => [code, label, total]),
  "CAR-RWA": [
    ["1.1", "Common Equity Tier 1 capital", false],
    ["1.2", "Additional Tier 1 capital", false],
    ["1.3", "Tier 2 capital", false],
    ["1", "Total regulatory capital", true],
    ["2.1", "Credit risk-weighted assets", false],
    ["2.2", "Market risk-weighted assets", false],
    ["2.3", "Operational risk-weighted assets", false],
    ["2", "Total risk-weighted assets", true],
  ],
  LMT: [
    ["1.1", "Level 1 high-quality liquid assets", false],
    ["1.2", "Level 2A high-quality liquid assets", false],
    ["1.3", "Level 2B high-quality liquid assets", false],
    ["1", "Total high-quality liquid assets", true],
    ["4.1", "Retail deposit outflows", false],
    ["4.2", "Wholesale funding outflows", false],
    ["4.3", "Inflows from performing exposures", false],
    ["4", "Net cash outflows, 30 days", true],
  ],
};

const GENERIC: [string, string, boolean][] = [
  ["1.1", "Reported balance, part A", false],
  ["1.2", "Reported balance, part B", false],
  ["1.3", "Reported balance, part C", false],
  ["1", "Total", true],
];

function syntheticLines(r: SubmissionRow): ContentLine[] {
  const template = TEMPLATES[r.return_code] ?? GENERIC;
  const seed = Number.parseInt(sha256Hex(r.receipt_id).slice(0, 8), 16);
  const lines: ContentLine[] = [];
  let section: { value: number; prior: number }[] = [];
  template.forEach(([code, label, total], i) => {
    if (total) {
      const sum = section.reduce((acc, l) => acc + l.value, 0);
      const prior = section.reduce((acc, l) => acc + l.prior, 0);
      lines.push({ code, label, value: String(sum), previous_version_value: null, prior_period_value: String(prior), is_total: true, changed: false });
      section = [];
    } else {
      const value = 40_000 + ((seed >> (i % 16)) % 900_000);
      const prior = Math.round(value * 0.97);
      lines.push({ code, label, value: String(value), previous_version_value: null, prior_period_value: String(prior), is_total: false, changed: false });
      section.push({ value, prior });
    }
  });
  return lines;
}

export function buildSubmissionDetail(r: SubmissionRow, all: SubmissionRow[], institution: InstitutionListItem | undefined): SubmissionDetail {
  const inst = INSTITUTION_SEEDS.find((s) => s.code === r.institution_code);
  const versions = all
    .filter((o) => o.institution_code === r.institution_code && o.return_code === r.return_code && o.reporting_date === r.reporting_date)
    .sort((a, b) => b.version - a.version)
    .map((o) => ({ version: o.version, receipt_id: o.receipt_id, received_at: o.received_at, status: o.status, current: o.status !== "superseded" }));
  const previous = versions.find((x) => x.version === r.version - 1);
  const isPalmgrove = r.receipt_id === "SRG-26-0114806";
  const received = new Date(r.received_at).getTime();
  const at = (deltaMs: number) => new Date(received + deltaMs).toISOString();
  const total = r.validation?.total ?? 0;
  const failed = r.validation?.failed ?? 0;
  const ruleSet = `${r.return_code} · 2026.1`;
  const groups = [
    { label: "Structure", share: 0.2, note: "Schema, codes, units" },
    { label: "Arithmetic", share: 0.38, note: "Totals and subtotals" },
    { label: "Cross-return", share: 0.24, note: r.return_code === "BSD2" ? "Against BSD5A and BSD6" : "Against related returns" },
    { label: "Period", share: 0.18, note: "Reporting date and continuity" },
  ];
  let assigned = 0;
  const builtGroups = groups.map((g, i) => {
    const size = i === groups.length - 1 ? total - assigned : Math.round(total * g.share);
    assigned += size;
    const groupFailed = i === 1 ? Math.min(failed, size) : 0;
    return { label: g.label, passed: size - groupFailed, total: size, note: g.note };
  });

  let lines: ContentLine[];
  let issues: SubmissionDetail["validation"]["issues"] = [];
  if (r.return_code === "BSD2" && (isPalmgrove || r.receipt_id === "SRG-26-0114590")) {
    lines = BSD2_LINES.map(([code, label, v2, v1, prior, changed, is_total]) => ({
      code,
      label,
      value: String(isPalmgrove ? v2 : v1),
      previous_version_value: isPalmgrove ? String(v1) : null,
      prior_period_value: String(prior),
      is_total,
      changed: isPalmgrove && changed,
    }));
  } else {
    lines = syntheticLines(r);
  }
  if (isPalmgrove) {
    issues = [{ rule_id: "BSD2-PER-004", severity: "warning", pointer: "/lines/1.3", message: "Line 1.3 Investments differs from version 1 by GHS 4,200k. Not blocking; the institution’s explanation is attached to the amendment." }];
  } else if (failed > 0) {
    issues = Array.from({ length: failed }, (_, i) => ({
      rule_id: `${r.return_code}-ARI-${String(10 + i).padStart(3, "0")}`,
      severity: r.status === "rejected" ? ("error" as const) : ("warning" as const),
      pointer: `/lines/4.${i + 1}`,
      message:
        r.return_code === "LMT"
          ? `Table 4 line 4.${i + 1} does not reconcile to the matching Table 1 total.`
          : `Line 4.${i + 1} does not equal the sum of its parts within the allowed tolerance.`,
    }));
  }

  const keyPrefix = r.institution_name.split(/\s+/).map((w) => w[0]?.toLowerCase() ?? "").join("").slice(0, 3);
  const kid = isPalmgrove || r.institution_code === "0117" ? "pgb-sign-2026-01" : `${keyPrefix}-sign-2026-01`;
  const reviewer = r.reviewer_name;

  const activity: SubmissionDetail["activity"] = isPalmgrove
    ? [
        { at: "2026-11-21T16:10:22.000Z", title: "Opened by A. Boateng", detail: "Changes-from-v1 view" },
        { at: "2026-11-21T16:02:00.000Z", title: "Assigned to A. Boateng", detail: "Rule: relationship supervisor for Palmgrove Bank" },
        { at: "2026-11-21T15:57:15.000Z", title: "Validation passed with 1 warning", detail: "58 of 58 rules · rule set BSD2 2026.1" },
        { at: "2026-11-21T15:57:12.418Z", title: "Receipt issued", detail: "Signed receipt returned to the institution in 1.6 s" },
        { at: "2026-11-21T15:57:12.418Z", title: "Signature and certificate verified", detail: "Institution key pgb-sign-2026-01 · registered 2 Feb 2026" },
        { at: "2026-11-21T15:57:12.418Z", title: "Amendment received", detail: "Supersedes SRG-26-0114590 with stated reason" },
      ]
    : [
        ...(r.status === "accepted" ? [{ at: at(46 * 60_000), title: `Accepted by ${reviewer === "Automatic" || !reviewer ? "automatic rule" : reviewer}`, detail: "No open issues" }] : []),
        ...(r.status === "rejected" ? [{ at: at(4_000), title: `Rejected: ${failed} blocking ${failed === 1 ? "rule" : "rules"} failed`, detail: "The institution was told to refile as a new version" }] : []),
        ...(r.status === "queried" ? [{ at: at(28 * 60_000), title: "Query raised", detail: "The institution has been asked to explain the return" }] : []),
        ...(r.validation
          ? [{ at: at(3_000), title: failed ? `Validation finished: ${failed} failed` : "Validation passed", detail: `${r.validation.passed} of ${r.validation.total} rules · rule set ${r.return_code} 2026.1` }]
          : [{ at: at(1_000), title: "Validation running", detail: `Rule set ${r.return_code} 2026.1` }]),
        { at: r.received_at, title: "Receipt issued", detail: "Signed receipt returned to the institution in 1.4 s" },
        { at: r.received_at, title: "Signature and certificate verified", detail: `Institution key ${kid}` },
      ];

  return {
    row: r,
    return_version: "2026.1",
    basis: "solo",
    amendment:
      r.version > 1 && previous
        ? {
            supersedes_receipt_id: previous.receipt_id,
            supersedes_received_at: previous.received_at,
            reason: isPalmgrove
              ? "Reclassified GHS 4.2m of placements from Due from banks to Investments after year-to-date review."
              : "Corrected figures after internal review.",
          }
        : null,
    validation: {
      rule_set: ruleSet,
      ran_at: at(isPalmgrove ? 2_582 : 2_000),
      duration_ms: isPalmgrove ? 2_900 : 2_100,
      groups: r.validation ? builtGroups : [],
      issues,
    },
    content: { currency: "GHS", unit: "thousands", sections: [], lines },
    evidence: {
      receipt_id: r.receipt_id,
      payload_sha256: isPalmgrove ? "9f2c41ab77e0d5c318be6a904f2d7c11a3e95b0c6d42f8e7205b9ad17c3e6f04" : sha256Hex(`payload:${r.receipt_id}`),
      signature:
        r.channel === "portal_upload"
          ? { valid: true, alg: "ES256", kid: `${keyPrefix}-portal-2026-01`, key_registered_at: "2026-02-02T09:00:00.000Z", custody_note: "Held by the institution’s portal signing service" }
          : { valid: true, alg: "ES256", kid, key_registered_at: "2026-02-02T09:00:00.000Z", custody_note: "Held in the institution’s own key service" },
      certificate: { valid: true, not_after: inst?.cert ?? "2027-03-14T23:59:59.000Z" },
      receipt_signature: { valid: true, kid: "gateway-receipt-2026-q4" },
      received_at: r.received_at,
      ledger_seq: isPalmgrove ? 1_148_206 : 1_100_000 + (Number.parseInt(r.receipt_id.slice(-6), 10) % 48_000),
      chain_intact: true,
    },
    declaration: {
      prepared_by_role: "Head of Financial Reporting",
      approved_by_role: "Chief Finance Officer",
      approved_at: isPalmgrove ? "2026-11-21T15:51:00.000Z" : at(-6 * 60_000),
      software: r.software_declared,
    },
    institution: {
      code: r.institution_code,
      name: r.institution_name,
      type: inst?.type ?? institution?.type ?? "universal_bank",
      supervisor_name: inst?.supervisor ?? null,
      cycle_received: isPalmgrove ? 9 : 10 - (institution?.outstanding ?? 0),
      cycle_due: 10,
      outstanding: isPalmgrove ? ["LMT, due 13 Nov 2026"] : [],
    },
    versions,
    activity,
    allowed_actions: [],
    documents: [],
  };
}

// ---------------------------------------------------------------------------
// Queries (Queries.dc.html)
// ---------------------------------------------------------------------------

export type MockQuery = QueryDetail & { last_activity_at: string };

type QuerySeed = {
  number: string;
  institution: string;
  receipt_id: string;
  return_code: string;
  subject: string;
  kind: QueryDetail["kind"];
  status: QueryDetail["status"];
  opened_at: string;
  last_activity_at: string;
  due_at: string | null;
  opened_by: string;
  question: string;
  reply: string | null;
  reply_at: string | null;
};

const QUERY_SEEDS: QuerySeed[] = [
  {
    number: "QRY-2026-0318",
    institution: "Riverbend Rural Bank",
    receipt_id: "SRG-26-0114702",
    return_code: "BSD8",
    subject: "Basis for two upgraded facilities in BSD8",
    kind: "query",
    status: "awaiting_institution",
    opened_at: "2026-11-21T11:30:02.000Z",
    last_activity_at: "2026-11-21T11:30:02.000Z",
    due_at: "2026-11-24T17:00:00.000Z",
    opened_by: "K. Owusu",
    question:
      "Two facilities classified Substandard in September are reported as Current in this return: references RB-LN-20417 and RB-LN-20983, together GHS 1.9m.\n\nPlease provide the basis for each upgrade, the repayment record since 30 Sep 2026 and the credit committee minute that approved the change.",
    reply: "Acknowledged. The credit committee minute and repayment schedules are being collated; we will respond in full before the deadline.",
    reply_at: "2026-11-21T15:02:00.000Z",
  },
  {
    number: "QRY-2026-0317",
    institution: "Northgate Savings & Loans",
    receipt_id: "SRG-26-0114480",
    return_code: "BSD2",
    subject: "BSD2 fails 2 cross-checks against BSD5A",
    kind: "resubmission",
    status: "awaiting_supervisor",
    opened_at: "2026-11-20T12:20:00.000Z",
    last_activity_at: "2026-11-20T16:05:00.000Z",
    due_at: "2026-11-24T17:00:00.000Z",
    opened_by: "A. Boateng",
    question: "Lines 3.2 and 3.4 do not agree with the capital figures in your BSD5A for 31 Oct 2026. Please correct the return and refile it as a new version.",
    reply: "We have corrected both lines after reconciling to the capital return and will refile today.",
    reply_at: "2026-11-20T16:05:00.000Z",
  },
  {
    number: "QRY-2026-0316",
    institution: "Harbourline Bank",
    receipt_id: "SRG-26-0114771",
    return_code: "LMT",
    subject: "LMT Table 4 does not reconcile to Table 1",
    kind: "resubmission",
    status: "awaiting_institution",
    opened_at: "2026-11-20T14:30:00.000Z",
    last_activity_at: "2026-11-20T14:30:00.000Z",
    due_at: "2026-11-25T17:00:00.000Z",
    opened_by: "K. Owusu",
    question: "The totals in Table 4 of your LMT for 31 Oct 2026 do not reconcile to Table 1. Please correct the return and refile it.",
    reply: null,
    reply_at: null,
  },
  {
    number: "QRY-2026-0309",
    institution: "Kestrel Bank",
    receipt_id: "", // resolved from the heatmap (a generated filing)
    return_code: "BSD13",
    subject: "Explain FX swap volumes in BSD13",
    kind: "query",
    status: "overdue",
    opened_at: "2026-11-16T10:00:00.000Z",
    last_activity_at: "2026-11-18T09:40:00.000Z",
    due_at: "2026-11-19T17:00:00.000Z",
    opened_by: "K. Owusu",
    question: "FX swap volumes in your BSD13 rose by 64% against September. Please explain the increase and list the counterparties involved.",
    reply: null,
    reply_at: null,
  },
  {
    number: "QRY-2026-0305",
    institution: "Cedarwood Bank",
    receipt_id: "SRG-26-0114577",
    return_code: "NPL-MONTHLY",
    subject: "NPL ratio movement in September",
    kind: "query",
    status: "awaiting_institution",
    opened_at: "2026-11-17T11:15:00.000Z",
    last_activity_at: "2026-11-17T11:15:00.000Z",
    due_at: "2026-11-26T17:00:00.000Z",
    opened_by: "E. Asante",
    question: "Your NPL ratio fell from 14.2% to 11.8% between August and September. Please explain the write-offs and recoveries behind the movement.",
    reply: null,
    reply_at: null,
  },
  {
    number: "QRY-2026-0304",
    institution: "Summit Mutual Bank",
    receipt_id: "SRG-26-0114541",
    return_code: "LE-MONTHLY",
    subject: "Large exposure to one connected group",
    kind: "query",
    status: "awaiting_supervisor",
    opened_at: "2026-11-15T09:30:00.000Z",
    last_activity_at: "2026-11-17T10:20:00.000Z",
    due_at: "2026-11-20T17:00:00.000Z",
    opened_by: "K. Owusu",
    question: "Exposures to the connected group reported in lines 2.1 to 2.3 together exceed the large exposure limit. Please confirm the group structure and your plan to reduce the exposure.",
    reply: "The group structure chart and a reduction plan are attached to our response through the portal.",
    reply_at: "2026-11-17T10:20:00.000Z",
  },
  {
    number: "QRY-2026-0298",
    institution: "Savanna Union Bank",
    receipt_id: "SRG-26-0114655",
    return_code: "BSD13",
    subject: "Late BSD13: confirm controls remediation",
    kind: "query",
    status: "awaiting_institution",
    opened_at: "2026-11-15T13:10:00.000Z",
    last_activity_at: "2026-11-15T13:10:00.000Z",
    due_at: "2026-11-28T17:00:00.000Z",
    opened_by: "A. Boateng",
    question: "BSD13 was filed late again this cycle. Please confirm the control changes you have made so that it is filed on time from November.",
    reply: null,
    reply_at: null,
  },
  {
    number: "QRY-2026-0287",
    institution: "Meridian Coast Bank",
    receipt_id: "", // resolved from the heatmap (a generated filing)
    return_code: "BSD7A",
    subject: "Fee income classification in BSD7A",
    kind: "query",
    status: "closed",
    opened_at: "2026-11-11T10:00:00.000Z",
    last_activity_at: "2026-11-13T15:45:00.000Z",
    due_at: "2026-11-14T17:00:00.000Z",
    opened_by: "K. Owusu",
    question: "Please confirm whether card scheme fees are reported as fee income or other income in BSD7A.",
    reply: "Card scheme fees are reported as fee income, line 3.2, consistent with prior periods.",
    reply_at: "2026-11-13T15:45:00.000Z",
  },
];

function supervisorFullName(short: string): string {
  return ({ "K. Owusu": "Kofi Owusu", "A. Boateng": "Akosua Boateng", "E. Asante": "Esi Asante" } as Record<string, string>)[short] ?? short;
}

export function buildQueries(heatmap: Overview["heatmap"]): MockQuery[] {
  return QUERY_SEEDS.map((q) => {
    const inst = seedByName(q.institution);
    const receiptId =
      q.receipt_id ||
      heatmap.rows.find((r) => r.institution_code === inst.code)?.cells.find((c) => c.return_code === q.return_code)?.receipt_id;
    if (!receiptId) throw new Error(`No fixture filing for ${q.number}`);
    const delivered = new Date(new Date(q.opened_at).getTime() + 60_000);
    delivered.setUTCSeconds(0, 0);
    const deliveredAt = delivered.toISOString();
    const viaApi = inst.channel === "api";
    const messages: MockQuery["messages"] = [
      { id: `${q.number}-m1`, author: "supervisor", author_label: supervisorFullName(q.opened_by), author_role: "Supervisor", body: q.question, created_at: q.opened_at },
      {
        id: `${q.number}-m2`,
        author: "system",
        author_label: "Gateway",
        author_role: null,
        body:
          q.number === "QRY-2026-0318"
            ? "Delivered via API at 11:31 and acknowledged by the institution’s system · also shown in its portal inbox"
            : viaApi
              ? "Delivered via API and acknowledged by the institution’s system · also shown in its portal inbox"
              : "Shown in the institution’s portal inbox and sent to its compliance contact",
        created_at: deliveredAt,
      },
    ];
    if (q.reply && q.reply_at) {
      messages.push({ id: `${q.number}-m3`, author: "institution", author_label: inst.name, author_role: "Compliance Officer", body: q.reply, created_at: q.reply_at });
    }
    if (q.status === "closed") {
      messages.push({ id: `${q.number}-m4`, author: "system", author_label: "Gateway", author_role: null, body: "Closed by K. Owusu", created_at: q.last_activity_at });
    }
    return {
      number: q.number,
      subject: q.subject,
      kind: q.kind,
      status: q.status,
      institution_code: inst.code,
      institution_name: inst.name,
      receipt_id: receiptId,
      return_code: q.return_code,
      reporting_date: CYCLE_DATE,
      opened_by: q.opened_by,
      opened_at: q.opened_at,
      due_at: q.due_at,
      delivered_at: deliveredAt,
      acknowledged_at: viaApi || q.number === "QRY-2026-0318" ? deliveredAt : null,
      messages,
      last_activity_at: q.last_activity_at,
    };
  });
}

export const QUERY_COUNTS = { open: 7, awaiting_institution: 5, awaiting_supervisor: 2, closed: 64 };

// ---------------------------------------------------------------------------
// Institution list and profiles (Institution.dc.html for Northgate)
// ---------------------------------------------------------------------------

const ON_TIME: Record<string, number> = {
  "0231": 71, "0121": 88, "0304": 90, "0133": 94, "0117": 93, "0142": 89, "0147": 97, "0104": 99, "0109": 100, "0126": 98, "0138": 100, "0153": 96,
};

export function buildInstitutionList(heatmap: Overview["heatmap"]): InstitutionListItem[] {
  return INSTITUTION_SEEDS.map((s) => {
    const heat = heatmap.rows.find((r) => r.institution_code === s.code);
    return {
      code: s.code,
      name: s.name,
      type: s.type,
      status: s.status,
      channel: s.channel,
      software_declared: s.software,
      on_time_12m: ON_TIME[s.code] ?? null,
      outstanding: heat ? heat.cells.filter((c) => c.state === "outstanding" && !c.receipt_id).length : 0,
    };
  });
}

const HISTORY_MONTHS = ["Dec 25", "Jan 26", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"];
const HISTORY_DATES = ["2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-31"];

const NORTHGATE_HISTORY: [string, string][] = [
  ["BSD2", "OOOLOOLOOLO"],
  ["BSD3A", "OOOOLOOLOOX"],
  ["BSD5A", "OOLOOOLOLOX"],
  ["BSD6", "OOOOOLOOOLL"],
  ["BSD7A", "OLOOOOOLOOO"],
  ["BSD8", "OOOOLOOOOLX"],
  ["BSD13", "OOOOOOOLOOO"],
  ["NPL-MONTHLY", "OOLOOOOOLOX"],
];

export function buildInstitutionDetail(
  item: InstitutionListItem,
  heatmap: Overview["heatmap"],
  rows: SubmissionRow[],
  openQueries: number,
): InstitutionDetail {
  const seed = INSTITUTION_SEEDS.find((s) => s.code === item.code);
  const heat = heatmap.rows.find((r) => r.institution_code === item.code);
  const isNorthgate = item.code === "0231";
  const receiptFor = (code: string, date: string) => {
    if (date === CYCLE_DATE) return heat?.cells.find((c) => c.return_code === code)?.receipt_id ?? null;
    return `SRG-${date.slice(2, 4)}-0${String(100_000 + (Number.parseInt(sha256Hex(`${item.code}${code}${date}`).slice(0, 6), 16) % 9_000)).padStart(6, "0")}`;
  };
  const historyRows = isNorthgate
    ? NORTHGATE_HISTORY.map(([code, pattern]) => ({
        return_code: code,
        cells: pattern.split("").map((m, i) => {
          const reporting_date = HISTORY_DATES[i] ?? CYCLE_DATE;
          const state = MARK[m] ?? "on_time";
          return { reporting_date, state, receipt_id: state === "outstanding" ? null : receiptFor(code, reporting_date) };
        }),
      }))
    : heat
      ? heat.cells
          .filter((c) => c.state !== "not_due")
          .map((c) => ({
            return_code: c.return_code,
            cells: HISTORY_DATES.map((reporting_date) =>
              reporting_date === CYCLE_DATE
                ? { reporting_date, state: c.state, receipt_id: c.receipt_id }
                : { reporting_date, state: "on_time" as HeatState, receipt_id: receiptFor(c.return_code, reporting_date) },
            ),
          }))
      : [];
  const filings = rows.filter((r) => r.institution_code === item.code).sort((a, b) => b.received_at.localeCompare(a.received_at));
  const last = filings[0];
  const outstandingCodes = heat?.cells.filter((c) => c.state === "outstanding" && !c.receipt_id).map((c) => c.return_code) ?? [];
  const obligations: InstitutionDetail["obligations"] = isNorthgate
    ? [
        { return_code: "BSD5A", return_version: "2026.1", reporting_date: CYCLE_DATE, basis: "solo", due_at: "2026-11-13T17:00:00.000Z", status: "overdue", receipt_id: null },
        { return_code: "BSD3A", return_version: "2026.1", reporting_date: CYCLE_DATE, basis: "solo", due_at: "2026-11-13T17:00:00.000Z", status: "overdue", receipt_id: null },
        { return_code: "BSD8", return_version: "2026.1", reporting_date: CYCLE_DATE, basis: "solo", due_at: "2026-11-13T17:00:00.000Z", status: "overdue", receipt_id: null },
        { return_code: "NPL-MONTHLY", return_version: "2026.1", reporting_date: CYCLE_DATE, basis: "solo", due_at: "2026-11-16T17:00:00.000Z", status: "overdue", receipt_id: null },
        { return_code: "BSD1", return_version: "2026.1", reporting_date: "2026-11-20", basis: "solo", due_at: "2026-11-23T17:00:00.000Z", status: "due", receipt_id: null },
        { return_code: "BSD2", return_version: "2026.1", reporting_date: "2026-11-30", basis: "solo", due_at: "2026-12-14T17:00:00.000Z", status: "not_due", receipt_id: null },
      ]
    : [
        ...outstandingCodes.map((code) => ({ return_code: code, return_version: "2026.1", reporting_date: CYCLE_DATE, basis: "solo" as const, due_at: "2026-11-13T17:00:00.000Z", status: "overdue" as const, receipt_id: null })),
        { return_code: "BSD1", return_version: "2026.1", reporting_date: "2026-11-20", basis: "solo", due_at: "2026-11-23T17:00:00.000Z", status: "due", receipt_id: null },
        { return_code: "BSD2", return_version: "2026.1", reporting_date: "2026-11-30", basis: "solo", due_at: "2026-12-14T17:00:00.000Z", status: "not_due", receipt_id: null },
      ];
  const lateThisYear = isNorthgate ? 19 : Math.round((100 - (item.on_time_12m ?? 100)) * 0.9);
  return {
    code: item.code,
    name: item.name,
    type: item.type,
    channel: item.channel,
    flags: isNorthgate ? ["heightened_monitoring"] : [],
    stats: {
      on_time_12m: item.on_time_12m ?? 0,
      peer_median_on_time_12m: 96,
      outstanding: isNorthgate ? 4 : item.outstanding,
      late_this_year: lateThisYear,
      filings_this_year: isNorthgate ? 88 : 104,
      open_queries: openQueries,
      last_filing: isNorthgate
        ? { at: "2026-11-20T11:48:15.000Z", label: "BSD2 v1 · portal upload" }
        : last
          ? { at: last.received_at, label: `${last.return_code} v${last.version} · ${last.channel === "api" ? "API" : "portal upload"}` }
          : null,
    },
    history: { months: isNorthgate || historyRows.length ? [...HISTORY_MONTHS] : [], rows: historyRows },
    obligations: item.status === "live" ? obligations : [],
    contacts: [
      { role: "Compliance officer", name: seed?.contacts[0] ?? "Not recorded" },
      { role: "Technical contact", name: seed?.contacts[1] ?? "Not recorded" },
    ],
    credentials: {
      portal_users: seed?.users ?? 0,
      mfa: (seed?.users ?? 0) > 0,
      api_client: seed?.client_id ? { client_id: seed.client_id, certificate_not_after: seed.cert ?? "2027-06-30T23:59:59.000Z" } : null,
      signing_keys: seed?.client_id ? [{ kid: item.code === "0117" ? "pgb-sign-2026-01" : `${seed.client_id.split("-")[0]}-sign-2026-01`, status: "active" }] : [],
    },
    supervisor_name: seed?.supervisor ?? null,
    notes: isNorthgate
      ? [{ id: "note-0231-1", author_name: "A. Boateng", body: "Four returns outstanding for October. Institution reports a core banking migration; requested a remediation plan by 28 Nov.", created_at: "2026-11-19T10:05:00.000Z" }]
      : [],
  };
}

// ---------------------------------------------------------------------------
// Filing calendar (Calendar.dc.html for November 2026)
// ---------------------------------------------------------------------------

export function buildCalendar(month: string, today: string): CalendarMonth {
  const cadences = [
    { cadence: "daily", returns: 1 },
    { cadence: "weekly", returns: 6 },
    { cadence: "monthly", returns: 15 },
    { cadence: "quarterly", returns: 5 },
    { cadence: "semiannual", returns: 2 },
    { cadence: "annual", returns: 9 },
  ];
  if (month === "2026-11") {
    return {
      month,
      days: [
        { date: "2026-11-02", events: [{ title: "Weekly returns", detail: "6 returns", tone: "past", received: 288, due: 288 }] },
        { date: "2026-11-09", events: [{ title: "Weekly returns", detail: "6 returns", tone: "past", received: 286, due: 288 }] },
        { date: "2026-11-10", events: [{ title: "Quarterly · 30 Sep", detail: "5 returns", tone: "past", received: 236, due: 240 }] },
        { date: "2026-11-13", events: [{ title: "Monthly · 31 Oct", detail: "14 returns", tone: "warning", received: 571, due: 612 }] },
        {
          date: "2026-11-16",
          events: [
            { title: "Weekly returns", detail: "6 returns", tone: "warning", received: 281, due: 288 },
            { title: "NPL-MONTHLY", detail: "31 Oct", tone: "warning", received: 45, due: 48 },
          ],
        },
        { date: "2026-11-23", events: [{ title: "Weekly returns", detail: "6 returns", tone: "future", received: null, due: 288 }] },
        {
          date: "2026-11-30",
          events: [
            { title: "Weekly returns", detail: "6 returns", tone: "future", received: null, due: 288 },
            { title: "IRRBB-PILOT", detail: "30 Sep · pilot banks", tone: "future", received: null, due: 10 },
          ],
        },
      ],
      upcoming: [
        { label: "Mon 23 Nov", count_label: "288 filings", detail: "Weekly: BSD1, BSD1A, BSD1B, BSD14, BSD15A, BSD15B" },
        { label: "Tue 24 Nov", count_label: "1 query", detail: "Riverbend Rural Bank reply on BSD8" },
        { label: "Mon 30 Nov", count_label: "298 filings", detail: "Weekly returns, and IRRBB-PILOT for 10 pilot banks" },
        { label: "Every business day", count_label: "48 filings", detail: "DBK-DAILY by the published cut-off" },
      ],
      cadences,
    };
  }
  // Other months: weekly returns every Monday and the monthly cycle on the 13th.
  const [y, m] = month.split("-").map(Number) as [number, number];
  const days: CalendarMonth["days"] = [];
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  for (let d = 1; d <= last; d += 1) {
    const date = `${month}-${String(d).padStart(2, "0")}`;
    const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    const tone = date < today ? "past" : "future";
    const events: CalendarMonth["days"][number]["events"] = [];
    if (weekday === 1) events.push({ title: "Weekly returns", detail: "6 returns", tone, received: tone === "past" ? 288 : null, due: 288 });
    if (d === 13) {
      const prev = new Date(Date.UTC(y, m - 1, 0));
      events.push({ title: `Monthly · ${prev.getUTCDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][prev.getUTCMonth()]}`, detail: "14 returns", tone, received: tone === "past" ? 612 : null, due: 612 });
    }
    if (events.length) days.push({ date, events });
  }
  return { month, days, upcoming: [], cadences };
}

// ---------------------------------------------------------------------------
// Onboarding (Onboarding.dc.html)
// ---------------------------------------------------------------------------

const REGISTER_ORDER = ["0104", "0109", "0133", "0121", "0304", "0231", "0412", "0236", "0161"];

export function buildOnboarding(): OnboardingOverview {
  return {
    stages: [
      { stage: "requested", count: 2 },
      { stage: "sandbox", count: 3 },
      { stage: "certified", count: 2 },
      { stage: "live", count: 44 },
    ],
    register: REGISTER_ORDER.map((code) => {
      const s = INSTITUTION_SEEDS.find((x) => x.code === code);
      if (!s) throw new Error(`Missing register seed ${code}`);
      return {
        code: s.code,
        name: s.name,
        type: s.type,
        channel: s.channel,
        software_declared: s.software,
        enrolled_at: "2026-02-02T09:00:00.000Z",
        client_id: s.client_id,
        certificate_not_after: s.cert,
        certificate_expiring: s.code === "0133",
        portal_users: s.users,
        status: s.register_status,
      };
    }),
  };
}

// ---------------------------------------------------------------------------
// Audit (Audit.dc.html)
// ---------------------------------------------------------------------------

type AuditSeed = [number, string, string, string, string, string, string];

const AUDIT_SEEDS: AuditSeed[] = [
  [1_148_412, "2026-11-21T16:38:04.000Z", "Meridian Coast Bank", "API client mcb-prod-01", "filing", "Filed BSD2 v1", "SRG-26-0114822"],
  [1_148_409, "2026-11-21T16:21:40.000Z", "K. Owusu", "Portal · BoG SSO", "decision", "Accepted filing", "SRG-26-0114819"],
  [1_148_401, "2026-11-21T16:10:22.000Z", "A. Boateng", "Portal · BoG SSO", "view", "Opened submission", "SRG-26-0114806"],
  [1_148_395, "2026-11-21T16:02:00.000Z", "System", "Assignment rule", "system", "Assigned reviewer", "SRG-26-0114806"],
  [1_148_388, "2026-11-21T15:57:12.000Z", "Palmgrove Bank", "API client pgb-prod-01", "filing", "Filed amendment v2", "SRG-26-0114806"],
  [1_148_371, "2026-11-21T14:58:40.000Z", "J. Tetteh, operator", "Support · approved session", "operator_access", "Support session started", "SUP-2026-0042"],
  [1_148_352, "2026-11-21T14:12:09.000Z", "System", "Validation service", "system", "Rejected filing, 3 rules", "SRG-26-0114771"],
  [1_148_340, "2026-11-21T13:40:51.000Z", "Lakeside Trust Bank", "Portal upload · 2 users", "filing", "Filed BSD6 v1", "SRG-26-0114760"],
  [1_148_297, "2026-11-21T11:30:02.000Z", "K. Owusu", "Portal · BoG SSO", "query", "Opened query", "QRY-2026-0318"],
  [1_148_201, "2026-11-21T09:02:17.000Z", "K. Owusu", "Portal · BoG SSO · MFA", "sign_in", "Signed in", "session 4f1a…"],
];

/** Which institution an entry concerns, for the institution filter. */
export const AUDIT_INSTITUTION: Record<number, string> = {
  1_148_412: "0104",
  1_148_409: "0109",
  1_148_401: "0117",
  1_148_395: "0117",
  1_148_388: "0117",
  1_148_352: "0121",
  1_148_340: "0126",
  1_148_297: "0304",
};

export const AUDIT_ACTOR_TYPE: Record<number, string> = {
  1_148_412: "institution",
  1_148_409: "staff",
  1_148_401: "staff",
  1_148_395: "system",
  1_148_388: "institution",
  1_148_371: "operator",
  1_148_352: "system",
  1_148_340: "institution",
  1_148_297: "staff",
  1_148_201: "staff",
};

export function buildAuditList(): AuditList {
  return {
    integrity: {
      intact: true,
      entries: 1_148_412,
      verified_at: "2026-11-21T16:30:00.000Z",
      head_hash: "7c1e9a04d2b85f3e6a10c47b9e2d5f8a1c3b07e6d4a9f2c58e1b6d3a7f90b3f2",
    },
    siem: { streaming: true, lag_seconds: 3 },
    retention_label: "[Retention period set by BoG]",
    items: AUDIT_SEEDS.map(([seq, occurred_at, actor_label, actor_via, category, action, object_ref]) => ({ seq, occurred_at, actor_label, actor_via, category, action, object_ref })),
    next_cursor: null,
  };
}

export function buildAuditEntry(item: AuditList["items"][number]): AuditEntry {
  if (item.seq === 1_148_371) {
    return {
      ...item,
      detail: {
        operator: "J. Tetteh, AequorOS support",
        approved_by: "M. Adjei, Bank of Ghana · 14:55",
        scope_and_limit: "Read-only diagnostics, 60 minutes, no filing content",
        reason_given: "Slow receipts reported by two institutions",
      },
      prev_hash: "3b07c2e19d4a5f607e18c3b2a6d90f45c1d8e2b7f04a69e3d5b1c8a7e2f60d94",
      entry_hash: "e52f8a13c7b04d9621fa3e7c58d1b0a26f3e9c1d4b7a08e5f2c6d9b3a1e07f48",
      signature_kid: "gateway-audit-2026-q4",
      signature_valid: true,
    };
  }
  return {
    ...item,
    detail: { action: item.action, object: item.object_ref, recorded_by: item.actor_via },
    prev_hash: sha256Hex(`entry:${item.seq - 1}`),
    entry_hash: sha256Hex(`entry:${item.seq}`),
    signature_kid: "gateway-audit-2026-q4",
    signature_valid: true,
  };
}

// ---------------------------------------------------------------------------
// Access (Access.dc.html)
// ---------------------------------------------------------------------------

export function buildAccess(): AccessOverview {
  return {
    roles: [
      { group: "srg-supervisors", role: "supervisor", can: "Review, accept, query and request resubmission for assigned institutions", members: 18 },
      { group: "srg-senior-supervisors", role: "senior_supervisor", can: "Everything a supervisor can, for all institutions; reassign work", members: 6 },
      { group: "srg-returns-policy", role: "returns_policy", can: "Draft and approve return definitions, with a second approver", members: 4 },
      { group: "srg-access-approvers", role: "access_approver", can: "Approve or deny operator support sessions", members: 3 },
      { group: "srg-internal-audit", role: "auditor", can: "Read-only access to everything, including audit log export", members: 5 },
    ],
    pending: [
      {
        number: "SUP-2026-0043",
        operator_name: "R. Quaye",
        operator_title: "AequorOS on-call engineer",
        scope: "Read-only logs of the validation service",
        excludes: "Filing content, supervisor notes, keys",
        reason: "Receipt times above 5 s since 16:20",
        incident_ref: "INC-2026-0117",
        requested_at: "2026-11-21T16:41:00.000Z",
        expires_at: "2026-11-21T17:41:00.000Z",
      },
    ],
    sessions: [
      { number: "SUP-2026-0042", operator_name: "J. Tetteh", status: "active", detail: "Approved by M. Adjei · 60 min from 14:58" },
      { number: "SUP-2026-0041", operator_name: "R. Quaye", status: "ended", detail: "Approved by E. Asante · 30 min on 18 Nov" },
      { number: "SUP-2026-0040", operator_name: "J. Tetteh", status: "denied", detail: "Requested 12 Nov · reason too broad" },
      { number: "SUP-2026-0039", operator_name: "S. Amoah", status: "expired", detail: "Not answered within 60 minutes" },
    ],
  };
}
