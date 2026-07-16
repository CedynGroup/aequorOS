/**
 * Client-side business-line view over the FTP product rows.
 *
 * The backend prices individual products; it has no business-line dimension.
 * This module groups products into desk-level lines with a transparent,
 * name/category keyword rule (documented in GROUPING_RULE and surfaced in the
 * UI). Aggregation is pure arithmetic over backend figures: balances and
 * contributions are summed, and the line margin is the balance-weighted
 * margin implied by those sums — no pricing math is redone here.
 */

import type { FtpProductRead } from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';

export type BusinessLine = {
  key: string;
  label: string;
  side: 'asset' | 'liability' | 'mixed';
  products: FtpProductRead[];
  balanceGhs: number;
  contributionGhs: number;
  /** Σ contribution ÷ Σ balance — the balance-weighted net margin (%). */
  weightedMarginPct: number;
  belowFloorCount: number;
};

export const GROUPING_RULE =
  'Products are grouped by name and category keywords: corporate/SME lending, retail & mortgage lending, treasury & securities (asset side); transactional deposits, term & wholesale funding (liability side). This is a client-side view — the FTP engine prices products, not lines.';

type LineDef = {
  key: string;
  label: string;
  category: 'asset' | 'liability';
  match: (product: string) => boolean;
};

const LINE_DEFS: LineDef[] = [
  {
    key: 'corporate_sme',
    label: 'Corporate & SME lending',
    category: 'asset',
    match: (p) => /corporate|sme|commercial/.test(p),
  },
  {
    key: 'retail_lending',
    label: 'Retail & mortgage lending',
    category: 'asset',
    match: (p) => /retail|mortgage|consumer|personal/.test(p),
  },
  {
    key: 'treasury',
    label: 'Treasury & securities',
    category: 'asset',
    match: (p) => /securit|gov|treasur|bill|bond|interbank/.test(p),
  },
  {
    key: 'transactional_deposits',
    label: 'Transactional & savings deposits',
    category: 'liability',
    match: (p) => /current|savings|transact|demand/.test(p),
  },
  {
    key: 'term_wholesale',
    label: 'Term & wholesale funding',
    category: 'liability',
    match: (p) => /term|wholesale|fixed|interbank|borrow/.test(p),
  },
];

function lineFor(product: FtpProductRead): { key: string; label: string } {
  const name = product.product.toLowerCase();
  for (const def of LINE_DEFS) {
    if (def.category === product.category && def.match(name)) {
      return { key: def.key, label: def.label };
    }
  }
  return product.category === 'asset'
    ? { key: 'other_assets', label: 'Other asset products' }
    : { key: 'other_funding', label: 'Other funding products' };
}

const LINE_ORDER = [
  'corporate_sme',
  'retail_lending',
  'treasury',
  'other_assets',
  'transactional_deposits',
  'term_wholesale',
  'other_funding',
];

/** Aggregate the FTP product rows into business lines (see GROUPING_RULE). */
export function aggregateBusinessLines(products: FtpProductRead[]): BusinessLine[] {
  const byKey = new Map<string, BusinessLine>();

  for (const product of products) {
    const { key, label } = lineFor(product);
    let line = byKey.get(key);
    if (!line) {
      line = {
        key,
        label,
        side: product.category,
        products: [],
        balanceGhs: 0,
        contributionGhs: 0,
        weightedMarginPct: 0,
        belowFloorCount: 0,
      };
      byKey.set(key, line);
    }
    if (line.side !== product.category) line.side = 'mixed';
    line.products.push(product);
    line.balanceGhs += num(product.balanceGhs);
    line.contributionGhs += num(product.contributionGhs);
    if (product.belowMinMargin) line.belowFloorCount += 1;
  }

  for (const line of byKey.values()) {
    line.weightedMarginPct =
      line.balanceGhs > 0 ? (line.contributionGhs / line.balanceGhs) * 100 : 0;
  }

  return [...byKey.values()].sort(
    (a, b) => LINE_ORDER.indexOf(a.key) - LINE_ORDER.indexOf(b.key)
  );
}
