'use client';

import { useMemo, useState } from 'react';
import type { FxCurrencyPositionRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import Sparkline from '@/components/ui/Sparkline';
import StatusPill from '@/components/ui/StatusPill';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FxModuleFrame, { type FxFrameContext } from '@/components/fx/FxModuleFrame';
import ExposureBars from '@/components/fx/charts/ExposureBars';
import ScenarioStrip from '@/components/fx/ScenarioStrip';
import { fxPositionSplits, type FxPositionSplit } from '@/components/fx/params';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtNum, fmtPct, regShort, currencyCode } from '@/lib/format';

export default function FxExposurePage() {
  return (
    <FxModuleFrame
      crumb="Exposure"
      title="FX Exposure"
      subtitle={`Net open position by currency · ${regShort()} NOP framework · ${currencyCode()} equivalents at period-end spot`}
    >
      {(ctx) => <ExposureBody ctx={ctx} />}
    </FxModuleFrame>
  );
}

function ExposureBody({ ctx }: { ctx: FxFrameContext }) {
  const { data, metrics: m, run } = ctx;
  const [selectedCurrency, setSelectedCurrency] = useState<string | null>(null);

  const aggregateLimit = num(m.nopAggregateLimitPct);
  const singleLimit = num(m.nopSingleLimitPct);

  const splits = useMemo(() => fxPositionSplits(run), [run]);

  const trend = data.trend;
  const nopSpark = trend.map((p) => num(p.nopPctTier1));
  const prior = trend.length >= 2 ? trend[trend.length - 2] : undefined;
  const nopDelta = prior ? num(m.nopPctTier1) - num(prior.nopPctTier1) : undefined;

  const statusFor = (s: 'green' | 'amber' | 'red' | string) =>
    s === 'red' ? ('crit' as const) : s === 'amber' ? ('warn' as const) : ('ok' as const);

  const bars = data.positions.map((p) => ({
    currency: p.currency,
    netGhs: num(p.netGhs),
    absPctTier1: num(p.absPctTier1),
    withinSingleLimit: p.withinSingleLimit,
  }));

  const selectedSplit: FxPositionSplit | undefined = selectedCurrency
    ? splits.get(selectedCurrency)
    : undefined;
  const selectedPosition = selectedCurrency
    ? data.positions.find((p) => p.currency === selectedCurrency)
    : undefined;

  const columns: Column<FxCurrencyPositionRead>[] = [
    { key: 'ccy', header: 'Currency', render: (r) => r.currency, width: '12%' },
    {
      key: 'side',
      header: 'Side',
      render: (r) => (
        <StatusPill tone={r.side === 'long' ? 'action' : 'amber'}>
          {r.side === 'long' ? 'Long' : 'Short'}
        </StatusPill>
      ),
    },
    {
      key: 'netCcy',
      header: 'Net (CCY)',
      numeric: true,
      render: (r) => fmtNum(num(r.netCcy)),
    },
    {
      key: 'spot',
      header: 'Spot (GHS)',
      numeric: true,
      render: (r) => num(r.spotGhs).toFixed(4),
    },
    {
      key: 'netGhs',
      header: 'Net (GHS)',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.netGhs)),
    },
    {
      key: 'pct',
      header: '% Tier 1',
      numeric: true,
      render: (r) => (
        <span className={!r.withinSingleLimit ? 'text-critical font-medium' : undefined}>
          {fmtPct(num(r.absPctTier1), 2)}
        </span>
      ),
    },
    {
      key: 'status',
      header: `Limit ${fmtPct(singleLimit, 0)}`,
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.withinSingleLimit ? 'compliant' : 'breach'}>
          {r.withinSingleLimit ? 'Within' : 'Breach'}
        </StatusPill>
      ),
    },
  ];

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Aggregate NOP / Tier 1"
          value={fmtPct(num(m.nopPctTier1), 2)}
          delta={nopDelta}
          invertDelta
          status={statusFor(m.nopStatus)}
          sparkline={nopSpark.length >= 2 ? <Sparkline data={nopSpark} /> : undefined}
          hint={`Limit ${fmtPct(aggregateLimit, 0)}`}
        />
        <KpiStat
          label={`Largest single currency (${m.singleCcyMaxCurrency})`}
          value={fmtPct(num(m.singleCcyMaxPct), 2)}
          status={statusFor(m.singleCcyStatus)}
          hint={`Single-currency limit ${fmtPct(singleLimit, 0)}`}
        />
        <KpiStat
          label="Net open position"
          value={fmtCurrency(num(m.nopGhs))}
          hint={`Long ${fmtCurrency(num(m.sumLongGhs))} · Short ${fmtCurrency(
            num(m.sumShortGhs),
            'GHS'
          )}`}
        />
        <KpiStat
          label="Tier 1 capital"
          value={fmtCurrency(num(m.tier1Ghs))}
          hint="Limit denominator"
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <ChartFrame
          title="Net position by currency"
          subtitle="Long positive, short negative · colored by single-currency limit state"
          height={300}
          footer={
            <span>
              Green within limit · amber at ≥ 80% of the {fmtPct(singleLimit, 0)} limit ·
              red in breach
            </span>
          }
        >
          <ExposureBars data={bars} singleLimitPct={singleLimit} />
        </ChartFrame>

        <SectionCard
          title="Position detail"
          subtitle="Net exposure, period-end spot, and limit state per currency"
          noPadding
          footer={
            splits.size === 0 ? (
              <span>
                Run all scenarios to persist a run — the stored fact snapshot adds the
                asset / liability / derivative split per currency.
              </span>
            ) : (
              <span>Select a row for the asset / liability / derivative split.</span>
            )
          }
        >
          <DataTable
            columns={columns}
            rows={data.positions}
            density="compact"
            onRowClick={
              splits.size > 0
                ? (r) =>
                    setSelectedCurrency((current) =>
                      current === r.currency ? null : r.currency
                    )
                : undefined
            }
            rowClassName={(r) =>
              r.currency === selectedCurrency ? 'bg-action-light/40' : ''
            }
          />
        </SectionCard>
      </div>

      {selectedSplit && selectedPosition && (
        <SectionCard
          title={`${selectedSplit.currency} position split`}
          subtitle="From the stored run's fact snapshot — figures in original currency"
          actions={
            <button
              type="button"
              onClick={() => setSelectedCurrency(null)}
              className="text-caption font-medium text-slate hover:text-navy"
            >
              Close
            </button>
          }
        >
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <SplitStat label="Assets (CCY)" value={fmtNum(selectedSplit.assetsCcy)} />
            <SplitStat
              label="Liabilities (CCY)"
              value={fmtNum(selectedSplit.liabilitiesCcy)}
            />
            <SplitStat
              label="Net derivatives (CCY)"
              value={fmtNum(selectedSplit.netDerivativesCcy)}
            />
            <SplitStat
              label="Net (CCY)"
              value={fmtNum(selectedSplit.netCcy)}
              hint={`= assets − liabilities + derivatives · ${fmtCurrencySigned(
                num(selectedPosition.netGhs),
                'GHS'
              )} at spot ${num(selectedPosition.spotGhs).toFixed(4)}`}
            />
          </div>
        </SectionCard>
      )}

      <SectionCard
        title="Depreciation scenarios"
        subtitle="Aggregate NOP under the cedi depreciation shocks — full detail on VaR & Stress"
      >
        <ScenarioStrip scenarios={data.scenarios} aggregateLimitPct={aggregateLimit} />
      </SectionCard>
    </>
  );
}

function SplitStat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className="mt-1 font-mono text-h2 text-navy tnum">{value}</p>
      {hint && <p className="mt-0.5 text-caption text-slate">{hint}</p>}
    </div>
  );
}
