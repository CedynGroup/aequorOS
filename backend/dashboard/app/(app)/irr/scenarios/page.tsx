'use client';

/**
 * Scenarios: side-by-side comparison of two stored scenario results and the
 * provenance register of official IRR runs (immutable, value-hashed). The
 * comparison spread is simple presentation arithmetic on two engine outputs
 * and is labelled as such.
 */

import { useState } from 'react';
import type {
  IrrEveScenarioRead,
  RegulatoryRunSummaryRead,
} from '@aequoros/risk-service-api';
import IrrWorkspace from '@/components/irr/IrrWorkspace';
import { useIrrRuns } from '@/components/irr/hooks';
import {
  scenarioDescription,
  scenarioLabel,
} from '@/components/irr/scenarios';
import CopyButton from '@/components/ui/CopyButton';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RunBadge from '@/components/ui/RunBadge';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { fmtTimestamp, labelize, num, shortId } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

function runTone(status: string): StatusTone {
  if (status === 'succeeded') return 'compliant';
  if (status === 'failed') return 'breach';
  return 'pending';
}

const selectClass =
  'rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-caption font-medium text-navy hover:border-slate focus:outline-none focus:ring-1 focus:ring-action';

export default function IrrScenariosPage() {
  const [codeA, setCodeA] = useState<string>('parallel_up_200');
  const [codeB, setCodeB] = useState<string>('parallel_down_200');
  const [thisPeriodOnly, setThisPeriodOnly] = useState(true);

  return (
    <IrrWorkspace
      crumb="Scenarios"
      subtitle="Official-run scenario comparison and the immutable run register"
    >
      {({ data, metrics: m, latestRun, computedAt, bankId, periodId }) => {
        const scenarios = data.eveScenarios ?? [];
        const scenarioA =
          scenarios.find((s) => s.scenarioCode === codeA) ?? scenarios[0];
        const scenarioB =
          scenarios.find((s) => s.scenarioCode === codeB) ?? scenarios[1];
        const runBadge = latestRun ? <RunBadge run={latestRun} /> : undefined;

        return (
          <>
            <SectionCard
              title="Compare two scenarios"
              subtitle="Stored engine results side by side — spread is display arithmetic on the two figures"
              actions={
                <div className="flex items-center gap-2">
                  <ScenarioSelect
                    id="scenario-a"
                    label="Scenario A"
                    value={scenarioA?.scenarioCode ?? ''}
                    options={scenarios}
                    onChange={setCodeA}
                  />
                  <span className="text-caption text-slate">vs</span>
                  <ScenarioSelect
                    id="scenario-b"
                    label="Scenario B"
                    value={scenarioB?.scenarioCode ?? ''}
                    options={scenarios}
                    onChange={setCodeB}
                  />
                </div>
              }
              computedAt={computedAt}
              runBadge={runBadge}
            >
              {scenarioA && scenarioB ? (
                <ComparePanel a={scenarioA} b={scenarioB} limitPct={num(m.eveLimitPct)} />
              ) : (
                <p className="text-body text-slate">
                  Run all scenarios to populate the comparison.
                </p>
              )}
            </SectionCard>

            <RunRegister
              bankId={bankId}
              periodId={thisPeriodOnly ? periodId : undefined}
              thisPeriodOnly={thisPeriodOnly}
              onToggleScope={() => setThisPeriodOnly((v) => !v)}
            />
          </>
        );
      }}
    </IrrWorkspace>
  );
}

function ScenarioSelect({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: IrrEveScenarioRead[];
  onChange: (code: string) => void;
}) {
  return (
    <label htmlFor={id} className="inline-flex items-center gap-1.5">
      <span className="sr-only">{label}</span>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={selectClass}
      >
        {options.map((s) => (
          <option key={s.scenarioCode} value={s.scenarioCode}>
            {scenarioLabel(s.scenarioCode)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ComparePanel({
  a,
  b,
  limitPct,
}: {
  a: IrrEveScenarioRead;
  b: IrrEveScenarioRead;
  limitPct: number;
}) {
  const rows: {
    label: string;
    render: (s: IrrEveScenarioRead) => React.ReactNode;
    spread?: React.ReactNode;
  }[] = [
    {
      label: 'Shock shape',
      render: (s) => (
        <span className="text-slate">{scenarioDescription(s.scenarioCode) ?? '—'}</span>
      ),
    },
    {
      label: 'EVE',
      render: (s) => (
        <span className="font-mono tnum text-navy">{fmtCurrency(num(s.eveGhs))}</span>
      ),
      spread: (
        <span className="font-mono tnum text-navy">
          {fmtCurrencySigned(num(a.eveGhs) - num(b.eveGhs))}
        </span>
      ),
    },
    {
      label: 'ΔEVE vs base',
      render: (s) => {
        const v = num(s.deltaEveGhs);
        return (
          <span className={`font-mono tnum ${v < 0 ? 'text-critical' : 'text-navy'}`}>
            {fmtCurrencySigned(v)}
          </span>
        );
      },
      spread: (
        <span className="font-mono tnum text-navy">
          {fmtCurrencySigned(num(a.deltaEveGhs) - num(b.deltaEveGhs))}
        </span>
      ),
    },
    {
      label: 'ΔEVE / Tier 1',
      render: (s) => {
        const v = num(s.deltaEvePctTier1);
        return (
          <span className={`font-mono tnum ${s.breach ? 'text-critical font-medium' : 'text-navy'}`}>
            {fmtPct(v, 2)}
          </span>
        );
      },
      spread: (
        <span className="font-mono tnum text-navy">
          {fmtPct(num(a.deltaEvePctTier1) - num(b.deltaEvePctTier1), 2)}
        </span>
      ),
    },
    {
      label: `Status vs ${limitPct}% limit`,
      render: (s) => (
        <StatusPill tone={s.breach ? 'breach' : 'compliant'}>
          {s.breach ? 'Breach' : 'Within limit'}
        </StatusPill>
      ),
    },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse">
        <thead>
          <tr className="border-b border-border bg-surface">
            <th className="px-4 py-2 text-left text-micro font-medium uppercase tracking-wider text-slate w-[22%]" />
            <th className="px-4 py-2 text-left text-micro font-medium uppercase tracking-wider text-navy">
              {scenarioLabel(a.scenarioCode)}
            </th>
            <th className="px-4 py-2 text-left text-micro font-medium uppercase tracking-wider text-navy">
              {scenarioLabel(b.scenarioCode)}
            </th>
            <th className="px-4 py-2 text-left text-micro font-medium uppercase tracking-wider text-slate">
              Spread (A − B)
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-b border-border-light last:border-b-0">
              <td className="px-4 py-2.5 text-caption font-medium text-slate">{row.label}</td>
              <td className="px-4 py-2.5">{row.render(a)}</td>
              <td className="px-4 py-2.5">{row.render(b)}</td>
              <td className="px-4 py-2.5">
                {row.spread ?? <span className="text-slate-light">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunRegister({
  bankId,
  periodId,
  thisPeriodOnly,
  onToggleScope,
}: {
  bankId: string | undefined;
  periodId: string | undefined;
  thisPeriodOnly: boolean;
  onToggleScope: () => void;
}) {
  const runs = useIrrRuns(bankId, { reportingPeriodId: periodId });
  const rows = runs.data?.runs ?? [];

  const columns: Column<RegulatoryRunSummaryRead>[] = [
    {
      key: 'created',
      header: 'Created',
      width: '16%',
      render: (r) => (
        <span className="font-mono text-caption tnum text-navy">
          {fmtTimestamp(r.createdAt)}
        </span>
      ),
    },
    {
      key: 'period',
      header: 'Period',
      render: (r) => <span className="text-slate">{r.periodLabel}</span>,
    },
    {
      key: 'scenario',
      header: 'Scenario',
      render: (r) => (
        <span className="font-medium text-navy">{scenarioLabel(r.scenarioCode)}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (r) => (
        <StatusPill tone={runTone(r.status)}>{labelize(r.status)}</StatusPill>
      ),
    },
    {
      key: 'engine',
      header: 'Engine',
      render: (r) => (
        <span className="font-mono text-caption text-slate">{r.engineVersion}</span>
      ),
    },
    {
      key: 'hash',
      header: 'Input hash',
      align: 'right',
      render: (r) => (
        <span className="inline-flex items-center gap-1.5 justify-end">
          <span className="font-mono text-caption text-navy" title={r.inputHash}>
            {shortId(r.inputHash, 12)}…
          </span>
          <CopyButton text={r.inputHash} label={`input hash for run ${shortId(r.id, 8)}`} />
        </span>
      ),
    },
  ];

  return (
    <SectionCard
      title="Official run register"
      subtitle={
        runs.data
          ? `${runs.data.total} immutable IRR run${runs.data.total === 1 ? '' : 's'} — each stores the full canonical input snapshot under a value-based SHA-256 hash`
          : 'Immutable IRR runs with value-based input hashes'
      }
      actions={
        <button
          type="button"
          onClick={onToggleScope}
          className="px-2.5 py-1.5 rounded-md border border-border text-caption font-medium text-slate hover:text-navy hover:bg-surface transition-colors"
        >
          {thisPeriodOnly ? 'Show all periods' : 'Show this period only'}
        </button>
      }
      noPadding
    >
      {runs.isLoading ? (
        <p className="px-5 py-4 text-body text-slate">Loading run register…</p>
      ) : rows.length === 0 ? (
        <p className="px-5 py-4 text-body text-slate">
          No official IRR runs yet — run all scenarios to mint the first batch.
        </p>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          density="compact"
          stickyHeader
          maxHeight={480}
        />
      )}
    </SectionCard>
  );
}
