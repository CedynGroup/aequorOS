'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import {
  listCurveDefinitions,
  listDeskDeterminations,
  listDeskPublications,
  type DeskCurveDefinition,
  type DeskDetermination,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, relTime } from '@/lib/format';
import {
  CurvesQaBadge,
  DeterminationStatusPill,
  PublicationResults,
  QaBadge,
} from '@/components/desk';
import {
  Chip,
  DataTable,
  EmptyState,
  ErrorPanel,
  KpiStat,
  PageHeader,
  SectionCard,
  SkeletonRows,
  type Column,
  type KpiStatus,
} from '@/components/ui';

function ActionLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 text-caption font-medium text-action hover:underline"
    >
      {children}
      <ArrowRight size={13} />
    </Link>
  );
}

const FANOUT_STATUS: Record<string, KpiStatus> = {
  complete: 'ok',
  partial: 'warn',
  failed: 'crit',
};

/**
 * One operational home for the rates-first desk: KPI pulse, the live
 * determinations preview, the approved curve family, and the latest fan-out
 * evidence. The linked detail routes own their respective mutations and
 * four-eyes gates.
 */
export default function MarketWorkspacePage() {
  const router = useRouter();
  const determinations = useApi(() => listDeskDeterminations());
  const definitions = useApi(() => listCurveDefinitions());
  const publications = useApi(() => listDeskPublications());

  const rows = determinations.data?.determinations ?? [];
  const activeCurves = (definitions.data?.definitions ?? []).filter(
    (definition) => definition.status === 'approved',
  );
  const pendingReview = rows.filter((row) => row.status === 'pending_review');
  const published = rows.filter((row) => row.status === 'published');
  const latestPublication = publications.data?.publications[0] ?? null;

  const detColumns: Column<DeskDetermination>[] = [
    {
      key: 'cob',
      header: 'COB',
      sortable: true,
      sortAccessor: (r) => r.cob_date,
      render: (r) => <span className="font-mono text-caption text-navy">{fmtDate(r.cob_date)}</span>,
    },
    { key: 'status', header: 'Status', render: (r) => <DeterminationStatusPill status={r.status} /> },
    { key: 'qa', header: 'Rates QA', render: (r) => <QaBadge determination={r} /> },
    { key: 'curves', header: 'Curves', render: (r) => <CurvesQaBadge determination={r} /> },
  ];

  const curveColumns: Column<DeskCurveDefinition>[] = [
    {
      key: 'code',
      header: 'Curve',
      sortable: true,
      sortAccessor: (r) => r.curve_code,
      render: (r) => (
        <span className="inline-flex items-center gap-2">
          <span className="font-mono text-caption text-navy">{r.curve_code}</span>
          <Chip tone="ok">v{r.version}</Chip>
        </span>
      ),
    },
    { key: 'ccy', header: 'Ccy', render: (r) => <span className="text-caption text-slate">{r.currency}</span> },
    {
      key: 'interp',
      header: 'Interpolation',
      render: (r) => <span className="text-caption text-slate">{r.interpolation_method}</span>,
    },
    {
      key: 'freq',
      header: 'Primary',
      render: (r) => <span className="text-caption text-slate">{r.curve_frequency}</span>,
    },
    { key: 'tier', header: 'Tier', render: (r) => <Chip>{r.entitlement_tier}</Chip> },
  ];

  return (
    <div>
      <PageHeader
        title="Market workspace"
        sub="Desk control room for rate packages, published curve tenors, QA review, and bank publication fan-out."
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiStat
          label="Pending review"
          value={pendingReview.length}
          status={pendingReview.length > 0 ? 'warn' : 'ok'}
          hint="Awaiting checker approval"
        />
        <KpiStat
          label="Approved curves"
          value={activeCurves.length}
          hint="Governed definitions live"
        />
        <KpiStat
          label="Published packages"
          value={published.length}
          hint="Visible to banks"
        />
        <KpiStat
          label="Latest fan-out"
          value={latestPublication ? latestPublication.status : '—'}
          status={latestPublication ? FANOUT_STATUS[latestPublication.status] : undefined}
          hint={latestPublication ? relTime(latestPublication.published_at) : 'No publication recorded'}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(24rem,0.85fr)]">
        <SectionCard
          title="Current determinations"
          subtitle="Rates, curves, and the persisted 1D / 1M / 3M / 6M / 1Y pull grids are generated together."
          actions={<ActionLink href="/desk/determinations">View all</ActionLink>}
          noPadding
        >
          {determinations.loading && <SkeletonRows rows={5} />}
          {determinations.error && (
            <div className="p-4">
              <ErrorPanel
                error={determinations.error}
                onRetry={determinations.reload}
                context="Loading determinations"
              />
            </div>
          )}
          {determinations.data && rows.length === 0 && (
            <EmptyState
              title="No rate packages"
              hint="Open a determination for the current COB to begin the desk workflow."
            />
          )}
          {rows.length > 0 && (
            <DataTable
              columns={detColumns}
              rows={rows}
              density="compact"
              pageSize={6}
              onRowClick={(r) => router.push(`/desk/determinations/${r.id}`)}
            />
          )}
        </SectionCard>

        <SectionCard
          title="Published tenor families"
          subtitle="A defined curve publishes a dense tenor surface plus pull grids at all supported frequencies."
          actions={<ActionLink href="/desk/curves">Manage curves</ActionLink>}
          noPadding
        >
          {definitions.loading && <SkeletonRows rows={4} />}
          {definitions.error && (
            <div className="p-4">
              <ErrorPanel
                error={definitions.error}
                onRetry={definitions.reload}
                context="Loading curve definitions"
              />
            </div>
          )}
          {definitions.data && activeCurves.length === 0 && (
            <EmptyState
              title="No approved curve definitions"
              hint="A second operator must approve a definition before it can generate a curve."
            />
          )}
          {activeCurves.length > 0 && (
            <DataTable
              columns={curveColumns}
              rows={activeCurves}
              density="compact"
              onRowClick={(r) => router.push(`/desk/curves/${encodeURIComponent(r.curve_code)}`)}
            />
          )}
        </SectionCard>
      </div>

      {latestPublication && (
        <SectionCard
          title="Latest publication evidence"
          subtitle="Most recent fan-out into every bank's canonical store."
          actions={<ActionLink href="/desk/publications">Inspect all</ActionLink>}
          className="mt-5"
        >
          <PublicationResults publication={latestPublication} />
        </SectionCard>
      )}
    </div>
  );
}
