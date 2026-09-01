'use client';

/**
 * Migration-implied PD panel (advisory) for the Delinquency & Migration tab.
 *
 * Everything here is analysis, not authority: the advisory statement renders
 * verbatim from the backend, not-estimable cells show their reason instead of
 * a number, and the ECL rows are SUGGESTIONS the approver may adopt by hand
 * in Institution → Registers — this panel writes nothing.
 */

import type { Column } from '@/components/ui/DataTable';
import type { CreditPdRead, EclSuggestionRead, PdEstimateRead } from '@aequoros/risk-service-api';
import Link from 'next/link';
import DataTable from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import { num } from '@/lib/api/values';
import { fmtInt } from '@/lib/format';

function pct(value: PdEstimateRead['pd12mPct']): string | null {
  if (value == null) return null;
  return `${num(value).toFixed(2)}%`;
}

const estimateColumns: Column<PdEstimateRead>[] = [
  {
    key: 'grade',
    header: 'Grade',
    render: (r) => (
      <span className="text-navy">
        {r.grade}
        {r.segment ? <span className="text-slate"> · {r.segment}</span> : null}
      </span>
    ),
  },
  {
    key: 'loanMonths',
    header: 'Loan-months',
    align: 'right',
    numeric: true,
    render: (r) => fmtInt(r.loanMonths),
  },
  {
    key: 'defaults',
    header: 'Defaults observed',
    align: 'right',
    numeric: true,
    render: (r) => fmtInt(r.defaultsObserved),
  },
  {
    key: 'hazard',
    header: 'Monthly hazard',
    align: 'right',
    numeric: true,
    render: (r) => pct(r.monthlyHazardPct) ?? '—',
  },
  {
    key: 'pd',
    header: '12-month PD',
    align: 'right',
    numeric: true,
    render: (r) =>
      r.pd12mPct != null ? (
        <span className="font-medium text-navy">{pct(r.pd12mPct)}</span>
      ) : (
        <span className="text-slate" title={r.notEstimableReason ?? undefined}>
          Not estimable
        </span>
      ),
  },
];

const suggestionColumns: Column<EclSuggestionRead>[] = [
  {
    key: 'segment',
    header: 'Segment',
    render: (r) => <span className="text-navy">{r.segment}</span>,
  },
  { key: 'stage', header: 'Stage', align: 'right', numeric: true, render: (r) => `${r.stage}` },
  {
    key: 'pd',
    header: 'Suggested 12-month PD',
    align: 'right',
    numeric: true,
    render: (r) => `${num(r.suggestedPdPct).toFixed(2)}%`,
  },
  {
    key: 'basis',
    header: 'Evidence base',
    render: (r) => <span className="text-slate">{r.basis}</span>,
  },
];

export default function PdPanel({ pd }: { pd: CreditPdRead }) {
  if (!pd.available) {
    return (
      <SectionCard title="Migration-implied PD (advisory)">
        <p className="text-body text-slate leading-relaxed">{pd.reason}</p>
        <p className="mt-3 text-caption text-slate leading-relaxed">{pd.advisoryStatement}</p>
      </SectionCard>
    );
  }
  const window = pd.windowStart ? `${pd.windowStart} → ${pd.asOf}` : pd.asOf;
  const reasons = [
    ...new Set(
      [...(pd.overall ?? []), ...(pd.segments ?? [])]
        .map((estimate) => estimate.notEstimableReason)
        .filter((reason): reason is string => Boolean(reason))
    ),
  ];
  return (
    <>
      <SectionCard
        title="Migration-implied PD (advisory)"
        subtitle={`${window} · ${fmtInt(pd.monthPairsObserved ?? 0)} month pairs · ${fmtInt(pd.matchedLoanMonths ?? 0)} matched performing loan-months (${fmtInt(pd.exitedLoanMonths ?? 0)} excluded as departures)`}
        noPadding
        footer={pd.advisoryStatement}
      >
        <DataTable
          columns={estimateColumns}
          rows={[...(pd.overall ?? []), ...(pd.segments ?? [])]}
          density="compact"
        />
        {reasons.length > 0 ? (
          <div className="border-t border-mist px-5 py-3">
            {reasons.map((reason) => (
              <p key={reason} className="text-caption text-slate leading-relaxed">
                {reason}
              </p>
            ))}
          </div>
        ) : null}
      </SectionCard>
      {(pd.eclSuggestions ?? []).length > 0 ? (
        <SectionCard
          title="Suggested ECL register rows"
          subtitle="Display only — the platform never writes the register. The approver may adopt these through the ordinary register approval path."
          noPadding
          footer={
            <span>
              Adopt or ignore in{' '}
              <Link href="/institution/registers" className="text-teal underline underline-offset-2">
                Institution → Registers → ECL assumptions
              </Link>
              . Until adopted there, these figures affect no calculation.
            </span>
          }
        >
          <DataTable columns={suggestionColumns} rows={pd.eclSuggestions ?? []} density="compact" />
        </SectionCard>
      ) : null}
    </>
  );
}
