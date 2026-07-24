'use client';

import { useMemo } from 'react';
import type { FxStandaloneVarRead } from '@aequoros/risk-service-api';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FxModuleFrame, { type FxFrameContext } from '@/components/fx/FxModuleFrame';
import VarWaterfall from '@/components/fx/charts/VarWaterfall';
import TrendChart from '@/components/fx/charts/TrendChart';
import ScenarioStrip from '@/components/fx/ScenarioStrip';
import { fxRunParameters } from '@/components/fx/params';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

export default function FxVarPage() {
  return (
    <FxModuleFrame
      crumb="VaR & Stress"
      title="FX Value at Risk & Stress"
      subtitle="Historical-simulation VaR · diversification decomposition · cedi-crisis stressed VaR"
    >
      {(ctx) => <VarBody ctx={ctx} />}
    </FxModuleFrame>
  );
}

function VarBody({ ctx }: { ctx: FxFrameContext }) {
  const { data, metrics: m, run } = ctx;
  const params = useMemo(() => fxRunParameters(run), [run]);

  const portfolioVar = num(m.var991dGhs);
  const standaloneTotal = num(m.standaloneVarTotalGhs);
  const benefit = num(m.diversificationBenefitGhs);
  const stressed = num(m.stressedVarGhs);
  const confidence = num(m.varConfidencePct);

  const benefitPct = standaloneTotal > 0 ? (benefit / standaloneTotal) * 100 : 0;
  const upliftRatio = portfolioVar > 0 ? stressed / portfolioVar : 0;

  const standalone = data.standaloneVars.map((s) => ({
    currency: s.currency,
    varGhs: num(s.standaloneVarGhs),
  }));

  const varTrend = data.trend.map((p) => ({
    label: p.label,
    value: num(p.var991dGhs),
    stored: p.stored,
  }));

  const stressedNote = params?.crisis
    ? `Historical VaR re-run on the 2022–23 cedi-crisis slice of the return history ` +
      `(observations ${params.crisis.windowStart}–${params.crisis.windowEnd}), scaled by a ` +
      `+${(params.crisis.correlationUplift * 100).toFixed(0)}% correlation uplift.`
    : 'Historical VaR re-run on the 2022–23 cedi-crisis slice of the return history with a supervisory correlation uplift — persist a run to surface the exact window parameters.';

  const columns: Column<FxStandaloneVarRead>[] = [
    { key: 'ccy', header: 'Currency', render: (r) => r.currency, width: '22%' },
    {
      key: 'net',
      header: 'Net position (GHS)',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.netGhs)),
    },
    {
      key: 'var',
      header: `Standalone VaR (${confidence.toFixed(0)}%, 1d)`,
      numeric: true,
      render: (r) => fmtCurrency(num(r.standaloneVarGhs)),
    },
    {
      key: 'share',
      header: 'Share of undiversified',
      numeric: true,
      render: (r) =>
        standaloneTotal > 0
          ? fmtPct((num(r.standaloneVarGhs) / standaloneTotal) * 100, 1)
          : '—',
    },
  ];

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label={`Portfolio VaR (${confidence.toFixed(0)}%, 1-day)`}
          value={fmtCurrency(portfolioVar)}
          hint={`${m.varObservations} return observations`}
        />
        <KpiStat
          label="Sum of standalone VaR"
          value={fmtCurrency(standaloneTotal)}
          hint="Undiversified currency VaR"
        />
        <KpiStat
          label="Diversification benefit"
          value={fmtCurrency(benefit)}
          status="ok"
          hint={`${fmtPct(benefitPct, 1)} of standalone sum`}
        />
        <KpiStat
          label="Stressed VaR"
          value={fmtCurrency(stressed)}
          status="warn"
          hint={
            upliftRatio > 0 ? `${upliftRatio.toFixed(2)}× base VaR` : 'Cedi-crisis calibration'
          }
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
        <ChartFrame
          title="VaR decomposition"
          subtitle="Standalone per-currency VaR bridges down to the diversified portfolio VaR"
          height={300}
          className="xl:col-span-3"
          footer={
            <span>
              Undiversified sum {fmtCurrency(standaloneTotal)} − diversification{' '}
              {fmtCurrency(benefit)} = portfolio VaR {fmtCurrency(portfolioVar)}
            </span>
          }
        >
          <VarWaterfall
            input={{
              standalone,
              diversificationBenefitGhs: benefit,
              portfolioVarGhs: portfolioVar,
            }}
          />
        </ChartFrame>

        <div className="xl:col-span-2 flex flex-col gap-6">
          <div className="card p-5 border-l-4 border-l-warning flex flex-col gap-2">
            <p className="text-caption font-medium text-slate uppercase tracking-wider">
              Cedi-crisis stressed VaR
            </p>
            <p className="font-mono text-kpi text-navy tnum">
              {fmtCurrency(stressed)}
            </p>
            <p className="text-caption text-slate leading-relaxed">{stressedNote}</p>
            <p className="text-caption text-slate">
              Base VaR {fmtCurrency(portfolioVar)}
              {upliftRatio > 0 && (
                <>
                  {' '}
                  · stress multiple{' '}
                  <span className="font-mono font-medium text-navy tnum">
                    {upliftRatio.toFixed(2)}×
                  </span>
                </>
              )}
            </p>
          </div>

          <SectionCard
            title="Standalone VaR by currency"
            subtitle="Single-currency historical VaR before diversification"
            noPadding
          >
            <DataTable columns={columns} rows={data.standaloneVars} density="compact" />
          </SectionCard>
        </div>
      </div>

      <SectionCard
        title="Depreciation scenario NOP"
        subtitle="Aggregate NOP under the persisted cedi depreciation shocks"
      >
        <ScenarioStrip
          scenarios={data.scenarios}
          aggregateLimitPct={num(m.nopAggregateLimitPct)}
        />
      </SectionCard>

      {varTrend.length >= 2 && (
        <ChartFrame
          title={`Portfolio VaR trend (${confidence.toFixed(0)}%, 1-day)`}
          subtitle="Trailing periods · hollow points are inline computations without a persisted run"
          height={260}
        >
          <TrendChart
            data={varTrend}
            valueLabel="Portfolio VaR"
            format={(v) => fmtCurrency(v)}
            colorIndex={1}
          />
        </ChartFrame>
      )}
    </>
  );
}
