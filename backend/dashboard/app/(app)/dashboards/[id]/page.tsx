"use client";

/**
 * One certified dashboard, read.
 *
 * THE DATE REACHES THE PACK BY BEING THE DATE THE PACK WAS ASKED FOR. A pack
 * carries no date of its own: each widget declares its window RELATIVE to the
 * reporting date — as of, month to date, trailing twelve months — and the server
 * resolves those into real windows when it serves the pack for the date on the
 * filter bar. Changing the date re-reads the pack, so a twelve-month trend stays
 * a twelve-month trend; rewriting each widget's window in the browser would
 * quietly turn it into a point read under the title the pack authored.
 *
 * The canvas then answers each widget separately, authorized for whoever opened
 * the page, so two readers of the same dashboard see two different sets of tiles
 * — one may see a chart where the other sees "Access restricted", and neither
 * learns anything about the other's view.
 *
 * A GOVERNED EXPORT IS AN EXPORT OF ONE ANSWER. There is no such thing as "the
 * dashboard's query", so the export control sits in each figure's own frame and
 * carries that figure's own question. The page-level menu offers print, which
 * carries the whole canvas as it stands.
 *
 * A pack id this institution is not served resolves to nothing and is not found.
 */

import { use, useState } from "react";
import { notFound } from "next/navigation";
import type { BiFilter, BiQuery } from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import DashboardCanvas from "@/components/bi/DashboardCanvas";
import ExplainDrawer from "@/components/bi/ExplainDrawer";
import ExportActions from "@/components/bi/ExportActions";
import ExportMenu, { printExportOption } from "@/components/bi/ExportMenu";
import FilterBar from "@/components/bi/FilterBar";
import TrustBadge from "@/components/bi/TrustBadge";
import {
  CERTIFICATION_LABELS,
  shownBesideRefusals,
  useDashboard,
} from "@/components/bi/dashboards";
import { isBiUnavailable, useBiCatalogue, useBiTrust } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";

/**
 * What the reader is still shown when every FIGURE on the pack was refused.
 *
 * The server's sentence is about the figures, and it is right about them. But a
 * pack also carries views that read no figure — an embedded platform surface, a
 * dataset the institution has not supplied, a measure the platform has not
 * published — and those still appear. Left unsaid, "none of them are shown" reads
 * on screen as "there is nothing here" while four tiles sit underneath it.
 */
const STILL_SHOWN =
  "The views still shown read no figure of their own: they open another part of the platform, or name what is outstanding.";

export default function DashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { bank, period } = useBankContext();

  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;
  const [filters, setFilters] = useState<BiFilter[]>([]);
  const [explaining, setExplaining] = useState<{
    measure: string;
    query: BiQuery;
  } | null>(null);

  const catalogue = useBiCatalogue(bank?.id);
  const trust = useBiTrust(bank?.id, asOf);
  const pack = useDashboard(bank?.id, id, asOf);
  const dashboard = pack.dashboard;

  // 404 is the answer for a pack id this institution is not served, and for a
  // deployment that serves no analytics at all. It is decided only once the
  // route has answered — never while the read is still in flight.
  if (isBiUnavailable(pack.error)) notFound();

  if (dashboard === null) {
    return (
      <>
        <PageHeader
          breadcrumbs={[{ label: "Dashboards", href: "/dashboards" }]}
          title="Dashboard"
          asOf={asOf}
        />
        <PageContainer className="py-6">
          {pack.error ? (
            <ErrorPanel
              error={pack.error}
              onRetry={() => void pack.refetch()}
              title="Could not load this dashboard"
            />
          ) : (
            <div className="card space-y-2 p-5" aria-busy="true">
              <SkeletonLine width="55%" />
              <SkeletonLine width="80%" />
              <SkeletonLine width="35%" />
            </div>
          )}
        </PageContainer>
      </>
    );
  }

  const stillShown = shownBesideRefusals(dashboard);

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Dashboards", href: "/dashboards" }]}
        title={dashboard.title}
        subtitle={dashboard.description}
        asOf={asOf}
        action={
          <div className="flex items-center gap-2">
            <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
              {CERTIFICATION_LABELS[dashboard.certification]}
            </span>
            <span className="text-micro text-slate">
              Version {dashboard.version}
            </span>
            {trust.data && (
              <TrustBadge
                status={trust.data.status}
                failingChecks={(trust.data.checks ?? [])
                  .filter((check) => check.status !== "green")
                  .map((check) => check.checkId)}
              />
            )}
            <ExportMenu options={[printExportOption()]} />
          </div>
        }
      />

      <PageContainer className="space-y-6 py-6">
        <FilterBar
          catalogue={catalogue.data}
          asOf={asOf}
          onAsOfChange={setChosenDate}
          filters={filters}
          onFiltersChange={setFilters}
        />

        {dashboard.restrictedWidgets > 0 && (
          <div
            role="status"
            className="rounded-md border border-border bg-surface px-4 py-3"
          >
            <p className="text-caption leading-relaxed text-navy">
              {dashboard.message}
            </p>
            {dashboard.everyFigureRefused && stillShown > 0 && (
              <p className="mt-1 text-caption leading-relaxed text-slate">
                {STILL_SHOWN}
              </p>
            )}
          </div>
        )}

        <DashboardCanvas
          bankId={bank?.id}
          widgets={dashboard.widgets}
          filters={filters}
          onExplain={(measure, query) => setExplaining({ measure, query })}
          widgetActions={(query) => (
            <ExportActions bankId={bank?.id} query={query} />
          )}
        />
      </PageContainer>

      <ExplainDrawer
        bankId={bank?.id}
        measure={explaining?.measure ?? null}
        query={explaining?.query ?? null}
        onClose={() => setExplaining(null)}
      />
    </>
  );
}
