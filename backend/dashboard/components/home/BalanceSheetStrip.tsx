'use client';

/**
 * Big-4 balance-sheet strip: total assets, deposits, loans, and capital base
 * summed from the effective period's canonical facts (display grouping only —
 * no client-side regulatory math), formatted as compact GHS.
 */

import type { BankReportingPeriodRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankPeriodFacts } from '@/lib/api/hooks';
import {
  totalAssets,
  totalCapital,
  totalDeposits,
  totalLoans,
} from '@/lib/api/facts';
import { fmtCurrency } from '@/lib/format';

export default function BalanceSheetStrip({
  bankId,
  period,
}: {
  bankId: string | undefined;
  period: BankReportingPeriodRead;
}) {
  const facts = useBankPeriodFacts(bankId, period.id);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <h2 className="text-h3 text-navy">Balance sheet</h2>
        <p className="text-caption text-slate">
          Canonical facts · period {period.label}
        </p>
      </div>
      {facts.isLoading || !facts.data ? (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          <KpiStat
            label="Total assets"
            value={fmtCurrency(totalAssets(facts.data))}
            hint="Asset side, canonical"
          />
          <KpiStat
            label="Deposits"
            value={fmtCurrency(totalDeposits(facts.data))}
            hint="Retail + wholesale"
          />
          <KpiStat
            label="Loans (gross)"
            value={fmtCurrency(totalLoans(facts.data))}
            hint="Gross loan book"
          />
          <KpiStat
            label="Capital base"
            value={fmtCurrency(totalCapital(facts.data))}
            hint="Total capital"
          />
        </div>
      )}
    </div>
  );
}
