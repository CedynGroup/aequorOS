'use client';

import {
  Chip,
  DataTable,
  StatusChip,
  StatusPill,
  type Column,
  type StatusTone,
} from '@/components/ui';
import { fmtTs, relTime, DASH } from '@/lib/format';
import type { DeskDetermination, DeskPublication, DeskPublicationResult } from '@/lib/api';

// --------------------------------------------------------------------------
// Markets Desk shared bits: the determination state vocabulary and the QA
// verdict, rendered identically on the list, detail, and publications pages.
// All render through the design-system StatusPill (dot + bordered uppercase).
// --------------------------------------------------------------------------

/**
 * Fixed StatusPill tones for the determinations state machine
 * (services/market_desk/determinations.py): draft -> pending_review ->
 * approved -> published, with rejected as the checker's branch. An unknown
 * state falls back to the neutral `pending` tone rather than guessing severity.
 */
const DETERMINATION_TONES: Record<string, StatusTone> = {
  draft: 'slate',
  pending_review: 'amber',
  approved: 'success',
  published: 'action',
  rejected: 'critical',
};

/** The StatusPill tone bound to a determination status (neutral when unknown). */
export function determinationStatusTone(status: string): StatusTone {
  return DETERMINATION_TONES[status] ?? 'pending';
}

export function DeterminationStatusPill({ status }: { status: string }) {
  return <StatusPill tone={determinationStatusTone(status)}>{status.replace(/_/g, ' ')}</StatusPill>;
}

/**
 * Rates package readiness (gates approve/submit). Curves QA is shown
 * separately — a curves fail does not block rates publish.
 */
export function QaBadge({ determination }: { determination: DeskDetermination }) {
  const derived = determination.derived_values ?? {};
  const rates =
    derived.rates_qa_passed !== undefined ? derived.rates_qa_passed : derived.qa_passed;
  if (rates === true) return <StatusPill tone="success">rates ready</StatusPill>;
  if (rates === false) return <StatusPill tone="critical">rates fail</StatusPill>;
  return <StatusPill tone="pending">not computed</StatusPill>;
}

/** Curves QA badge — advisory for rates-first publish. */
export function CurvesQaBadge({ determination }: { determination: DeskDetermination }) {
  const curves = determination.derived_values?.curves_qa_passed;
  if (curves === true) return <StatusPill tone="success">curves pass</StatusPill>;
  if (curves === false) return <StatusPill tone="amber">curves fail</StatusPill>;
  return null;
}

/** Methodology binding as a mono chip: CODE vN. */
export function MethodologyChip({ code, version }: { code: string; version: number }) {
  return (
    <Chip mono title={`Methodology ${code} version ${version}`}>
      {code} v{version}
    </Chip>
  );
}

/**
 * One publication's header line + per-bank fan-out results, straight from
 * DeskPublication.results (services/market_desk/publication.py). Partial
 * failure is the contract there — per-bank errors are recorded, not rolled
 * back — so the DataTable renders failed banks alongside completed ones.
 */
export function PublicationResults({ publication }: { publication: DeskPublication }) {
  const results = publication.results;
  const failed = results.filter(
    (r) => Boolean(r.error) || /fail|error|rolled_back/i.test(r.status ?? ''),
  ).length;

  const columns: Column<DeskPublicationResult>[] = [
    {
      key: 'bank',
      header: 'Bank',
      sortable: true,
      sortAccessor: (r) => r.bank_id ?? '',
      render: (r) => <span className="font-mono text-caption text-ink">{r.bank_id ?? DASH}</span>,
    },
    {
      key: 'batch',
      header: 'Ingestion batch',
      render: (r) => (
        <span className="font-mono text-caption text-slate">{r.ingestion_batch_id ?? DASH}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (r) => r.status ?? '',
      render: (r) => <StatusChip value={r.status} />,
    },
    {
      key: 'error',
      header: 'Error',
      render: (r) => <span className="text-caption text-critical">{r.error ?? DASH}</span>,
    },
  ];

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
        <StatusChip value={publication.status} />
        <span>
          published by <span className="font-mono text-ink">{publication.published_by}</span>
        </span>
        <span title={fmtTs(publication.published_at)}>{relTime(publication.published_at)}</span>
        <span className="text-slate-light">·</span>
        <span className="tnum">
          {results.length} bank{results.length === 1 ? '' : 's'}
        </span>
        {failed > 0 && <Chip tone="crit">{failed} failed</Chip>}
      </div>
      {results.length === 0 ? (
        <p className="mt-2 text-caption text-slate">
          No per-bank results recorded — there were no banks to publish to.
        </p>
      ) : (
        <div className="mt-2 overflow-hidden rounded border border-border-light">
          <DataTable
            columns={columns}
            rows={results}
            density="compact"
            pageSize={25}
            getFilterText={results.length > 8 ? (r) => `${r.bank_id ?? ''} ${r.status ?? ''}` : undefined}
            filterPlaceholder="Filter banks…"
          />
        </div>
      )}
    </div>
  );
}
