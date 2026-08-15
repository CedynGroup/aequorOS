'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ExternalLink, X } from 'lucide-react';
import { listDeskPublications, type DeskPublication } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime } from '@/lib/format';
import { PublicationResults } from '@/components/desk';
import {
  Button,
  DataTable,
  Drawer,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  PageHeader,
  SectionCard,
  Select,
  SkeletonRows,
  StatusChip,
  type Column,
} from '@/components/ui';

/**
 * /desk/publications — every fan-out the desk has run, newest first, as a
 * filterable DataTable (status / determination / date). Opening a row reveals
 * the per-bank delivery results in a Drawer (partial failure is recorded, never
 * hidden) and links back to the determination it published.
 * Source: GET /operator/v1/desk/publications.
 */
export default function PublicationsPage() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi(() => listDeskPublications());

  const [statusFilter, setStatusFilter] = useState('');
  const [determinationFilter, setDeterminationFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('');
  const [selected, setSelected] = useState<DeskPublication | null>(null);

  const publications = data?.publications ?? [];

  const statuses = useMemo(
    () => Array.from(new Set(publications.map((p) => p.status))).sort(),
    [publications],
  );

  const filtered = useMemo(() => {
    return publications.filter((p) => {
      if (statusFilter && p.status !== statusFilter) return false;
      if (determinationFilter && !p.determination_id.toLowerCase().includes(determinationFilter.toLowerCase())) {
        return false;
      }
      if (dateFilter && fmtDate(p.published_at) !== dateFilter) return false;
      return true;
    });
  }, [publications, statusFilter, determinationFilter, dateFilter]);

  const filtersActive = statusFilter !== '' || determinationFilter !== '' || dateFilter !== '';

  const columns: Column<DeskPublication>[] = [
    {
      key: 'published',
      header: 'Published',
      sortable: true,
      sortAccessor: (p) => p.published_at,
      render: (p) => (
        <span className="text-caption text-navy" title={fmtTs(p.published_at)}>
          {relTime(p.published_at)}
        </span>
      ),
    },
    {
      key: 'determination',
      header: 'Determination',
      render: (p) => (
        <Link
          href={`/desk/determinations/${p.determination_id}`}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center gap-1 font-mono text-caption text-action hover:underline"
        >
          {p.determination_id.slice(0, 14)}…
          <ExternalLink size={11} aria-hidden />
        </Link>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (p) => p.status,
      render: (p) => <StatusChip value={p.status} />,
    },
    {
      key: 'banks',
      header: 'Banks',
      numeric: true,
      sortable: true,
      sortAccessor: (p) => p.results.length,
      render: (p) => p.results.length,
    },
    {
      key: 'failed',
      header: 'Failed',
      numeric: true,
      render: (p) => {
        const failed = p.results.filter(
          (r) => Boolean(r.error) || /fail|error|rolled_back/i.test(r.status ?? ''),
        ).length;
        return failed > 0 ? <span className="text-critical">{failed}</span> : '0';
      },
    },
    {
      key: 'by',
      header: 'Published by',
      render: (p) => <span className="font-mono text-caption text-ink">{p.published_by}</span>,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Publications"
        sub="Deliberate, logged fan-outs of approved determinations into every bank's canonical store — one ingestion batch per bank, superseding by natural key."
      />

      <SectionCard
        title="Fan-out history"
        subtitle="Newest first. Open a row for the per-bank delivery detail."
        noPadding
      >
        <div className="flex flex-wrap items-end gap-3 border-b border-border-light px-4 py-3">
          <Field label="Status" className="w-40">
            <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Determination" className="w-48">
            <Input
              placeholder="id contains…"
              value={determinationFilter}
              onChange={(e) => setDeterminationFilter(e.target.value)}
            />
          </Field>
          <Field label="Date" className="w-44">
            <Input type="date" value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} />
          </Field>
          {filtersActive && (
            <Button
              variant="ghost"
              size="sm"
              icon={<X size={13} />}
              onClick={() => {
                setStatusFilter('');
                setDeterminationFilter('');
                setDateFilter('');
              }}
            >
              Clear
            </Button>
          )}
        </div>

        {loading && <SkeletonRows rows={5} />}

        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading publications" />
          </div>
        )}

        {data && filtered.length === 0 && (
          <EmptyState
            title={filtersActive ? 'No publications match these filters' : 'Nothing published yet'}
            hint={
              filtersActive
                ? 'Clear the filters to see the full fan-out history.'
                : 'Approve a determination on the Determinations screen and publish it — the fan-out record appears here.'
            }
          />
        )}

        {data && filtered.length > 0 && (
          <DataTable
            columns={columns}
            rows={filtered}
            density="compact"
            pageSize={20}
            onRowClick={(p) => setSelected(p)}
            getFilterText={(p) => `${p.determination_id} ${p.status} ${p.published_by}`}
            filterPlaceholder="Filter fan-outs…"
          />
        )}
      </SectionCard>

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title="Publication fan-out"
        description={selected ? `Published ${fmtDate(selected.published_at)}` : undefined}
        width="w-[640px]"
        footer={
          selected && (
            <Button
              variant="secondary"
              icon={<ExternalLink size={14} />}
              onClick={() => router.push(`/desk/determinations/${selected.determination_id}`)}
            >
              Open determination
            </Button>
          )
        }
      >
        {selected && <PublicationResults publication={selected} />}
      </Drawer>
    </div>
  );
}
