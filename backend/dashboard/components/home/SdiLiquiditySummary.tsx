'use client';

import Link from 'next/link';
import SectionCard from '@/components/ui/SectionCard';
import KpiStat from '@/components/ui/KpiStat';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useSdiLiquidityPosition } from '@/components/basel/sdiHooks';

export default function SdiLiquiditySummary({ bankId }: { bankId: string | undefined }) {
  const liquidity = useSdiLiquidityPosition(bankId);
  const data = liquidity.data;
  const table1Breaches = data?.ratios.filter((ratio) => ratio.status === 'below_minimum').length ?? 0;
  const reserveBreaches = data?.reserves.filter((reserve) => reserve.status === 'below_minimum').length ?? 0;
  const ready = data?.readiness.filter((row) => row.status === 'ready').length ?? 0;
  return (
    <QueryBoundary isLoading={liquidity.isLoading} error={liquidity.error} onRetry={() => liquidity.refetch()}>
      {data ? (
        <SectionCard
          title="SDI Liquidity"
          subtitle={`LMTD baseline controls as of ${data.as_of}`}
          actions={<Link href="/liquidity" className="text-caption font-medium text-action hover:underline">Open liquidity →</Link>}
        >
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <KpiStat label="Table 1 breaches" value={String(table1Breaches)} status={table1Breaches > 0 ? 'crit' : 'ok'} hint="Binding prudential ratios" />
            <KpiStat label="Reserve breaches" value={String(reserveBreaches)} status={reserveBreaches > 0 ? 'crit' : 'ok'} hint="Primary and secondary reserves" />
            <KpiStat label="Data-ready controls" value={`${ready} / ${data.readiness.length}`} status={ready === data.readiness.length ? 'ok' : 'warn'} hint="Canonical-book readiness" />
          </div>
        </SectionCard>
      ) : null}
    </QueryBoundary>
  );
}