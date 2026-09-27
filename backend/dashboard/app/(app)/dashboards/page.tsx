"use client";

/**
 * Dashboards — the saved and curated views this reader can open.
 *
 * There are none yet, and this page says so rather than showing a shelf of
 * tiles that resolve to nothing. Two sources fill the list and neither is on
 * the wire today: the curated content packs, authored and validated
 * server-side, and saved dashboards, which are tenant rows with an owner, a
 * visibility and a version history. `components/bi/dashboards.ts` is the seam
 * both bind to; see that file for why a pack is never copied into the browser.
 *
 * Sharing a dashboard never shares data — every widget on one is authorized for
 * whoever opened it — so the list is not itself a disclosure: it names views,
 * not figures.
 */

import Link from "next/link";
import { ArrowRight, LayoutGrid } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import TrustBadge from "@/components/bi/TrustBadge";
import {
  CERTIFICATION_LABELS,
  listDashboards,
} from "@/components/bi/dashboards";

export default function DashboardsPage() {
  const dashboards = listDashboards();

  return (
    <>
      <PageHeader
        title="Dashboards"
        subtitle="Prepared views over the measures your access covers. Each tile on a dashboard is authorized for you, not for whoever built it."
      />

      <PageContainer className="py-6">
        {dashboards.length === 0 ? (
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
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {dashboards.map((dashboard) => (
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
                  <div className="mt-3 flex items-center gap-2">
                    <TrustBadge status="grey" size="compact" />
                    <span className="text-micro text-slate">
                      {dashboard.widgets.length}{" "}
                      {dashboard.widgets.length === 1 ? "view" : "views"}
                    </span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </PageContainer>
    </>
  );
}
