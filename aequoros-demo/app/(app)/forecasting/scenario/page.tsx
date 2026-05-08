'use client';

import { useState, useMemo } from 'react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import KPICard from '@/components/ui/KPICard';
import BalanceSheetProjectionChart from '@/components/charts/BalanceSheetProjectionChart';
import { projection, strategicAssumptions } from '@/lib/data/forecasting';
import { bank } from '@/lib/data/bank';
import { fmtCurrency, fmtPctSigned } from '@/lib/format';

export default function ScenarioBuilder() {
  const [horizon, setHorizon] = useState<12 | 24 | 36>(36);
  const [assetGrowthMultiplier, setAssetGrowthMultiplier] = useState<number>(1.0);
  const [nimMultiplier, setNimMultiplier] = useState<number>(1.0);

  const adjusted = useMemo(() => {
    return projection.map((p, i) => ({
      ...p,
      assets: Number((p.assets * (1 + (assetGrowthMultiplier - 1) * (i / 36))).toFixed(0)),
      loans: Number((p.loans * (1 + (assetGrowthMultiplier - 1) * (i / 36))).toFixed(0)),
      govSecs: Number((p.govSecs * (1 + (assetGrowthMultiplier - 1) * (i / 36))).toFixed(0)),
      cashAndBoG: Number((p.cashAndBoG * (1 + (assetGrowthMultiplier - 1) * (i / 36))).toFixed(0)),
      nim: Number((p.nim * nimMultiplier).toFixed(2)),
    }));
  }, [assetGrowthMultiplier, nimMultiplier]);

  const endState = adjusted[horizon];
  const baseEndState = projection[horizon];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'Scenario Builder' },
        ]}
        title="Scenario Builder"
        subtitle="Adjust strategic assumptions and project forward"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="card px-5 py-4 grid grid-cols-1 md:grid-cols-3 gap-6 items-start">
          <div>
            <label className="block text-micro font-medium uppercase tracking-wider text-slate mb-2">
              Horizon
            </label>
            <div className="inline-flex gap-1 bg-surface p-1 rounded">
              {[12, 24, 36].map((h) => (
                <button
                  key={h}
                  type="button"
                  onClick={() => setHorizon(h as 12 | 24 | 36)}
                  className={`px-3 py-1.5 rounded text-caption font-medium ${
                    horizon === h ? 'bg-navy text-white' : 'text-slate hover:text-navy'
                  }`}
                >
                  {h}M
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-micro font-medium uppercase tracking-wider text-slate mb-2">
              Asset growth × multiplier{' '}
              <span className="font-mono text-navy">
                {(assetGrowthMultiplier * 100).toFixed(0)}%
              </span>
            </label>
            <input
              type="range"
              min={0.5}
              max={1.5}
              step={0.05}
              value={assetGrowthMultiplier}
              onChange={(e) => setAssetGrowthMultiplier(parseFloat(e.target.value))}
              className="w-full accent-action"
            />
            <div className="flex justify-between text-caption text-slate mt-1 font-mono">
              <span>50%</span>
              <span>100%</span>
              <span>150%</span>
            </div>
          </div>

          <div>
            <label className="block text-micro font-medium uppercase tracking-wider text-slate mb-2">
              NIM multiplier{' '}
              <span className="font-mono text-navy">
                {(nimMultiplier * 100).toFixed(0)}%
              </span>
            </label>
            <input
              type="range"
              min={0.7}
              max={1.3}
              step={0.05}
              value={nimMultiplier}
              onChange={(e) => setNimMultiplier(parseFloat(e.target.value))}
              className="w-full accent-action"
            />
            <div className="flex justify-between text-caption text-slate mt-1 font-mono">
              <span>70%</span>
              <span>100%</span>
              <span>130%</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <KPICard
            label={`Assets at +${horizon}M (scenario)`}
            value={endState.assets}
            prefix="GHS"
            suffix="M"
            decimals={0}
            footer={`Baseline ${fmtCurrency(baseEndState.assets * 1_000_000, 'GHS')}`}
            status={endState.assets >= baseEndState.assets * 0.9 ? 'compliant' : 'amber'}
          />
          <KPICard
            label="Implied LDR"
            value={endState.ldr}
            suffix="%"
            decimals={1}
            footer="Target ≤75%"
            status={endState.ldr <= 75 ? 'compliant' : 'approaching'}
          />
          <KPICard
            label="Adjusted NIM"
            value={endState.nim}
            suffix="%"
            decimals={2}
            footer={`Plan target 5.00%`}
            status={endState.nim >= 4.5 ? 'compliant' : 'amber'}
          />
          <KPICard
            label="Variance vs baseline"
            value={(endState.assets - baseEndState.assets) / 10}
            suffix=" %"
            decimals={1}
            footer="Asset trajectory"
          />
        </div>

        <Card>
          <CardHeader
            title="Projected balance sheet"
            subtitle="Asset composition under your assumptions"
          />
          <CardBody>
            <BalanceSheetProjectionChart data={adjusted} horizonMonths={horizon} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Active assumptions" subtitle="Apply to capital plan submission" />
          <CardBody className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {strategicAssumptions.map((a) => (
              <div key={a.label} className="space-y-1">
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  {a.label}
                </p>
                <p className="font-mono text-h3 text-navy tabular-nums">
                  {a.value}
                  {a.suffix}
                </p>
                <p className="text-caption text-slate">
                  Plan{' '}
                  <span className="font-mono text-navy">
                    {a.planValue}
                    {a.suffix}
                  </span>{' '}
                  · Var{' '}
                  <span className={a.variance >= 0 ? 'text-success' : 'text-warning'}>
                    {fmtPctSigned(a.variance, 1)}
                  </span>
                </p>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
