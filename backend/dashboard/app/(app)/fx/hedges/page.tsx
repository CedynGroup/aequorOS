'use client';

import { useMemo } from 'react';
import { ShieldCheck } from 'lucide-react';
import type { FxHedgeRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FxModuleFrame, { type FxFrameContext } from '@/components/fx/FxModuleFrame';
import HedgeScatter from '@/components/fx/charts/HedgeScatter';
import { fxRunParameters } from '@/components/fx/params';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

/**
 * IFRS 9 dual-test defaults, as stated by the backend's `hedges_effective`
 * validation rule ("R-squared >= 80% and dollar-offset within 80-125%").
 * The stored run's parameter snapshot overrides these when available.
 */
const DEFAULT_R2_MIN = 80;
const DEFAULT_OFFSET_LOW = 80;
const DEFAULT_OFFSET_HIGH = 125;

export default function FxHedgesPage() {
  return (
    <FxModuleFrame
      crumb="Hedge Book"
      title="FX Hedge Book"
      subtitle="Hedge inventory · IFRS 9 prospective effectiveness · mark-to-market"
    >
      {(ctx) => <HedgesBody ctx={ctx} />}
    </FxModuleFrame>
  );
}

function HedgesBody({ ctx }: { ctx: FxFrameContext }) {
  const { data, metrics: m, run } = ctx;
  const params = useMemo(() => fxRunParameters(run), [run]);

  const bands = params?.hedgeBands ?? {
    r2MinPct: DEFAULT_R2_MIN,
    offsetLowPct: DEFAULT_OFFSET_LOW,
    offsetHighPct: DEFAULT_OFFSET_HIGH,
  };
  const bandsSource = params?.hedgeBands
    ? 'from the stored run parameter snapshot'
    : 'IFRS 9 defaults cited by the hedge validation rule';

  const hedges = data.hedges;
  const aggregateMtm = num(m.hedgeAggregateMtmGhs);
  const passRate =
    m.hedgeTotalCount > 0 ? (m.hedgeEffectiveCount / m.hedgeTotalCount) * 100 : 0;

  const points = hedges.map((h) => ({
    hedgeId: h.hedgeId,
    pair: h.pair,
    instrument: h.instrument,
    r2Pct: num(h.prospectiveR2Pct),
    offsetPct: num(h.dollarOffsetPct),
    mtmGhs: num(h.mtmGhs),
    effective: h.effective,
  }));

  const columns: Column<FxHedgeRead>[] = [
    { key: 'id', header: 'Hedge', render: (r) => r.hedgeId, width: '14%' },
    { key: 'pair', header: 'Pair', render: (r) => r.pair },
    { key: 'instrument', header: 'Instrument', render: (r) => r.instrument },
    {
      key: 'r2',
      header: `Prospective R² (≥ ${fmtPct(bands.r2MinPct, 0)})`,
      numeric: true,
      render: (r) => {
        const v = num(r.prospectiveR2Pct);
        return (
          <span className={v < bands.r2MinPct ? 'text-critical font-medium' : undefined}>
            {fmtPct(v, 1)}
          </span>
        );
      },
    },
    {
      key: 'offset',
      header: `Dollar offset (${fmtPct(bands.offsetLowPct, 0)}–${fmtPct(
        bands.offsetHighPct,
        0
      )})`,
      numeric: true,
      render: (r) => {
        const v = num(r.dollarOffsetPct);
        const outside = v < bands.offsetLowPct || v > bands.offsetHighPct;
        return (
          <span className={outside ? 'text-critical font-medium' : undefined}>
            {fmtPct(v, 1)}
          </span>
        );
      },
    },
    {
      key: 'mtm',
      header: 'MTM (GHS)',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.mtmGhs)),
    },
    {
      key: 'status',
      header: 'IFRS 9',
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.effective ? 'compliant' : 'breach'}>
          {r.effective ? 'Effective' : 'Ineffective'}
        </StatusPill>
      ),
    },
  ];

  if (hedges.length === 0) {
    return (
      <EmptyState
        Icon={ShieldCheck}
        title="No FX hedges on the book"
        description="No fx_hedge facts exist for this period. Once hedge positions are ingested, the IFRS 9 dual effectiveness test, MTM, and pass-region scatter appear here."
      />
    );
  }

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KpiStat
          label="Aggregate hedge MTM"
          value={fmtCurrencySigned(aggregateMtm)}
          hint="Sum of hedge marks, GHS equivalent"
        />
        <KpiStat
          label="Effective hedges"
          value={`${m.hedgeEffectiveCount} of ${m.hedgeTotalCount}`}
          status={m.hedgeEffectiveCount === m.hedgeTotalCount ? 'ok' : 'warn'}
          hint={`${fmtPct(passRate, 0)} pass the dual test`}
        />
        <KpiStat
          label="Dual-test bands"
          value={`R² ≥ ${bands.r2MinPct.toFixed(0)}%`}
          hint={`Offset ${bands.offsetLowPct.toFixed(0)}–${bands.offsetHighPct.toFixed(
            0
          )}% · ${bandsSource}`}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <ChartFrame
          title="Effectiveness scatter"
          subtitle="Prospective R² vs dollar-offset · shaded region passes both IFRS 9 tests"
          height={300}
          footer={
            <span>
              Pass region: offset {bands.offsetLowPct.toFixed(0)}–
              {bands.offsetHighPct.toFixed(0)}% and R² ≥ {bands.r2MinPct.toFixed(0)}% (
              {bandsSource})
            </span>
          }
        >
          <HedgeScatter
            data={points}
            r2MinPct={bands.r2MinPct}
            offsetLowPct={bands.offsetLowPct}
            offsetHighPct={bands.offsetHighPct}
          />
        </ChartFrame>

        <SectionCard
          title="Hedge inventory"
          subtitle="Per-hedge instrument, marks, and prospective test results"
          noPadding
          actions={
            <StatusPill tone={m.hedgeEffectiveCount === m.hedgeTotalCount ? 'compliant' : 'amber'}>
              {m.hedgeEffectiveCount} of {m.hedgeTotalCount} effective
            </StatusPill>
          }
        >
          <DataTable columns={columns} rows={hedges} density="compact" />
        </SectionCard>
      </div>
    </>
  );
}
