/**
 * Locators and API reads shared by the functional risk journeys.
 *
 * Those journeys assert computed FIGURES, so they need to find one figure on a
 * dense page without leaning on layout: a KPI tile by its label, a table row by
 * its first cell. Expectations stay in the specs, written as the fixture
 * numbers they were derived from.
 */

import { expect, type Locator, type Page } from "@playwright/test";
import { E2E_API_ORIGIN } from "../../playwright.config";
import { E2E_USERS, mintBackendToken } from "./mint";

export const SAMPLE_BANK_ID = "BK-SAMP0001";

/**
 * The KPI tile (`components/ui/KpiStat`) whose label is exactly `label`. A
 * section card is a `.card` too, so only the innermost card qualifies.
 */
export function kpi(page: Page | Locator, label: string): Locator {
  return page
    .locator(".card")
    .filter({ has: page.getByText(label, { exact: true }) })
    .filter({ hasNot: page.locator(".card") });
}

/**
 * Assert the KPI tile labelled `label` shows `value`. A `hint` both selects the
 * tile (two tiles may share a label, e.g. a regulatory and a desk figure) and
 * asserts its caption.
 */
export async function expectKpi(
  page: Page | Locator,
  label: string,
  value: string,
  hint?: string,
): Promise<void> {
  const tile =
    hint === undefined
      ? kpi(page, label)
      : kpi(page, label).filter({ hasText: hint });
  await expect(tile).toHaveCount(1);
  await expect(tile.locator("span.font-mono").first()).toHaveText(value);
}

/**
 * `lib/format.ts::fmtCurrency` for an amount given in GHS millions, so a
 * spec can write its expectation as the fixture's own number.
 */
export function ghsM(millions: number): string {
  if (millions === 0) return "GHS 0";
  return Math.abs(millions) >= 1_000
    ? `GHS ${(millions / 1_000).toFixed(1)}B`
    : Math.abs(millions) >= 1
      ? `GHS ${millions.toFixed(1)}M`
      : `GHS ${(millions * 1_000).toFixed(1)}K`;
}

/** `lib/format.ts::fmtCurrencySigned` for an amount in GHS millions. */
export function signedGhsM(millions: number): string {
  return `${millions >= 0 ? "+" : "-"}${ghsM(Math.abs(millions))}`;
}

/**
 * The body row of `table` whose FIRST cell is exactly `key`. Matching the
 * leading cell, not any cell, keeps "1-3m" from also matching "1-3y".
 */
export function tableRow(table: Locator, key: string): Locator {
  return table.locator("tbody tr").filter({
    has: table
      .page()
      .locator("td:first-child", {
        hasText: new RegExp(`^\\s*${escape(key)}\\s*$`),
      }),
  });
}

/**
 * Assert a table row's leading cells read exactly `cells`, in column order. A
 * clickable `DataTable` row carries a trailing affordance cell, which is not a
 * figure and is not compared.
 */
export async function expectRow(
  table: Locator,
  key: string,
  cells: (string | RegExp)[],
): Promise<void> {
  const row = tableRow(table, key);
  await expect(row).toHaveCount(1);
  for (const [index, cell] of cells.entries()) {
    await expect(row.locator("td").nth(index)).toHaveText(cell);
  }
}

/**
 * A read of the backend as a fixture role, for asserting what a journey
 * PERSISTED. The screen proves what the reader saw; this proves what the
 * platform kept.
 */
export async function apiGet<T = any>(
  page: Page,
  role: keyof typeof E2E_USERS,
  pathName: string,
): Promise<T> {
  const response = await page.request.get(
    `${E2E_API_ORIGIN}/api/v1${pathName}`,
    {
      headers: { Authorization: `Bearer ${await mintBackendToken(role)}` },
    },
  );
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as T;
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
