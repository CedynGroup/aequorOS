'use client';

/**
 * Treasury Command Center — the home page and hero of the bank demo.
 *
 * Composition (top → bottom, permuted by the role lens):
 *   1. Breach banner (open critical/high alerts, or a slim compliance strip)
 *   2. Six-module pulse wall (headline live metric per regulatory engine)
 *   3. Big-4 balance-sheet strip (canonical facts)
 *   4. Ratio trend (LCR/NSFR/CAR across all periods) + operational feed
 *   5. Per-module run-freshness strip
 *
 * Every panel reads the *effective* reporting period resolved by
 * `useEffectivePeriod`: the header-selected period when it has facts,
 * otherwise the newest computed period — labelled explicitly — so the page
 * can never show populated KPIs beside an "empty" live state again.
 */

import Link from 'next/link';
import { Database, Info } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonCard, SkeletonChart, SkeletonLine } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC, labelize } from '@/lib/api/values';
import { useEffectivePeriod } from '@/components/home/hooks';
import RoleLensTabs, { ROLE_CONFIG, useRoleLens } from '@/components/home/RoleLens';
import BreachBanner from '@/components/home/BreachBanner';
import PulseWall from '@/components/home/PulseWall';
import BalanceSheetStrip from '@/components/home/BalanceSheetStrip';
import RatioTrendChart from '@/components/home/RatioTrendChart';
import OperationalFeed from '@/components/home/OperationalFeed';
import FreshnessStrip from '@/components/home/FreshnessStrip';
import { centralBankName } from '@/lib/format';

export default function CommandCenterPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const [role, setRole] = useRoleLens();
  const lens = ROLE_CONFIG[role];
  const effective = useEffectivePeriod();

  return (
    <>
      <PageHeader
        title="Treasury Command Center"
        subtitle={
          bank
            ? `${bank.name} · ${centralBankName()} licensee · ${labelize(bank.licenseType)}`
            : 'Loading bank profile…'
        }
        asOf={
          effective.period ? fmtDateUTC(effective.period.periodEnd) : undefined
        }
        action={<RoleLensTabs role={role} onChange={setRole} />}
      />

      {effective.isResolving ? (
        <CommandCenterSkeleton />
      ) : effective.isEmpty ? (
        <div className="px-8 py-10 max-w-2xl mx-auto">
          {effective.error ? (
            <div className="mb-6">
              <ErrorPanel
                error={effective.error}
                title="Could not resolve a computed reporting period"
              />
            </div>
          ) : null}
          <EmptyState
            Icon={Database}
            title="No computed data yet"
            description="This bank has reporting periods but none holds activated data. Upload your source files in the Data Engine and activate them — the Command Center lights up for the activated as-of period."
            action={
              <Link
                href="/data-engine"
                className="inline-flex items-center gap-2 px-4 py-2 text-caption font-medium btn-primary"
              >
                Open the Data Engine
              </Link>
            }
          />
        </div>
      ) : effective.period ? (
        <div className="px-8 py-6 space-y-6">
          {effective.isFallback && effective.selectedPeriod && (
            <div className="card border-l-4 border-l-action bg-action-light/30 px-5 py-3 flex items-start gap-3">
              <Info
                size={15}
                className="text-action shrink-0 mt-0.5"
                aria-hidden
              />
              <p className="text-body text-navy/85 leading-relaxed">
                Showing{' '}
                <span className="font-medium text-navy">
                  {fmtDateUTC(effective.period.periodEnd)}
                </span>{' '}
                — latest computed period. The selected period (
                {effective.selectedPeriod.label}) has no activated data yet.
              </p>
            </div>
          )}

          <BreachBanner bankId={bankId} period={effective.period} />

          {lens.panels.map((panel) => {
            switch (panel) {
              case 'pulse':
                return (
                  <PulseWall
                    key="pulse"
                    bankId={bankId}
                    period={effective.period!}
                    moduleOrder={lens.moduleOrder}
                  />
                );
              case 'balance':
                return (
                  <BalanceSheetStrip
                    key="balance"
                    bankId={bankId}
                    period={effective.period!}
                  />
                );
              case 'band':
                return (
                  <div
                    key="band"
                    className="grid grid-cols-1 xl:grid-cols-5 gap-6"
                  >
                    <div className="xl:col-span-3 min-w-0">
                      <RatioTrendChart
                        bankId={bankId}
                        period={effective.period!}
                      />
                    </div>
                    <div className="xl:col-span-2 min-w-0">
                      <OperationalFeed bankId={bankId} />
                    </div>
                  </div>
                );
              case 'freshness':
                return (
                  <FreshnessStrip
                    key="freshness"
                    bankId={bankId}
                    period={effective.period!}
                  />
                );
              default:
                return null;
            }
          })}
        </div>
      ) : null}
    </>
  );
}

/** Loading layout that mirrors the Command Center grid. */
function CommandCenterSkeleton() {
  return (
    <div className="px-8 py-6 space-y-6" aria-busy="true" aria-label="Loading">
      <div className="card px-5 py-3">
        <SkeletonLine width="40%" height={12} />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
        <div className="xl:col-span-3">
          <SkeletonChart height={280} />
        </div>
        <div className="xl:col-span-2">
          <SkeletonChart height={280} />
        </div>
      </div>
    </div>
  );
}
