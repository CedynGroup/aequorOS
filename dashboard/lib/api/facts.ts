/**
 * Pure selectors over the period facts payload for the overview profile bar.
 *
 * These are DISPLAY groupings only (summing canonical balance-sheet facts for
 * headline cards) — no regulatory math is performed client-side.
 */

import type { BankFactRead, BankFactsRead } from '@aequoros/risk-service-api';
import { num } from './values';

function side(fact: BankFactRead): string | undefined {
  const attributes = fact.attributes as { side?: string } | null | undefined;
  return attributes?.side;
}

function sum(facts: BankFactRead[]): number {
  return facts.reduce((total, fact) => total + num(fact.amount), 0);
}

/** Sum of the asset side of the canonical balance sheet. */
export function totalAssets(facts: BankFactsRead): number {
  return sum(facts.balanceSheet.filter((fact) => side(fact) === 'asset'));
}

/** Retail + wholesale deposit balances. */
export function totalDeposits(facts: BankFactsRead): number {
  return sum(
    facts.balanceSheet.filter(
      (fact) =>
        side(fact) === 'liability' &&
        (fact.category.startsWith('retail_deposits') ||
          fact.category.startsWith('wholesale_'))
    )
  );
}

/** Gross loans balance-sheet line. */
export function totalLoans(facts: BankFactsRead): number {
  return sum(facts.balanceSheet.filter((fact) => fact.category === 'loans_gross'));
}

/** Total capital (equity side of the canonical balance sheet). */
export function totalCapital(facts: BankFactsRead): number {
  return sum(
    facts.balanceSheet.filter((fact) => fact.category === 'capital_total')
  );
}
