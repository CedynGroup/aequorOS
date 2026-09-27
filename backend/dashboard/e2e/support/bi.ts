/**
 * Shared facts the four `bi-*` journeys assert against.
 *
 * ADDITIVE ONLY. Nothing here mints an identity or writes a fixture row: the
 * identities come from `support/mint.ts` and the book comes from
 * `scripts/e2e_bootstrap.py`. This module holds the two things a BI journey
 * needs and must not guess at — the reporting date the fixture's marts were
 * built at, and the reconciliation verdict that fixture HONESTLY earns.
 *
 * **The date is read from the platform, never computed here.** The bootstrap
 * carries the canonical book forward to the last month end on or before today
 * and seeds the position book at that same date (H-015), so the marts, the live
 * plane and the reporting-period spine all sit on one date that moves with the
 * calendar. A literal here would pass today and fail on the first of next month.
 *
 * **The trust state is asserted, not flattered.** After H-015 the fixture's
 * reconciliation is genuinely MIXED, and the mixture is the product's own answer
 * to a partial book: four checks pass, two report differences, three fail and
 * one was never assessed. A journey that wanted a green overall badge would have
 * to change the book to get one, which would delete the coverage it was trying
 * to claim. See `EXPECTED_CHECKS` for why each one reads the way it does.
 */

import { expect, type APIRequestContext } from "@playwright/test";
import { E2E_API_ORIGIN } from "../../playwright.config";
import { E2E_USERS, mintBackendToken } from "./mint";

export const SAMPLE_BANK_ID = "BK-SAMP0001";

export type BiRole = keyof typeof E2E_USERS;
// Referenced at run time so the import is a value import, which is what
// `keyof typeof` above needs.
export const BI_ROLE_NAMES = Object.keys(E2E_USERS);

/** One authenticated tenant-API call as a fixture identity. */
export async function biApi(
  request: APIRequestContext,
  role: BiRole,
  route: string,
  init: { method?: string; data?: unknown } = {},
): Promise<{ status: number; headers: Record<string, string>; body: unknown }> {
  const response = await request.fetch(`${E2E_API_ORIGIN}/api/v1${route}`, {
    method: init.method ?? "GET",
    data: init.data,
    headers: { Authorization: `Bearer ${await mintBackendToken(role)}` },
  });
  const text = await response.text();
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    // A governed export answers with a CSV body; keep it as text.
  }
  return { status: response.status(), headers: response.headers(), body };
}

/**
 * The reporting date the fixture's analytics were built at: the latest reporting
 * period the platform lists, which the API returns period-end descending.
 */
export async function fixtureAsOf(
  request: APIRequestContext,
): Promise<string> {
  const { status, body } = await biApi(
    request,
    "admin",
    `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
  );
  expect(status).toBe(200);
  const periods = (body as { periods: { period_end: string }[] }).periods;
  expect(periods.length).toBeGreaterThan(0);
  return periods[0].period_end.slice(0, 10);
}

/**
 * Every reconciliation check, with the verdict the fixture earns and the reason
 * it earns it. The labels are the product's own copy
 * (`app/features/read_bi.py::CHECK_LABELS`); the badge text is
 * `components/bi/TrustBadge.tsx`.
 */
export const EXPECTED_CHECKS = [
  {
    id: "R1",
    label: "Non-performing loans agree with the credit engine",
    status: "green",
    badge: "Reconciled",
    why: "the mart's NPL share and the live credit engine's npl_ratio_pct agree exactly",
  },
  {
    id: "R2",
    label: "Loan balances agree with the balance sheet",
    status: "red",
    badge: "Does not reconcile",
    why: "the fixture's LOAN position balances do not sum to its balance-sheet loans_gross fact",
  },
  {
    id: "R3",
    label: "Deposit balances agree with the balance sheet",
    status: "red",
    badge: "Does not reconcile",
    why: "the same gap on the funding side, against the five live deposit lines",
  },
  {
    id: "R4",
    label: "Income-statement lines agree with the regulatory return",
    status: "grey",
    badge: "Not assessed",
    why: "the fixture carries no P&L ledger rows for the month, so the check could not run — and grey is not a pass",
  },
  {
    id: "R5",
    label: "Every position in the book reached the analytics tables",
    status: "green",
    badge: "Reconciled",
    why: "every current snapshot was copied into the marts",
  },
  {
    id: "R6",
    label: "Every balance is stated in the reporting currency",
    status: "amber",
    badge: "Differences found",
    why: "one USD position carries no reporting-currency conversion",
  },
  {
    id: "R7",
    label: "Every balance is attributed to a known branch",
    status: "amber",
    badge: "Differences found",
    why: "part of the book carries no branch at all",
  },
  {
    id: "R8",
    label: "The analytics tables are as current as the live figures",
    status: "green",
    badge: "Reconciled",
    why: "the mart and the live plane are on one date — the H-015 fix",
  },
  {
    id: "R9",
    label: "The balance sheet balances",
    status: "green",
    badge: "Reconciled",
    why: "the platform's own balance-sheet identity control passed",
  },
  {
    id: "R10",
    label: "Arrears ageing is complete",
    status: "red",
    badge: "Does not reconcile",
    why: "no loan in the fixture carries days past due, so the ageing is incomplete",
  },
] as const;

/** The worst verdict across the checks, and therefore the overall badge. */
export const EXPECTED_OVERALL_STATUS = "red";
export const EXPECTED_OVERALL_BADGE = "Does not reconcile";

/**
 * Figures the fixture's book produces, in the units the surfaces show them in.
 * `fmtCurrency` is compact by default (`lib/format.ts`), which is why the screen
 * reads `GHS 81.9M` where the payload carries `81850000.000000`.
 */
export const FIXTURE_FIGURES = {
  grossLoansTotalRaw: "84850000.000000",
  standardGradeRaw: "81850000.000000",
  substandardGradeRaw: "3000000.000000",
  // `fmtCurrency` is compact to one decimal: 81_850_000 / 1e6 is 81.85, whose
  // nearest double is 81.84999…, so `toFixed(1)` gives 81.8 and the screen reads
  // `GHS 81.8M`. Pinned as the product actually renders it rather than as the
  // arithmetic suggests.
  standardGradeOnScreen: "GHS 81.8M",
  substandardGradeOnScreen: "GHS 3.0M",
  // Per-grade NPL ratio: nothing graded standard is non-performing and
  // everything graded substandard is, which is the classification being
  // coherent rather than a coincidence of the fixture.
  standardNplOnScreen: "0.00%",
  substandardNplOnScreen: "100.00%",
} as const;

/**
 * The seven certified content packs, by id and by the title a reader sees.
 * `app/domain/bi/packs/*.json`, validated by `PackSpec`.
 */
export const CERTIFIED_PACKS = [
  { id: "alco", title: "Asset and liability committee" },
  { id: "board", title: "Board" },
  { id: "branch_network", title: "Branch network" },
  { id: "compliance", title: "Compliance" },
  { id: "credit", title: "Credit and collections" },
  { id: "cro", title: "Chief risk officer" },
  { id: "finance", title: "Finance" },
] as const;
