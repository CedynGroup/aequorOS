'use client';

import {
  Chip,
  DataTable,
  SectionCard,
  StatusPill,
  type Column,
  type StatusTone,
} from '@/components/ui';
import { DASH, fmtDate } from '@/lib/format';
import type { DeskPackageCompletenessItem, DeskPackageView } from '@/lib/api';
import { show } from './util';

function statusTone(status: string): StatusTone {
  if (status === 'present') return 'success';
  if (status === 'stale') return 'amber';
  if (status === 'missing') return 'critical';
  return 'slate';
}

function ProvenanceCell({ item }: { item: DeskPackageCompletenessItem }) {
  const p = item.provenance;
  if (p.source === 'manual') {
    return (
      <span className="text-caption text-slate">
        manual · <span className="font-mono">{p.entered_by}</span>
      </span>
    );
  }
  if (p.source === 'capture') {
    return (
      <span className="text-caption text-slate">
        capture · <span className="font-mono">{p.source_key}</span>
        {p.source_url && (
          <a
            href={p.source_url}
            target="_blank"
            rel="noreferrer"
            className="ml-1 text-action hover:underline"
          >
            source
          </a>
        )}
      </span>
    );
  }
  return <span className="text-caption text-slate">{DASH}</span>;
}

/**
 * Capture completeness — required weekly series as of a COB, with per-series
 * status pills, values, and provenance. The ready/missing verdict and stale
 * count ride the card actions slot.
 */
export function CompletenessPanel({
  completeness,
}: {
  completeness: DeskPackageView['completeness'];
}) {
  const columns: Column<DeskPackageCompletenessItem>[] = [
    {
      key: 'series',
      header: 'Series',
      sortable: true,
      sortAccessor: (i) => i.series_code,
      render: (i) => <span className="font-mono text-caption text-ink">{i.series_code}</span>,
    },
    { key: 'req', header: 'Req', render: (i) => <span className="text-caption text-slate">{i.required ? 'yes' : 'opt'}</span> },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (i) => i.status,
      render: (i) => <StatusPill tone={statusTone(i.status)}>{i.status}</StatusPill>,
    },
    { key: 'value', header: 'Value', numeric: true, render: (i) => i.value ?? DASH },
    {
      key: 'asof',
      header: 'As of',
      render: (i) => (
        <span className="text-caption text-slate">{i.as_of_date ? fmtDate(i.as_of_date) : DASH}</span>
      ),
    },
    { key: 'prov', header: 'Provenance', render: (i) => <ProvenanceCell item={i} /> },
  ];

  return (
    <SectionCard
      title="Capture completeness"
      subtitle="Required weekly rates series as of this COB. Missing series need manual entry under Observations, then Recompute."
      noPadding
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill tone={completeness.ready ? 'success' : 'critical'}>
            {completeness.ready ? 'complete' : 'missing'}
          </StatusPill>
          {completeness.required_stale.length > 0 && (
            <Chip tone="warn">{completeness.required_stale.length} stale</Chip>
          )}
          <span className="text-caption text-slate tnum">
            {completeness.required_present}/{completeness.required_total} required
          </span>
        </div>
      }
    >
      <DataTable
        columns={columns}
        rows={completeness.items}
        density="compact"
        getFilterText={(i) => `${i.series_code} ${i.status}`}
        filterPlaceholder="Filter series…"
      />
      {completeness.failed_captures.length > 0 && (
        <div className="border-t border-border-light px-4 py-3">
          <h3 className="text-body font-medium text-navy">Recent failed captures</h3>
          <ul className="mt-2 space-y-1">
            {completeness.failed_captures.map((c) => (
              <li key={c.id} className="text-caption text-critical">
                <span className="font-mono">{c.source_key}</span> · {c.as_of_date}:{' '}
                {c.parse_error ?? 'failed'}
              </li>
            ))}
          </ul>
        </div>
      )}
    </SectionCard>
  );
}

/**
 * The determination's value-based input snapshot with per-field provenance —
 * the audit trail the input_digest is computed over.
 */
export function InputProvenancePanel({ pkg }: { pkg: DeskPackageView }) {
  const rows = pkg.input_provenance;
  type Entry = DeskPackageView['input_provenance'][number];
  const columns: Column<Entry>[] = [
    { key: 'series', header: 'Series', render: (e) => <span className="font-mono text-caption">{e.series_code}</span> },
    {
      key: 'asof',
      header: 'As of',
      render: (e) => (
        <span className="text-caption text-slate">
          {e.as_of_date ? fmtDate(String(e.as_of_date)) : DASH}
        </span>
      ),
    },
    { key: 'value', header: 'Value', numeric: true, render: (e) => show(e.value) },
    {
      key: 'prov',
      header: 'Provenance',
      render: (e) => (
        <span className="text-caption text-slate">
          {e.provenance?.source ?? DASH}
          {e.provenance?.source_key && <span className="ml-1 font-mono">{e.provenance.source_key}</span>}
          {e.provenance?.entered_by && <span className="ml-1 font-mono">{e.provenance.entered_by}</span>}
        </span>
      ),
    },
  ];

  return (
    <SectionCard
      title="Input snapshot & field provenance"
      subtitle={`${rows.length} ${rows.length === 1 ? 'entry' : 'entries'}`}
      noPadding
    >
      {rows.length > 0 ? (
        <DataTable
          columns={columns}
          rows={rows}
          density="compact"
          stickyHeader
          maxHeight={384}
          getFilterText={(e) => e.series_code}
          filterPlaceholder="Filter series…"
        />
      ) : (
        <p className="px-4 py-4 text-caption text-slate">
          Snapshot is empty until Compute finalizes windowed inputs.
        </p>
      )}
    </SectionCard>
  );
}
