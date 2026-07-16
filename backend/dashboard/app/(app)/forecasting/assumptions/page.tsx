'use client';

/**
 * Assumptions — read-only registry of the assumptions the forecast engine
 * actually consumed. Two real sources:
 *  1. The resolved assumption set persisted on the latest succeeded run
 *     (ResolvedForecastAssumptions), with a per-field source label derived
 *     from the run's scenario code and the preset payload.
 *  2. The preset catalogue served by the scenarios endpoint (base / adverse /
 *     severely adverse) plus the engine defaults for the three fields the
 *     presets omit.
 */

import Link from 'next/link';
import { ArrowUpRight, BookOpen, Brain } from 'lucide-react';
import type {
  ForecastRunRead,
  ForecastScenarioListRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import EmptyState from '@/components/ui/EmptyState';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary from '@/components/ui/QueryBoundary';
import {
  ASSUMPTION_FIELDS,
  type AssumptionField,
  latestSucceededId,
  scenarioLabel,
} from '@/components/forecasting/lib';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useForecastRun,
  useForecastRuns,
  useForecastScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';

export default function AssumptionsPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;

  const scenariosQuery = useForecastScenarios(bankId);
  const runsQuery = useForecastRuns(bankId, { limit: 50 });
  const latestId = latestSucceededId(runsQuery.data?.runs ?? []);
  const runQuery = useForecastRun(bankId, latestId);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'Assumptions' },
        ]}
        title="Assumption Registry"
        subtitle="The resolved assumptions the projection engine consumed, and the preset catalogue they resolve from"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <Link
            href="/behavioral"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
          >
            <Brain size={13} aria-hidden />
            Behavioral models feed these
            <ArrowUpRight size={12} aria-hidden />
          </Link>
        }
      />

      <QueryBoundary
        isLoading={scenariosQuery.isLoading || runsQuery.isLoading}
        error={scenariosQuery.error ?? runsQuery.error}
        onRetry={() => {
          void scenariosQuery.refetch();
          void runsQuery.refetch();
        }}
      >
        <div className="px-8 py-6 space-y-6">
          {/* Resolved on the latest run */}
          {!latestId ? (
            <EmptyState
              Icon={BookOpen}
              title="No succeeded forecast runs yet"
              description="The registry shows the assumption set persisted on a run. Run a forecast from the Balance Sheet tab to populate it."
            />
          ) : runQuery.data ? (
            <ResolvedSection
              run={runQuery.data}
              scenarios={scenariosQuery.data}
            />
          ) : null}

          {/* Preset catalogue */}
          {scenariosQuery.data && (
            <PresetCatalogue scenarios={scenariosQuery.data} />
          )}

          <p className="text-caption text-slate max-w-3xl leading-relaxed">
            Deposit and prepayment behavior are modelled separately by the
            per-tenant behavioral ML models; their outputs inform how the
            preset growth and margin assumptions are calibrated.{' '}
            <Link href="/behavioral" className="text-action hover:underline">
              Open Behavioral Models
            </Link>
            .
          </p>
        </div>
      </QueryBoundary>
    </>
  );
}

// ---------------------------------------------------------------------------
// Source resolution — where each resolved value came from.
// ---------------------------------------------------------------------------

type Source =
  | { kind: 'custom'; label: string }
  | { kind: 'preset'; label: string }
  | { kind: 'default'; label: string };

function sourceFor(
  field: AssumptionField,
  run: ForecastRunRead,
  scenarios: ForecastScenarioListRead | undefined
): Source {
  if (run.scenarioCode === 'custom') {
    return { kind: 'custom', label: 'Custom override' };
  }
  const preset = scenarios?.scenarios.find((s) => s.code === run.scenarioCode);
  if (preset && preset.assumptions[field.apiKey] !== undefined) {
    return {
      kind: 'preset',
      label: `${scenarioLabel(run.scenarioCode)} preset`,
    };
  }
  if (field.hasEngineDefault) {
    return { kind: 'default', label: 'Engine default' };
  }
  return { kind: 'preset', label: `${scenarioLabel(run.scenarioCode)} preset` };
}

function sourceTone(kind: Source['kind']): 'action' | 'slate' | 'amber' {
  switch (kind) {
    case 'custom':
      return 'action';
    case 'default':
      return 'amber';
    default:
      return 'slate';
  }
}

// ---------------------------------------------------------------------------
// Resolved assumption definition cards
// ---------------------------------------------------------------------------

function ResolvedSection({
  run,
  scenarios,
}: {
  run: ForecastRunRead;
  scenarios: ForecastScenarioListRead | undefined;
}) {
  const groups = Array.from(new Set(ASSUMPTION_FIELDS.map((f) => f.group)));

  return (
    <SectionCard
      title="Resolved on the latest run"
      subtitle={`The exact assumption set the engine consumed for the ${scenarioLabel(run.scenarioCode)} run — persisted, immutable, hash-bound`}
      computedAt={run.createdAt}
      runBadge={<RunBadge run={run} />}
      footer={
        <span>
          Scenario{' '}
          <span className="font-medium text-navy">
            {scenarioLabel(run.scenarioCode)}
          </span>{' '}
          · engine {run.engineVersion}
        </span>
      }
    >
      <div className="space-y-5">
        {groups.map((group) => (
          <div key={group}>
            <p className="text-micro font-medium uppercase tracking-wider text-slate mb-2.5">
              {group}
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              {ASSUMPTION_FIELDS.filter((f) => f.group === group).map(
                (field) => {
                  const source = sourceFor(field, run, scenarios);
                  const value = num(run.assumptions[field.key]);
                  return (
                    <div
                      key={field.key}
                      className="rounded-md border border-border-light bg-surface-raised p-4 flex flex-col gap-1.5"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-caption font-medium text-navy">
                          {field.label}
                        </p>
                        <StatusPill tone={sourceTone(source.kind)}>
                          {source.label}
                        </StatusPill>
                      </div>
                      <p className="font-mono text-kpi text-navy tnum">
                        {value.toFixed(field.step < 1 ? 1 : 0)}
                        <span className="text-body text-slate">{field.unit}</span>
                      </p>
                      <p className="text-caption text-slate leading-relaxed">
                        {field.definition}
                      </p>
                    </div>
                  );
                }
              )}
            </div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Preset catalogue
// ---------------------------------------------------------------------------

function PresetCatalogue({
  scenarios,
}: {
  scenarios: ForecastScenarioListRead;
}) {
  const defaults: Record<string, string> = {
    fee_income_pct_assets: scenarios.defaults.feeIncomePctAssets,
    tax_rate_pct: scenarios.defaults.taxRatePct,
    securities_shift_pp: scenarios.defaults.securitiesShiftPp,
  };

  return (
    <SectionCard
      title="Preset catalogue"
      subtitle="Assumption values served by the scenarios endpoint — presets fill most fields; the engine defaults cover the rest"
      noPadding
    >
      <div className="overflow-x-auto">
        <table className="w-full text-body border-collapse tnum">
          <thead>
            <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
              <th className="text-left px-4 py-2.5">Assumption</th>
              {scenarios.scenarios.map((s) => (
                <th key={s.code} className="text-right px-4 py-2.5">
                  {scenarioLabel(s.code)}
                </th>
              ))}
              <th className="text-right px-4 py-2.5">Engine default</th>
            </tr>
          </thead>
          <tbody>
            {ASSUMPTION_FIELDS.map((field) => {
              const fallback = defaults[field.apiKey];
              return (
                <tr
                  key={field.key}
                  className="border-b border-border-light last:border-b-0"
                >
                  <td className="px-4 py-2.5">
                    <p className="text-navy/90 font-medium">{field.label}</p>
                    <p className="text-caption text-slate">{field.definition}</p>
                  </td>
                  {scenarios.scenarios.map((s) => {
                    const raw = s.assumptions[field.apiKey];
                    return (
                      <td key={s.code} className="px-4 py-2.5 text-right font-mono tnum">
                        {raw === undefined ? (
                          <span className="text-slate" title="Preset omits this field — the engine default applies">
                            —
                          </span>
                        ) : (
                          <>
                            {num(raw)}
                            {field.unit}
                          </>
                        )}
                      </td>
                    );
                  })}
                  <td className="px-4 py-2.5 text-right font-mono tnum">
                    {fallback === undefined ? (
                      <span className="text-slate">—</span>
                    ) : (
                      <>
                        {num(fallback)}
                        {field.unit}
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}
