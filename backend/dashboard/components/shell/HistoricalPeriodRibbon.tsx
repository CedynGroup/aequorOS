'use client';

/**
 * Announces time travel: shown only when the selected reporting period is
 * not the live edge (the newest period). The desk silently becomes a
 * history viewer when an older book is selected — this ribbon makes that
 * state explicit and offers the way back, per the three-plane model.
 */

import { History } from 'lucide-react';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

export default function HistoricalPeriodRibbon() {
  const { period, periods, setPeriodId } = useBankContext();
  const latest = periods[0];
  if (!period || !latest || period.id === latest.id) return null;

  return (
    <div className="flex items-center gap-3 px-8 py-2 bg-action-light/40 border-b border-action/20">
      <History size={13} className="text-action shrink-0" aria-hidden />
      <p className="text-caption text-navy/85">
        Viewing the <span className="font-mono font-medium">{period.label}</span> book
        (positions as at {fmtDateUTC(period.periodEnd)}) — historical, not live.
      </p>
      <button
        type="button"
        onClick={() => setPeriodId(latest.id)}
        className="ml-auto text-caption font-medium text-action hover:underline whitespace-nowrap"
      >
        Return to live edge →
      </button>
    </div>
  );
}
