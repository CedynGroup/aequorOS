'use client';

/**
 * EVE & NII sensitivity: full scenario table across the seven engine runs
 * (baseline + six Basel shocks), ΔEVE tornado, earnings-at-risk block, and a
 * short methodology note mirroring the backend engine's documented approach.
 */

import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import type {
  IrrEveScenarioRead,
  IrrMetricsRead,
  IrrValidationRead,
} from '@aequoros/risk-service-api';
import { useEarAnalysis } from '@/components/irr/hooks';
import IrrWorkspace from '@/components/irr/IrrWorkspace';
import TornadoChart from '@/components/irr/charts/TornadoChart';
import {
  scenarioDescription,
  scenarioLabel,
} from '@/components/irr/scenarios';
import DataTable, { type Column } from '@/components/ui/DataTable';
import KpiStat from '@/components/ui/KpiStat';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ValidationList from '@/components/ui/ValidationList';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

/** Desk-selectable EaR horizons; 12 months is the regulatory figure. */
const DESK_HORIZONS_MONTHS = [3, 6, 12, 24] as const;
const REGULATORY_HORIZON_MONTHS = 12;
/** The desk analysis keeps the regulatory ±200bp parallel shock for now. */
const DESK_DELTA_BP = 200;

export default function IrrSensitivityPage() {
  return (
    <IrrWorkspace
      crumb="EVE & NII"
      subtitle="Economic value and earnings sensitivity under the Basel IRRBB shock set"
    >
      {({ data, metrics: m, computedAt, bankId, periodId }) => {
        const eveLimit = num(m.eveLimitPct);
        const rows = data.eveScenarios ?? [];

        const eveBars = rows
          .filter((s) => s.scenarioCode !== 'baseline')
          .map((s) => ({
            label: scenarioLabel(s.scenarioCode),
            value: num(s.deltaEveGhs),
            pctTier1: num(s.deltaEvePctTier1),
            breach: s.breach,
          }));

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
            render: (r) => fmtCurrencySigned(num(r.deltaEveGhs)),
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
              subtitle={`Base EVE ${fmtCurrency(num(m.eveBaseGhs))} · Tier 1 ${fmtCurrency(num(m.tier1Ghs))} · supervisory limit ${eveLimit}% of Tier 1`}
              noPadding
              computedAt={computedAt}
            >
              <DataTable columns={columns} rows={rows} density="compact" />
            </SectionCard>

            <EarSection
              metrics={m}
              earValidations={earValidations}
              computedAt={computedAt}
              bankId={bankId}
              periodId={periodId}
            />

            <SectionCard
              title="ΔEVE tornado"
              subtitle="Scenarios ordered by economic-value impact; breaching shocks in red"
              computedAt={computedAt}
            >
              {eveBars.length > 0 ? (
                <TornadoChart data={eveBars} height={300} />
              ) : (
                <p className="text-body text-slate">No scenario results for this period.</p>
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
                  results are computed from the canonical position snapshot;
                  Tier 1 is read at computation time
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

// ---------------------------------------------------------------------------
// Earnings at Risk — regulatory 12-month figures plus an engine-backed desk
// horizon. The stored regulatory metrics never move; a non-12-month selection
// calls the pure analysis endpoint (nothing persisted) and renders its result
// beside them, clearly labeled.
// ---------------------------------------------------------------------------

function EarSection({
  metrics: m,
  earValidations,
  computedAt,
  bankId,
  periodId,
}: {
  metrics: IrrMetricsRead;
  earValidations: IrrValidationRead[];
  computedAt: Date | undefined;
  bankId: string | undefined;
  periodId: string | undefined;
}) {
  const [horizonMonths, setHorizonMonths] = useState<number>(
    REGULATORY_HORIZON_MONTHS
  );
  const isDeskHorizon = horizonMonths !== REGULATORY_HORIZON_MONTHS;
  const analysis = useEarAnalysis(
    bankId,
    periodId,
    horizonMonths,
    DESK_DELTA_BP,
    isDeskHorizon
  );

  const earUp = num(m.earUp200Ghs);
  const earDown = num(m.earDown200Ghs);

  return (
    <SectionCard
      title="Earnings at Risk (EaR)"
      subtitle={`Regulatory twelve-month ΔNII under ±${DESK_DELTA_BP}bp parallel shocks · Base NII ${fmtCurrency(num(m.niiBaseGhs))}`}
      noPadding
      computedAt={computedAt}
      actions={
        <label className="inline-flex items-center gap-2 text-caption text-slate">
          Desk horizon
          <select
            value={horizonMonths}
            onChange={(e) => setHorizonMonths(Number(e.target.value))}
            aria-label="Desk EaR horizon in months"
            className="px-2.5 py-1.5 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
          >
            {DESK_HORIZONS_MONTHS.map((months) => (
              <option key={months} value={months}>
                {months} months
                {months === REGULATORY_HORIZON_MONTHS ? ' (regulatory)' : ''}
              </option>
            ))}
          </select>
        </label>
      }
    >
      <div className="p-5 grid grid-cols-1 sm:grid-cols-3 gap-4">
        <KpiStat
          label="Base NII (annualized)"
          value={fmtCurrency(num(m.niiBaseGhs))}
          hint="Rate-sensitive book, swap carry included"
        />
        <KpiStat
          label={`ΔNII — rates +${DESK_DELTA_BP}bp`}
          value={fmtCurrencySigned(earUp)}
          status={earUp < 0 ? 'warn' : 'ok'}
          hint="Upward parallel shock · regulatory 12-month horizon"
        />
        <KpiStat
          label={`ΔNII — rates −${DESK_DELTA_BP}bp`}
          value={fmtCurrencySigned(earDown)}
          status={earDown < 0 ? 'warn' : 'ok'}
          hint="Downward parallel shock · regulatory 12-month horizon"
        />
      </div>

      {isDeskHorizon && (
        <div className="border-t border-border-light p-5 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusPill tone="action">
              Desk analysis · {horizonMonths}-month horizon
            </StatusPill>
            <span className="text-caption text-slate">
              Engine-computed on the canonical gap, nothing persisted — the
              regulatory 12-month figures above never change.
            </span>
          </div>
          {analysis.isLoading ? (
            <p className="inline-flex items-center gap-2 text-body text-slate">
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Computing {horizonMonths}-month earnings-at-risk…
            </p>
          ) : analysis.error ? (
            <ErrorPanel
              error={analysis.error}
              title="Desk EaR analysis failed"
              onRetry={() => analysis.refetch()}
            />
          ) : analysis.data ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <KpiStat
                label={`ΔNII — rates +${DESK_DELTA_BP}bp`}
                value={fmtCurrencySigned(num(analysis.data.earUp))}
                status={num(analysis.data.earUp) < 0 ? 'warn' : 'ok'}
                hint={`Desk analysis · ${horizonMonths}-month horizon`}
              />
              <KpiStat
                label={`ΔNII — rates −${DESK_DELTA_BP}bp`}
                value={fmtCurrencySigned(num(analysis.data.earDown))}
                status={num(analysis.data.earDown) < 0 ? 'warn' : 'ok'}
                hint={`Desk analysis · ${horizonMonths}-month horizon`}
              />
              <KpiStat
                label="Cumulative gap inside horizon"
                value={fmtCurrencySigned(
                  num(analysis.data.cumulativeGapWithinHorizon)
                )}
                hint={`Repricing buckets with midpoint inside ${horizonMonths} months`}
              />
            </div>
          ) : null}
        </div>
      )}

      {earValidations.length > 0 && (
        <div className="border-t border-border-light">
          <ValidationList validations={earValidations} />
        </div>
      )}
    </SectionCard>
  );
}
