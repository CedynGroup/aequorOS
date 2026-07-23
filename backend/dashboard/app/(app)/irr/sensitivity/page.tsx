'use client';

/**
 * EVE & NII sensitivity: full scenario table across the seven engine runs
 * (baseline + six Basel shocks), ΔEVE tornado, earnings-at-risk block, and a
 * short methodology note mirroring the backend engine's documented approach.
 */

import type { IrrEveScenarioRead } from '@aequoros/risk-service-api';
import IrrWorkspace from '@/components/irr/IrrWorkspace';
import TornadoChart from '@/components/irr/charts/TornadoChart';
import {
  scenarioDescription,
  scenarioLabel,
} from '@/components/irr/scenarios';
import DataTable, { type Column } from '@/components/ui/DataTable';
import KpiStat from '@/components/ui/KpiStat';
import RunBadge from '@/components/ui/RunBadge';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ValidationList from '@/components/ui/ValidationList';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

export default function IrrSensitivityPage() {
  return (
    <IrrWorkspace
      crumb="EVE & NII"
      subtitle="Economic value and earnings sensitivity under the Basel IRRBB shock set"
    >
      {({ data, metrics: m, latestRun, computedAt }) => {
        const eveLimit = num(m.eveLimitPct);
        const rows = data.eveScenarios ?? [];
        const runBadge = latestRun ? <RunBadge run={latestRun} /> : undefined;

        const eveBars = rows
          .filter((s) => s.scenarioCode !== 'baseline')
          .map((s) => ({
            label: scenarioLabel(s.scenarioCode),
            value: num(s.deltaEveGhs),
            pctTier1: num(s.deltaEvePctTier1),
            breach: s.breach,
          }));

        const earUp = num(m.earUp200Ghs);
        const earDown = num(m.earDown200Ghs);
        const earValidations = data.validations.filter(
          (v) => v.ruleCode === 'ear_within_limit'
        );

        const columns: Column<IrrEveScenarioRead>[] = [
          {
            key: 'scenario',
            header: 'Scenario',
            width: '18%',
            render: (r) => (
              <span className="font-medium text-navy">
                {scenarioLabel(r.scenarioCode)}
              </span>
            ),
          },
          {
            key: 'shock',
            header: 'Shock shape',
            width: '28%',
            render: (r) => (
              <span className="text-slate">
                {scenarioDescription(r.scenarioCode) ?? '—'}
              </span>
            ),
          },
          {
            key: 'eve',
            header: 'EVE',
            numeric: true,
            render: (r) => fmtCurrency(num(r.eveGhs)),
          },
          {
            key: 'delta',
            header: 'ΔEVE',
            numeric: true,
            render: (r) => {
              const v = num(r.deltaEveGhs);
              return (
                <span className={v < 0 ? 'text-critical font-medium' : undefined}>
                  {fmtCurrencySigned(v)}
                </span>
              );
            },
          },
          {
            key: 'pct',
            header: 'ΔEVE / Tier 1',
            numeric: true,
            render: (r) => {
              const v = num(r.deltaEvePctTier1);
              return (
                <span className={r.breach ? 'text-critical font-medium' : undefined}>
                  {fmtPct(v, 2)}
                </span>
              );
            },
          },
          {
            key: 'status',
            header: 'Status',
            align: 'right',
            render: (r) => (
              <StatusPill tone={r.breach ? 'breach' : 'compliant'}>
                {r.breach ? 'Breach' : 'Within limit'}
              </StatusPill>
            ),
          },
        ];

        return (
          <>
            <SectionCard
              title="EVE by scenario"
              subtitle={`Base EVE ${fmtCurrency(num(m.eveBaseGhs))} · Tier 1 ${fmtCurrency(
                num(m.tier1Ghs),
                'GHS'
              )} · supervisory limit ${eveLimit}% of Tier 1`}
              noPadding
              computedAt={computedAt}
              runBadge={runBadge}
            >
              <DataTable columns={columns} rows={rows} density="compact" />
            </SectionCard>

            <SectionCard
              title="ΔEVE tornado"
              subtitle="Scenarios ordered by economic-value impact; breaching shocks in red"
              computedAt={computedAt}
              runBadge={runBadge}
            >
              {eveBars.length > 0 ? (
                <TornadoChart data={eveBars} height={300} />
              ) : (
                <p className="text-body text-slate">No scenario results for this period.</p>
              )}
            </SectionCard>

            <SectionCard
              title="Earnings at Risk (EaR)"
              subtitle={`Twelve-month ΔNII under ±200bp parallel shocks · Base NII ${fmtCurrency(
                num(m.niiBaseGhs),
                'GHS'
              )}`}
              noPadding
              computedAt={computedAt}
              runBadge={runBadge}
            >
              <div className="p-5 grid grid-cols-1 sm:grid-cols-3 gap-4">
                <KpiStat
                  label="Base NII (annualized)"
                  value={fmtCurrency(num(m.niiBaseGhs))}
                  hint="Rate-sensitive book, swap carry included"
                />
                <KpiStat
                  label="ΔNII — rates +200bp"
                  value={fmtCurrencySigned(earUp)}
                  status={earUp < 0 ? 'warn' : 'ok'}
                  hint="Upward parallel shock"
                />
                <KpiStat
                  label="ΔNII — rates −200bp"
                  value={fmtCurrencySigned(earDown)}
                  status={earDown < 0 ? 'warn' : 'ok'}
                  hint="Downward parallel shock"
                />
              </div>
              {earValidations.length > 0 && (
                <div className="border-t border-border-light">
                  <ValidationList validations={earValidations} />
                </div>
              )}
            </SectionCard>

            <SectionCard
              title="Methodology"
              subtitle="How the engine computes these figures (regulatory-irr engine)"
            >
              <ul className="space-y-2.5 text-body text-navy/85 leading-relaxed list-disc pl-5">
                <li>
                  <span className="font-medium text-navy">EVE</span> — every
                  position is priced as a zero-coupon claim at its repricing
                  bucket midpoint on the base discount curve; each scenario
                  shifts the curve bucket-wise and re-prices the full book.
                  ΔEVE is measured against Tier 1 capital and classified
                  against the supervisory limit ({eveLimit}% here).
                </li>
                <li>
                  <span className="font-medium text-navy">EaR</span> — ΔNII =
                  Σ Gap<sub>i</sub> · Δr · (12 − m<sub>i</sub>)/12 over the
                  ≤12-month buckets, evaluated under the parallel ±200bp
                  shocks, where m<sub>i</sub> is the bucket midpoint in months.
                </li>
                <li>
                  <span className="font-medium text-navy">Swap treatment</span>{' '}
                  — interest-rate swap hedges are decomposed into paired legs
                  that sit in the repricing buckets like any other position, so
                  gap, duration, EVE and EaR all reprice the floating leg; the
                  swap&apos;s net carry (receive-leg accrual minus pay-leg
                  accrual) feeds base NII.
                </li>
                <li>
                  <span className="font-medium text-navy">Provenance</span> —
                  each official run persists the canonical input snapshot with
                  a value-based SHA-256 input hash; Tier 1 is read at run time
                  as the ΔEVE denominator but deliberately kept out of the
                  hash, scoping reproducibility to positions, hedges and IRR
                  parameters.
                </li>
              </ul>
            </SectionCard>
          </>
        );
      }}
    </IrrWorkspace>
  );
}
