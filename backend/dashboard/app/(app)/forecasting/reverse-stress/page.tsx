'use client';

import Link from 'next/link';
import { Target } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import { useLatestReverseStress, useRunReverseStress } from '@/lib/api/hooks';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { fmtDateUTC } from '@/lib/api/values';

// Reverse stress (Phase 2 item 4): bisection over the existing scenario
// engines for the severity multipliers that breach the LCR floor and the
// four-quarter CET1 minimum. The frontier is kept as a saved run
// anchored to both engines' baseline snapshots.

type Axis = Record<string, unknown> | undefined;

function axisValue(axis: Axis, key: string): string | null {
  const value = axis?.[key];
  return value === null || value === undefined ? null : String(value);
}

function axisBreached(axis: Axis): boolean {
  return axis?.['breached'] === true;
}

export default function ReverseStress() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const latest = useLatestReverseStress(bankId, periodId);
  const run = useRunReverseStress(bankId);

  const frontier = latest.data ?? null;
  const liquidity = frontier?.liquidityAxis as Axis;
  const capital = frontier?.capitalAxis as Axis;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'Reverse Stress' },
        ]}
        title="Reverse Stress Testing"
        subtitle="The severity multipliers at which the hard floors break — searched over the stored scenario engines, never a new model"
        action={
          <button
            type="button"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            disabled={!bankId || !periodId || run.isPending}
            onClick={() => periodId && run.mutate(periodId)}
          >
            {run.isPending ? 'Searching frontier…' : 'Run reverse stress'}
          </button>
        }
      />

      <QueryBoundary
        isLoading={latest.isLoading}
        error={latest.error ?? run.error}
        onRetry={() => latest.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {frontier ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <KpiStat
                  label="Liquidity frontier (combined scenario)"
                  value={
                    axisBreached(liquidity)
                      ? `${axisValue(liquidity, 'breach_multiplier')}× severity`
                      : `No breach to ${axisValue(liquidity, 'k_max')}×`
                  }
                  status={axisBreached(liquidity) ? 'crit' : 'ok'}
                  hint={
                    axisBreached(liquidity)
                      ? `LCR ${axisValue(liquidity, 'lcr_at_breach_pct')}% at breach vs floor ${axisValue(liquidity, 'lcr_min_pct')}%`
                      : `LCR floor ${axisValue(liquidity, 'lcr_min_pct')}% holds across the search range`
                  }
                />
                <KpiStat
                  label="Capital frontier (severe scenario)"
                  value={
                    axisBreached(capital)
                      ? `${axisValue(capital, 'breach_multiplier')}× severity`
                      : `No breach to ${axisValue(capital, 'k_max')}×`
                  }
                  status={axisBreached(capital) ? 'crit' : 'ok'}
                  hint={
                    axisBreached(capital)
                      ? `Worst CET1 ${axisValue(capital, 'worst_cet1_at_breach_pct')}% vs minimum ${axisValue(capital, 'cet1_min_pct')}%`
                      : `CET1 minimum ${axisValue(capital, 'cet1_min_pct')}% holds across the search range`
                  }
                />
              </div>

              <SectionCard
                title="Frontier narrative"
                subtitle="The most recent saved frontier for this period"
                footer={
                  <span>
                    The frontier anchors to both engines&apos; baseline snapshots,
                    so a Board pack can cite exactly which book and parameter set
                    produced it. The Stress Test Output Report pack (return code
                    STRESS-PACK) carries this frontier alongside the traffic lights —
                    generate it from the{' '}
                    <Link href="/submissions" className="text-action hover:underline">
                      Regulatory Reporting
                    </Link>
                    .
                  </span>
                }
              >
                <p className="text-body text-navy/85 leading-relaxed">{frontier.narrative}</p>
              </SectionCard>
            </>
          ) : (
            <EmptyState
              Icon={Target}
              title="No reverse-stress frontier yet"
              description="The search scales the combined liquidity scenario (run-offs, inflow haircuts, HQLA haircuts) and the severe capital scenario (credit losses, RWA growth, FX RWA) by bisection until the LCR floor or the four-quarter CET1 minimum breaks, then reports the multiplier and the ratio at breach. Both engines need a succeeded baseline for the current reporting period."
              action={
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
                  disabled={!bankId || !periodId || run.isPending}
                  onClick={() => periodId && run.mutate(periodId)}
                >
                  {run.isPending ? 'Searching frontier…' : 'Run reverse stress'}
                </button>
              }
            />
          )}
        </div>
      </QueryBoundary>
    </>
  );
}
