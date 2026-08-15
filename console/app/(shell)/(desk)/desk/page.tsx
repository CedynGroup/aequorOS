'use client';

import { useMemo, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowUpRight } from 'lucide-react';
import {
  listCurveDefinitions,
  listDeskDeterminations,
  listDeskPublications,
  listOperatingEnvironmentAssessments,
  type DeskDetermination,
  type DeskPublication,
  type OperatingEnvironmentAssessment,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, num, relTime, DASH } from '@/lib/format';
import { DeterminationStatusPill, MethodologyChip, QaBadge } from '@/components/desk';
import {
  Chip,
  type Column,
  DataTable,
  EmptyState,
  KpiStat,
  PageHeader,
  QueryBoundary,
  SectionCard,
} from '@/components/ui';

/**
 * /desk — the Markets Desk cockpit. A read-only aggregation over the existing
 * desk list endpoints: a KPI row (pending determinations, approved curve codes,
 * publications today, latest operating-environment score) and three compact
 * tables — recent determinations, recent publications, and the cross-surface
 * approvals queue — each linking into its sub-page. No mutations here.
 */

type PendingRow = {
  kind: string;
  reference: string;
  cob: string;
  who: string;
  status: string;
  href: string;
};

const SectionLink = ({ href, children }: { href: string; children: ReactNode }) => (
  <Link
    href={href}
    className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
  >
    {children} <ArrowUpRight size={13} aria-hidden />
  </Link>
);

export default function DeskIndexPage() {
  const router = useRouter();

  const { data, error, loading, reload } = useApi(
    () =>
      Promise.all([
        listDeskDeterminations(),
        listDeskPublications(),
        listCurveDefinitions(),
        listOperatingEnvironmentAssessments(),
      ]).then(([determinations, publications, curves, oe]) => ({
        determinations: determinations.determinations,
        publications: publications.publications,
        curves: curves.definitions,
        oe: oe.assessments,
      })),
    [],
  );

  const derived = useMemo(() => {
    if (!data) return null;
    const today = new Date().toISOString().slice(0, 10);
    const pendingDeterminations = data.determinations.filter((d) => d.status === 'pending_review');
    const approvedCurveCodes = new Set(
      data.curves.filter((c) => c.status === 'approved').map((c) => c.curve_code),
    );
    const publishedToday = data.publications.filter((p) => fmtDate(p.published_at) === today).length;
    const latestOe = data.oe.find((a) => a.status === 'published') ?? data.oe[0] ?? null;

    const pendingRows: PendingRow[] = [
      ...pendingDeterminations.map((d) => ({
        kind: 'Curve determination',
        reference: `${d.methodology_code} · ${fmtDate(d.cob_date)}`,
        cob: d.cob_date,
        who: d.prepared_by,
        status: d.status,
        href: `/desk/determinations/${d.id}`,
      })),
      ...data.oe
        .filter((a) => a.status === 'pending_review')
        .map((a) => ({
          kind: 'Operating environment',
          reference: `${a.jurisdiction_code} · ${fmtDate(a.cob_date)}`,
          cob: a.cob_date,
          who: a.proposed_by,
          status: a.status,
          href: `/desk/operating-environment`,
        })),
    ].sort((a, b) => (a.cob < b.cob ? 1 : -1));

    return {
      pendingCount: pendingDeterminations.length,
      approvedCurveCount: approvedCurveCodes.size,
      publishedToday,
      latestOe,
      pendingRows,
    };
  }, [data]);

  const determinationColumns = useMemo<Column<DeskDetermination>[]>(
    () => [
      {
        key: 'cob',
        header: 'COB',
        sortable: true,
        sortAccessor: (d) => d.cob_date,
        render: (d) => <span className="font-mono text-caption text-ink">{fmtDate(d.cob_date)}</span>,
      },
      {
        key: 'methodology',
        header: 'Methodology',
        render: (d) => <MethodologyChip code={d.methodology_code} version={d.methodology_version} />,
      },
      { key: 'status', header: 'Status', render: (d) => <DeterminationStatusPill status={d.status} /> },
      { key: 'qa', header: 'Rates QA', render: (d) => <QaBadge determination={d} /> },
      {
        key: 'prepared_by',
        header: 'Prepared by',
        render: (d) => <span className="font-mono text-micro text-slate">{d.prepared_by}</span>,
      },
      {
        key: 'created',
        header: 'Created',
        sortable: true,
        sortAccessor: (d) => d.created_at,
        render: (d) => (
          <span className="text-micro text-slate" title={fmtTs(d.created_at)}>
            {relTime(d.created_at)}
          </span>
        ),
      },
    ],
    [],
  );

  const publicationColumns = useMemo<Column<DeskPublication>[]>(
    () => [
      {
        key: 'determination',
        header: 'Determination',
        render: (p) => (
          <span className="font-mono text-caption text-ink">{p.determination_id.slice(0, 8)}…</span>
        ),
      },
      { key: 'status', header: 'Status', render: (p) => <DeterminationStatusPill status={p.status} /> },
      {
        key: 'published_by',
        header: 'Published by',
        render: (p) => <span className="font-mono text-micro text-slate">{p.published_by}</span>,
      },
      {
        key: 'banks',
        header: 'Tenants',
        numeric: true,
        render: (p) => p.results.length,
      },
      {
        key: 'when',
        header: 'When',
        sortable: true,
        sortAccessor: (p) => p.published_at,
        render: (p) => (
          <span className="text-micro text-slate" title={fmtTs(p.published_at)}>
            {relTime(p.published_at)}
          </span>
        ),
      },
    ],
    [],
  );

  const pendingColumns = useMemo<Column<PendingRow>[]>(
    () => [
      {
        key: 'kind',
        header: 'Surface',
        render: (r) => <Chip tone={r.kind === 'Operating environment' ? 'accent' : 'neutral'}>{r.kind}</Chip>,
      },
      {
        key: 'reference',
        header: 'Reference',
        render: (r) => <span className="text-caption text-ink">{r.reference}</span>,
      },
      {
        key: 'who',
        header: 'Proposed by',
        render: (r) => <span className="font-mono text-micro text-slate">{r.who}</span>,
      },
      { key: 'status', header: 'Status', render: (r) => <DeterminationStatusPill status={r.status} /> },
    ],
    [],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Markets Desk"
        subtitle="AequorOS' own market-research operation — the weekly determination pipeline, curve golden copies, and the approvals queue at a glance."
      />

      <QueryBoundary loading={loading} error={error} onRetry={reload} context="Loading the desk cockpit">
        {derived && data && (
          <>
            {/* KPI row */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <KpiStat
                label="Pending determinations"
                value={derived.pendingCount}
                status={derived.pendingCount > 0 ? 'warn' : 'ok'}
                hint="awaiting approval"
              />
              <KpiStat
                label="Approved curves"
                value={derived.approvedCurveCount}
                status="ok"
                hint="golden-copy codes"
              />
              <KpiStat
                label="Published today"
                value={derived.publishedToday}
                status="ok"
                hint={new Date().toISOString().slice(0, 10)}
              />
              <KpiStat
                label="Latest OE score"
                value={derived.latestOe ? num(derived.latestOe.score).toFixed(3) : DASH}
                hint={
                  derived.latestOe
                    ? `${derived.latestOe.jurisdiction_code} · ${derived.latestOe.status}`
                    : 'none yet'
                }
              />
            </div>

            {/* Approvals queue */}
            <SectionCard
              title="Pending approvals"
              subtitle="Determinations and operating-environment assessments awaiting a distinct approver (four-eyes)."
              actions={<SectionLink href="/desk/determinations">Determinations</SectionLink>}
              noPadding
            >
              {derived.pendingRows.length === 0 ? (
                <EmptyState title="Nothing awaiting approval" hint="The maker-checker queue is clear." />
              ) : (
                <DataTable
                  columns={pendingColumns}
                  rows={derived.pendingRows}
                  density="compact"
                  onRowClick={(r) => router.push(r.href)}
                />
              )}
            </SectionCard>

            {/* Recent determinations + publications */}
            <div className="grid gap-6 xl:grid-cols-2">
              <SectionCard
                title="Recent determinations"
                actions={<SectionLink href="/desk/determinations">View all</SectionLink>}
                noPadding
              >
                {data.determinations.length === 0 ? (
                  <EmptyState title="No determinations yet" hint="The weekly pipeline has not run." />
                ) : (
                  <DataTable
                    columns={determinationColumns}
                    rows={data.determinations.slice(0, 8)}
                    density="compact"
                    onRowClick={(d) => router.push(`/desk/determinations/${d.id}`)}
                  />
                )}
              </SectionCard>

              <SectionCard
                title="Recent publications"
                actions={<SectionLink href="/desk/publications">View all</SectionLink>}
                noPadding
              >
                {data.publications.length === 0 ? (
                  <EmptyState title="No publications yet" hint="Approved determinations publish here." />
                ) : (
                  <DataTable
                    columns={publicationColumns}
                    rows={data.publications.slice(0, 8)}
                    density="compact"
                    onRowClick={(p) => router.push(`/desk/determinations/${p.determination_id}`)}
                  />
                )}
              </SectionCard>
            </div>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
