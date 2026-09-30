/**
 * Shared facts the `bi-*` journeys assert against.
 *
 * ADDITIVE ONLY. Nothing here mints an identity or writes a fixture row: the
 * identities come from `support/mint.ts` and the book comes from
 * `scripts/e2e_bootstrap.py`. This module holds the things a BI journey needs
 * and must not guess at — above all the reporting date the fixture's marts
 * were built at.
 *
 * **The date is read from the platform, never computed here.** The bootstrap
 * carries the canonical book forward to the last month end on or before today
 * and seeds the position book at that same date (H-015), so the marts, the live
 * plane and the reporting-period spine all sit on one date that moves with the
 * calendar. A literal here would pass today and fail on the first of next month.
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
