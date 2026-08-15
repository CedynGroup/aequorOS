'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Plus, X } from 'lucide-react';
import {
  createDeskDetermination,
  listDeskDeterminations,
  toApiError,
  type ApiError,
  type DeskDetermination,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  CurvesQaBadge,
  DeterminationStatusPill,
  MethodologyChip,
  QaBadge,
} from '@/components/desk';
import {
  Button,
  Chip,
  DataTable,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  KpiStat,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
  type Column,
} from '@/components/ui';

/**
 * /desk/determinations — Research Desk work queue (Track 1 weekly rates).
 * Capture job stages drafts for the Analyst; never auto-submits. The status +
 * COB filters are wired to the API's server-side query params.
 * Source: GET /operator/v1/desk/determinations.
 */

const CAPTURE_IDENTITY = 'desk-capture-job@aequoros.system';

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'pending_review', label: 'Pending review' },
  { value: 'approved', label: 'Approved' },
  { value: 'published', label: 'Published' },
  { value: 'rejected', label: 'Rejected' },
];

function queueOf(d: DeskDetermination): 'analyst' | 'supervisor' | 'published' | 'other' {
  if (d.status === 'draft') return 'analyst';
  if (d.status === 'pending_review') return 'supervisor';
  if (d.status === 'published') return 'published';
  return 'other';
}

export default function DeterminationsPage() {
  const router = useRouter();

  // Unfiltered fetch drives the KPI counts + weekly-capture callout.
  const all = useApi(() => listDeskDeterminations());

  // Filter bar — wired to the API's server-side cob_date / status params.
  const [statusFilter, setStatusFilter] = useState('');
  const [cobFilter, setCobFilter] = useState('');
  const filtersActive = statusFilter !== '' || cobFilter !== '';
  const queue = useApi(
    () =>
      listDeskDeterminations({
        status: statusFilter || undefined,
        cobDate: cobFilter || undefined,
      }),
    [statusFilter, cobFilter],
  );

  const [creating, setCreating] = useState(false);
  const [cobDate, setCobDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState<ApiError | null>(null);

  const counts = useMemo(() => {
    const rows = all.data?.determinations ?? [];
    return {
      analyst: rows.filter((d) => queueOf(d) === 'analyst').length,
      supervisor: rows.filter((d) => queueOf(d) === 'supervisor').length,
      published: rows.filter((d) => queueOf(d) === 'published').length,
      newCapture: rows.filter((d) => d.status === 'draft' && d.prepared_by === CAPTURE_IDENTITY),
    };
  }, [all.data]);

  async function create() {
    setSubmitting(true);
    setCreateError(null);
    try {
      const row = await createDeskDetermination({ cob_date: cobDate });
      router.push(`/desk/determinations/${row.id}`);
    } catch (err) {
      setCreateError(toApiError(err));
      setSubmitting(false);
    }
  }

  const columns: Column<DeskDetermination>[] = [
    {
      key: 'cob',
      header: 'COB date',
      sortable: true,
      sortAccessor: (d) => d.cob_date,
      render: (d) => (
        <div>
          <span className="font-medium text-navy">{fmtDate(d.cob_date)}</span>
          <div className="font-mono text-micro text-slate" title={d.input_digest}>
            {d.input_digest.slice(0, 12)}…
          </div>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (d) => d.status,
      render: (d) => <DeterminationStatusPill status={d.status} />,
    },
    {
      key: 'methodology',
      header: 'Methodology',
      render: (d) => <MethodologyChip code={d.methodology_code} version={d.methodology_version} />,
    },
    {
      key: 'qa',
      header: 'Rates / curves',
      render: (d) => (
        <div className="flex flex-wrap gap-1">
          <QaBadge determination={d} />
          <CurvesQaBadge determination={d} />
          {d.prepared_by === CAPTURE_IDENTITY && d.status === 'draft' && (
            <Chip tone="accent">new capture</Chip>
          )}
          {(d.research_adjustments?.length ?? 0) > 0 && (
            <Chip tone="warn">{d.research_adjustments.length} adj</Chip>
          )}
        </div>
      ),
    },
    {
      key: 'prepared',
      header: 'Prepared by',
      sortable: true,
      sortAccessor: (d) => d.prepared_by,
      render: (d) => <span className="font-mono text-caption text-ink">{d.prepared_by}</span>,
    },
    {
      key: 'reviewed',
      header: 'Reviewed by',
      render: (d) => <span className="font-mono text-caption text-ink">{d.reviewed_by ?? DASH}</span>,
    },
    {
      key: 'published',
      header: 'Published',
      sortable: true,
      sortAccessor: (d) => d.published_at ?? '',
      render: (d) => <span className="text-caption text-ink">{fmtDate(d.published_at)}</span>,
    },
    {
      key: 'correction',
      header: 'Correction',
      render: (d) =>
        d.supersedes_id ? (
          <Chip tone="warn" title={`supersedes ${d.supersedes_id}`}>
            supersedes
          </Chip>
        ) : (
          <span className="text-slate-light">{DASH}</span>
        ),
    },
  ];

  const queueRows = queue.data?.determinations ?? [];

  return (
    <div>
      <PageHeader
        title="Research Desk"
        sub="Weekly rates workflow: capture stages a draft for the Analyst → review & adjustments → submit → Supervisor approve → publish. Track 2 methodology changes live under Methodology."
        action={
          <Button icon={<Plus size={15} />} onClick={() => setCreating((v) => !v)}>
            New determination
          </Button>
        }
      />

      {counts.newCapture.length > 0 && (
        <div className="card mb-5 border-action/40 bg-action-light/40 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone="accent">New weekly capture</Chip>
            <span className="text-body font-medium text-navy">
              {counts.newCapture.length} package
              {counts.newCapture.length === 1 ? '' : 's'} waiting for Analyst review
            </span>
          </div>
          <ul className="mt-2 space-y-1">
            {counts.newCapture.slice(0, 5).map((d) => (
              <li key={d.id}>
                <Link
                  href={`/desk/determinations/${d.id}`}
                  className="text-body text-action hover:underline"
                >
                  COB {fmtDate(d.cob_date)} — open rates package
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <KpiStat
          label="Awaiting analyst"
          value={counts.analyst}
          status={counts.analyst > 0 ? 'warn' : 'ok'}
          hint="Draft packages to review & adjust"
        />
        <KpiStat
          label="Awaiting supervisor"
          value={counts.supervisor}
          status={counts.supervisor > 0 ? 'warn' : 'ok'}
          hint="Submitted for four-eyes approval"
        />
        <KpiStat label="Published" value={counts.published} hint="Live on client Markets tabs" />
      </div>

      {creating && (
        <SectionCard title="Open a draft determination" className="mb-5">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="COB date" className="w-48">
              <Input type="date" value={cobDate} onChange={(e) => setCobDate(e.target.value)} />
            </Field>
            <Button
              onClick={() => void create()}
              loading={submitting}
              disabled={submitting || !cobDate}
            >
              Open draft
            </Button>
            <p className="max-w-xl text-caption text-slate">
              Opens a draft bound to the methodology version effective on this date and snapshots the
              current observations. The API refuses when no approved methodology is effective or no
              observations exist on or before the date.
            </p>
          </div>
          {createError && (
            <div className="mt-3">
              <ErrorPanel error={createError} context="Opening a draft determination" />
              {createError.status === 409 && (
                <p className="mt-2 text-caption text-slate">
                  A determination is by definition an application of an approved methodology to
                  captured observations — approve one under{' '}
                  <Link href="/desk/methodology" className="text-action hover:underline">
                    Methodology
                  </Link>{' '}
                  or enter observations under{' '}
                  <Link href="/desk/observations" className="text-action hover:underline">
                    Observations
                  </Link>{' '}
                  first.
                </p>
              )}
            </div>
          )}
        </SectionCard>
      )}

      <SectionCard
        title="Determinations"
        subtitle="Newest COB first. Status and COB filters query the desk API server-side."
        noPadding
      >
        <div className="flex flex-wrap items-end gap-3 border-b border-border-light px-4 py-3">
          <Field label="Status" className="w-44">
            <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="COB date" className="w-44">
            <Input type="date" value={cobFilter} onChange={(e) => setCobFilter(e.target.value)} />
          </Field>
          {filtersActive && (
            <Button
              variant="ghost"
              size="sm"
              icon={<X size={13} />}
              onClick={() => {
                setStatusFilter('');
                setCobFilter('');
              }}
            >
              Clear
            </Button>
          )}
        </div>

        {queue.loading && <SkeletonRows rows={6} />}

        {queue.error && (
          <div className="p-4">
            <ErrorPanel error={queue.error} onRetry={queue.reload} context="Loading determinations" />
          </div>
        )}

        {queue.data && queueRows.length === 0 && (
          <EmptyState
            title={filtersActive ? 'No determinations match these filters' : 'No determinations yet'}
            hint={
              filtersActive
                ? 'Clear the status / COB filters to see the full queue.'
                : 'Open a draft for a COB date to start the weekly Track-1 run. It needs an approved methodology and captured observations.'
            }
          />
        )}

        {queue.data && queueRows.length > 0 && (
          <DataTable
            columns={columns}
            rows={queueRows}
            density="compact"
            pageSize={15}
            onRowClick={(d) => router.push(`/desk/determinations/${d.id}`)}
            getFilterText={(d) =>
              `${fmtDate(d.cob_date)} ${d.status} ${d.methodology_code} ${d.prepared_by} ${
                d.reviewed_by ?? ''
              }`
            }
            filterPlaceholder="Filter by COB, methodology, operator…"
          />
        )}
      </SectionCard>
    </div>
  );
}
