'use client';

import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import StatusPill from '@/components/ui/StatusPill';
import ValidationList from '@/components/ui/ValidationList';
import FxModuleFrame, { type FxFrameContext } from '@/components/fx/FxModuleFrame';
import TrendChart from '@/components/fx/charts/TrendChart';
import { num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

export default function FxLimitsPage() {
  return (
    <FxModuleFrame
      crumb="Limits"
      title="FX Limits"
      subtitle="BoG net-open-position ceilings · aggregate and single-currency utilisation"
    >
      {(ctx) => <LimitsBody ctx={ctx} />}
    </FxModuleFrame>
  );
}

function LimitsBody({ ctx }: { ctx: FxFrameContext }) {
  const { data, metrics: m } = ctx;

  const aggregateLimit = num(m.nopAggregateLimitPct);
  const singleLimit = num(m.nopSingleLimitPct);
  const nopPct = num(m.nopPctTier1);

  const breaches = [
    ...(nopPct >= aggregateLimit ? ['Aggregate NOP'] : []),
    ...data.positions.filter((p) => !p.withinSingleLimit).map((p) => p.currency),
  ];

  const trend = data.trend.map((p) => ({
    label: p.label,
    value: num(p.nopPctTier1),
    stored: p.stored,
  }));
  const trendMax = Math.max(...trend.map((p) => p.value), aggregateLimit);

  const utilisation = aggregateLimit > 0 ? (nopPct / aggregateLimit) * 100 : 0;

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KpiStat
          label="Aggregate limit utilisation"
          value={fmtPct(utilisation, 1)}
          status={nopPct >= aggregateLimit ? 'crit' : utilisation >= 80 ? 'warn' : 'ok'}
          hint={`${fmtPct(nopPct, 2)} of Tier 1 vs ${fmtPct(aggregateLimit, 0)} ceiling`}
        />
        <KpiStat
          label="Single-currency ceiling"
          value={fmtPct(singleLimit, 0)}
          hint={`Largest position: ${m.singleCcyMaxCurrency} at ${fmtPct(
            num(m.singleCcyMaxPct),
            2
          )}`}
          status={
            m.singleCcyStatus === 'red' ? 'crit' : m.singleCcyStatus === 'amber' ? 'warn' : 'ok'
          }
        />
        <div className="card px-4 py-3.5 flex flex-col gap-2 min-w-0">
          <p className="text-micro font-medium text-slate uppercase tracking-wider">
            Breach status
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            {breaches.length === 0 ? (
              <StatusPill tone="compliant">No limit breaches</StatusPill>
            ) : (
              <StatusPill tone="breach">
                {breaches.length} breach{breaches.length > 1 ? 'es' : ''}:{' '}
                {breaches.join(', ')}
              </StatusPill>
            )}
          </div>
          <Link
            href="/alerts"
            className="mt-auto inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
          >
            View alerts
            <ArrowUpRight size={12} aria-hidden />
          </Link>
        </div>
      </div>

      <SectionCard
        title="Limit wall"
        subtitle="Every NOP ceiling for this period — measured value vs amber zone and hard limit"
      >
        <div className="space-y-6">
          <LimitBar
            label={`Aggregate NOP / Tier 1 (all currencies)`}
            value={nopPct}
            limit={aggregateLimit}
            direction="below"
            unit="%"
            format={(v) => v.toFixed(2)}
            limitLabel="BoG aggregate limit"
          />
          <div className="border-t border-border-light pt-5 space-y-5">
            {data.positions.map((p) => (
              <LimitBar
                key={p.currency}
                label={`${p.currency} single-currency NOP / Tier 1 (${
                  p.side === 'long' ? 'long' : 'short'
                })`}
                value={num(p.absPctTier1)}
                limit={singleLimit}
                direction="below"
                unit="%"
                format={(v) => v.toFixed(2)}
                limitLabel="Single-ccy limit"
              />
            ))}
          </div>
        </div>
      </SectionCard>

      {trend.length >= 2 && (
        <ChartFrame
          title="Aggregate NOP / Tier 1 trend"
          subtitle="Trailing periods against the aggregate ceiling · hollow points are inline computations"
          height={260}
        >
          <TrendChart
            data={trend}
            threshold={aggregateLimit}
            thresholdLabel={`Limit ${fmtPct(aggregateLimit, 0)}`}
            valueLabel="NOP / Tier 1"
            format={(v) => fmtPct(v, 2)}
            yDomain={[0, Math.ceil(trendMax + 3)]}
          />
        </ChartFrame>
      )}

      <SectionCard
        title="Validations"
        subtitle="NOP, VaR, and hedge-accounting rule evaluation for this period"
        noPadding
      >
        <ValidationList validations={data.validations} />
      </SectionCard>
    </>
  );
}
