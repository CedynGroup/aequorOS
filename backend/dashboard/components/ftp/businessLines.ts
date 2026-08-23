/**
 * Client-side business-line VIEW over the FTP product rows.
 *
 * PRESENTATIONAL ONLY — this module is not an alternate authority for any FTP
 * figure. The FTP engine (`backend/app/domain/ftp/engine.py`, surfaced as
 * `FtpProductRead` / `FtpBranchRead`) prices PRODUCTS and reports BRANCHES; it
 * has no business-line dimension and publishes no line-level margin. There is
 * therefore no backend figure this view can be reconciled against, which is
 * exactly why it must never read as an authoritative FTP margin
 * (see the forensic calculation-architecture audit, §5 divergence register:
 * "FTP business-line margin — presentational shadow calculation").
 *
 * What this module is allowed to do, and nothing more:
 *   - group the backend product rows by a transparent keyword rule
 *     (`GROUPING_RULE`, surfaced verbatim in the UI);
 *   - SUM backend figures within a group (balance, contribution, flag counts);
 *   - divide those two sums to show the margin the grouping implies
 *     (`MARGIN_NOTICE`, also surfaced in the UI).
 *
 * No pricing math is redone here, and no figure produced here may be filed,
 * certified, or compared against a regulatory floor.
 *
 * FAIL CLOSED. `impliedMarginPct` is `number | null`: a line with no positive
 * balance has NO computable margin, and a fabricated `0` would render as a
 * real — and unusually good — margin. Absence stays absence.
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
  /**
   * Σ contribution ÷ Σ balance over this VIEW's grouping — presentational,
   * not an engine output (see the module header and `MARGIN_NOTICE`).
   *
   * `null` when the line carries no positive balance: the ratio is not
   * computable and must be rendered as not computable, never as 0%.
   */
  impliedMarginPct: number | null;
  belowFloorCount: number;
};

export const GROUPING_RULE =
  'Products are grouped by name and category keywords: corporate/SME lending, retail & mortgage lending, treasury & securities (asset side); transactional deposits, term & wholesale funding (liability side). This is a client-side view — the FTP engine prices products, not lines.';

export const MARGIN_NOTICE =
  'The implied margin is this view’s own arithmetic: the summed contribution of the grouped products divided by their summed balance. The FTP engine publishes no business-line margin, so there is no engine figure behind it — read it as a way to compare these groups, not as a priced or reportable margin. A line with no balance shows no margin rather than 0%.';

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
        impliedMarginPct: null,
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
    // No positive balance ⇒ no denominator ⇒ NOT COMPUTABLE. Never 0.
    line.impliedMarginPct =
      line.balanceGhs > 0
        ? (line.contributionGhs / line.balanceGhs) * 100
        : null;
  }

  return [...byKey.values()].sort(
    (a, b) => LINE_ORDER.indexOf(a.key) - LINE_ORDER.indexOf(b.key)
  );
}
