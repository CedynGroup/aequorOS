'use client';

/**
 * "This book does not balance" strip.
 *
 * The live plane deliberately keeps computing on a book that fails the
 * balance-sheet identity — an operator has to SEE the broken book to fix it —
 * so the figures on every other surface are real numbers computed on a plugged
 * balance sheet. This strip is the half that makes that visible. Without it the
 * cockpit would show a CAR that reads exactly like a sound one.
 *
 * It renders nothing at all when the book reconciles, and nothing when the gap
 * is carried by an approved reconciliation exception — that case is governed,
 * dated and bounded, and a strip that speaks on a governed book teaches
 * operators to ignore it.
 */

import { AlertOctagon } from 'lucide-react';
import { useBankContext } from '@/components/shell/BankContext';
import { useLiveSummary } from '@/lib/api/hooks';
import { fmtCurrency, fmtPct } from '@/lib/format';

export default function UnreconciledBookBanner({
  bankId,
}: {
  /** Omit to use the active bank from context (the Data Engine surface). */
  bankId?: string | undefined;
}) {
  const { bank } = useBankContext();
  const live = useLiveSummary(bankId ?? bank?.id);
  const reconciliation = live.data?.reconciliation;
  if (!reconciliation || !reconciliation.blocksFiling) return null;

  const gap = Number(reconciliation.gap);
  const gapPct = Number(reconciliation.gapFraction) * 100;
  const tolerancePct = Number(reconciliation.tolerancePct);
  const shortSide = gap > 0 ? 'Liabilities and equity' : 'Assets';

  return (
    <div className="card border-l-4 border-l-critical bg-critical-light/40 px-5 py-4">
      <div className="flex items-start gap-3 min-w-0">
        <AlertOctagon
          size={18}
          className="text-critical shrink-0 mt-0.5"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-body font-semibold text-navy">
            This book does not balance — no figure below may be filed
          </p>
          <p className="mt-1.5 text-body text-navy/85 leading-relaxed">
            {shortSide} exceed the other side by{' '}
            <span className="font-medium text-navy">
              {fmtCurrency(Math.abs(gap))}
            </span>
            {Number.isFinite(gapPct) ? (
              <> ({fmtPct(gapPct, 2)} of assets)</>
            ) : null}
            , against a{' '}
            {reconciliation.toleranceSource === 'control_plane'
              ? 'governed tolerance'
              : 'default tolerance'}{' '}
            of {Number.isFinite(tolerancePct) ? fmtPct(tolerancePct, 2) : '—'}.
            Every ratio on this page is computed on a balance sheet that was
            forced to add up, so treat them as diagnostics, not as positions.
          </p>
          <p className="mt-1.5 text-caption text-slate leading-relaxed">
            Reconcile the general ledger against the sub-ledgers and re-ingest,
            or record an approved reconciliation exception. Regulatory returns,
            approvals, certifications and submissions are refused until then.
          </p>
        </div>
      </div>
    </div>
  );
}
