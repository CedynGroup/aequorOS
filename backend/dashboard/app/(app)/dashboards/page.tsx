"use client";

/**
 * Dashboards — the certified views this reader can open.
 *
 * The list is `GET …/bi/packs`, resolved for this institution and this reader.
 * It is not a menu of names the browser knows: a pack is authored and validated
 * server-side, and which of them an institution is served is the platform's own
 * decision about its licence class — an institution with no certified pack set is
 * answered 404 and told so, rather than shown seven doors that refuse.
 *
 * Sharing a dashboard never shares data — every tile on one is authorized for
 * whoever opened it — so this list is not itself a disclosure: it names views,
 * not figures. The per-tile badge is the institution's own reconciliation verdict
 * for the date, the same verdict every figure on every tile will carry; a reader
 * who may not read the checks is shown no badge rather than a grey one, because
 * "not assessed" would be a false statement about a book that was assessed.
 */

import Link from "next/link";
import { ArrowRight, LayoutGrid } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import TrustBadge from "@/components/bi/TrustBadge";
import {
  CERTIFICATION_LABELS,
  shownBesideRefusals,
  useDashboardList,
} from "@/components/bi/dashboards";
import { isBiUnavailable, useBiTrust } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { fmtInt } from "@/lib/format";

export default function DashboardsPage() {
  const { bank, period } = useBankContext();
  const asOf = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";

  const packs = useDashboardList(bank?.id, asOf);
  const trust = useBiTrust(bank?.id, asOf);
  const { dashboards } = packs;

  return (
    <>
      <PageHeader
        title="Dashboards"
        subtitle="Prepared views over the measures your access covers. Each tile on a dashboard is authorized for you, not for whoever built it."
        asOf={asOf}
      />

      <PageContainer className="py-6">
        {packs.isPending && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((index) => (
              <div key={index} className="card space-y-2 p-5" aria-busy="true">
                <SkeletonLine width="70%" />
                <SkeletonLine width="90%" />
                <SkeletonLine width="40%" height={8} />
              </div>
            ))}
          </div>
        )}

        {isBiUnavailable(packs.error) && (
          <EmptyState
            Icon={LayoutGrid}
            title="No dashboards are published for this institution"
            description="The platform publishes certified dashboards per licence class, and none is published for this one yet. Explore is where a view of your own starts: choose what to measure, break it down, then keep it."
            action={
              <Link
                href="/explore"
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-action hover:bg-surface"
              >
                Open Explore
                <ArrowRight size={13} aria-hidden />
              </Link>
            }
          />
        )}

        {packs.error && !isBiUnavailable(packs.error) && (
          <ErrorPanel
            error={packs.error}
            onRetry={() => void packs.refetch()}
            title="Could not load the published dashboards"
          />
        )}

        {packs.data && dashboards.length === 0 && (
          <EmptyState
            Icon={LayoutGrid}
            title="No dashboards yet"
            description="Nothing has been published to this institution and you have not saved a view of your own. Explore is where a view starts: choose what to measure, break it down, then keep it."
            action={
              <Link
                href="/explore"
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-action hover:bg-surface"
              >
                Open Explore
                <ArrowRight size={13} aria-hidden />
              </Link>
            }
          />
        )}

        {dashboards.length > 0 && (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {dashboards.map((dashboard) => {
              const shown = shownBesideRefusals(dashboard);
              return (
                <li key={dashboard.id}>
                  <Link
                    href={`/dashboards/${dashboard.id}`}
                    className="card block h-full p-5 hover:bg-surface/60"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <h2 className="text-h3 text-navy">{dashboard.title}</h2>
                      <span className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
                        {CERTIFICATION_LABELS[dashboard.certification]}
                      </span>
                    </div>
                    <p className="mt-1 text-caption leading-relaxed text-slate">
                      {dashboard.description}
                    </p>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {trust.data && (
                        <TrustBadge
                          status={trust.data.status}
                          failingChecks={(trust.data.checks ?? [])
                            .filter((check) => check.status !== "green")
                            .map((check) => check.checkId)}
                          size="compact"
                        />
                      )}
                      <span className="text-micro text-slate">
                        {fmtInt(shown)} {shown === 1 ? "view" : "views"}
                      </span>
                      {dashboard.restrictedWidgets > 0 && (
                        <span className="text-micro text-slate">
                          {fmtInt(dashboard.restrictedWidgets)}{" "}
                          {dashboard.restrictedWidgets === 1
                            ? "view is"
                            : "views are"}{" "}
                          outside your access
                        </span>
                      )}
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </PageContainer>
    </>
  );
}
