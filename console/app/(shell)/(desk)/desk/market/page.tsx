'use client';

import Link from 'next/link';
import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  FileOutput,
  GitBranch,
  LineChart,
  Send,
  TimerReset,
} from 'lucide-react';
import {
  listCurveDefinitions,
  listDeskDeterminations,
  listDeskPublications,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, relTime } from '@/lib/format';
import {
  CurvesQaBadge,
  DeterminationStatusPill,
  MethodologyChip,
  PublicationResults,
  QaBadge,
} from '@/components/desk';
import { Chip, EmptyState, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

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

/**
 * One operational home for the rates-first desk: published tenors, live
 * determinations, curve construction, FX-forward preview, and fan-out status.
 * The linked detail routes own their respective mutations and four-eyes gates.
 */
export default function MarketWorkspacePage() {
  const determinations = useApi(() => listDeskDeterminations());
  const definitions = useApi(() => listCurveDefinitions());
  const publications = useApi(() => listDeskPublications());

  const rows = determinations.data?.determinations ?? [];
  const activeCurves = (definitions.data?.definitions ?? []).filter(
    (definition) => definition.status === 'approved'
  );
  const pendingReview = rows.filter((row) => row.status === 'pending_review');
  const published = rows.filter((row) => row.status === 'published');
  const latestPublication = publications.data?.publications[0] ?? null;

  return (
    <div>
      <PageHeader
        title="Market workspace"
        sub="Desk control room for rate packages, published curve tenors, FX forward construction, QA review, and bank publication fan-out."
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="card p-4">
          <div className="flex items-center justify-between text-slate"><span className="text-micro uppercase tracking-wide">Pending review</span><TimerReset size={15} /></div>
          <div className="mt-2 font-mono text-h1 text-navy">{pendingReview.length}</div>
          <p className="mt-1 text-caption text-slate">Packages awaiting independent checker approval</p>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between text-slate"><span className="text-micro uppercase tracking-wide">Approved curves</span><GitBranch size={15} /></div>
          <div className="mt-2 font-mono text-h1 text-navy">{activeCurves.length}</div>
          <p className="mt-1 text-caption text-slate">Governed definitions available for construction</p>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between text-slate"><span className="text-micro uppercase tracking-wide">Published packages</span><CheckCircle2 size={15} /></div>
          <div className="mt-2 font-mono text-h1 text-navy">{published.length}</div>
          <p className="mt-1 text-caption text-slate">Historical packages now visible to banks</p>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between text-slate"><span className="text-micro uppercase tracking-wide">Latest fan-out</span><Send size={15} /></div>
          <div className="mt-2 text-body font-medium text-navy">{latestPublication ? latestPublication.status : '—'}</div>
          <p className="mt-1 text-caption text-slate">{latestPublication ? relTime(latestPublication.published_at) : 'No publication recorded'}</p>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(24rem,0.85fr)]">
        <section className="card overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-light px-5 py-4">
            <div>
              <p className="text-micro uppercase tracking-wide text-slate">Rate publication pipeline</p>
              <h2 className="mt-1 text-h2 text-navy">Current determinations</h2>
              <p className="mt-1 text-caption text-slate">Rates, curves, and the persisted 1D / 1M / 3M / 6M / 1Y pull grids are generated together.</p>
            </div>
            <ActionLink href="/desk/determinations">Open queue</ActionLink>
          </div>
          {determinations.loading && <SkeletonRows rows={5} />}
          {determinations.error && <div className="p-4"><ErrorPanel error={determinations.error} onRetry={determinations.reload} context="Loading determinations" /></div>}
          {determinations.data && rows.length === 0 && <EmptyState title="No rate packages" hint="Open a determination for the current COB to begin the desk workflow." />}
          {rows.length > 0 && (
            <table className="w-full text-body">
              <thead className="bg-surface text-micro uppercase tracking-wide text-slate">
                <tr>
                  <th className="px-5 py-2.5 text-left font-medium">COB</th>
                  <th className="px-3 py-2.5 text-left font-medium">Status</th>
                  <th className="px-3 py-2.5 text-left font-medium">QA</th>
                  <th className="px-3 py-2.5 text-left font-medium">Curves</th>
                  <th className="px-5 py-2.5 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 6).map((row) => (
                  <tr key={row.id} className="border-t border-border-light hover:bg-surface">
                    <td className="px-5 py-3 font-mono text-caption text-navy">{fmtDate(row.cob_date)}</td>
                    <td className="px-3 py-3"><DeterminationStatusPill status={row.status} /></td>
                    <td className="px-3 py-3"><QaBadge determination={row} /></td>
                    <td className="px-3 py-3"><CurvesQaBadge determination={row} /></td>
                    <td className="px-5 py-3 text-right"><ActionLink href={`/desk/determinations/${row.id}`}>Review</ActionLink></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-light px-5 py-4">
            <div>
              <p className="text-micro uppercase tracking-wide text-slate">Curve definitions</p>
              <h2 className="mt-1 text-h2 text-navy">Published tenor families</h2>
              <p className="mt-1 text-caption text-slate">A defined curve publishes a dense tenor surface plus pull grids at all supported frequencies.</p>
            </div>
            <ActionLink href="/desk/curves">Manage curves</ActionLink>
          </div>
          {definitions.loading && <SkeletonRows rows={4} />}
          {definitions.error && <div className="p-4"><ErrorPanel error={definitions.error} onRetry={definitions.reload} context="Loading curve definitions" /></div>}
          {definitions.data && activeCurves.length === 0 && <EmptyState title="No approved curve definitions" hint="A second operator must approve a definition before it can generate a curve." />}
          {activeCurves.length > 0 && (
            <div className="divide-y divide-border-light">
              {activeCurves.map((curve) => (
                <div key={curve.id} className="flex items-center justify-between gap-4 px-5 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2"><span className="font-mono text-body text-navy">{curve.curve_code}</span><Chip tone="ok">v{curve.version}</Chip></div>
                    <p className="mt-1 text-caption text-slate">{curve.currency} · {curve.interpolation_method} · {curve.curve_frequency} primary · {curve.entitlement_tier}</p>
                  </div>
                  <ActionLink href={`/desk/curves/${encodeURIComponent(curve.curve_code)}`}>Construct</ActionLink>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-3">
        <section className="card p-5">
          <LineChart size={18} className="text-action" />
          <h2 className="mt-3 text-h3 text-navy">Rates and curve forecast</h2>
          <p className="mt-1 text-caption text-slate">Constructed tenors, QA, review, and publish sit in one evidence package. Banks only receive approved fan-outs.</p>
          <div className="mt-4"><ActionLink href="/desk/determinations">Open rates package</ActionLink></div>
        </section>
        <section className="card p-5">
          <BarChart3 size={18} className="text-action" />
          <h2 className="mt-3 text-h3 text-navy">FX forward preview</h2>
          <p className="mt-1 text-caption text-slate">Build covered-interest-parity forward outrights from published base and quote curves. The preview records an operator audit event but does not publish automatically.</p>
          <div className="mt-4"><ActionLink href="/desk/fx-forwards">Construct FX forwards</ActionLink></div>
        </section>
        <section className="card p-5">
          <FileOutput size={18} className="text-action" />
          <h2 className="mt-3 text-h3 text-navy">Bank publication</h2>
          <p className="mt-1 text-caption text-slate">Approved determinations fan out through the desk-as-vendor adapter, recording each institution's ingestion result.</p>
          <div className="mt-4"><ActionLink href="/desk/publications">Inspect fan-out</ActionLink></div>
        </section>
      </div>

      {latestPublication && (
        <section className="card mt-5 p-5">
          <h2 className="text-h3 text-navy">Latest publication evidence</h2>
          <div className="mt-3"><PublicationResults publication={latestPublication} /></div>
        </section>
      )}
    </div>
  );
}
