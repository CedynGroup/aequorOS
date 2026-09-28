"use client";

/**
 * Dashboards — the views this reader can open, whoever built them.
 *
 * TWO LISTS, TWO ROUTES, ONE PAGE. `GET …/bi/packs` serves the dashboards the
 * PLATFORM publishes for this institution's licence class; `GET …/bi/dashboards`
 * serves the ones this BANK saved for itself — the reader's own, plus whatever
 * reaches them by name, by role, or organization-wide. Neither list is a menu the
 * browser knows: both are resolved server-side, per institution and per reader.
 *
 * EVERY TILE CARRIES THE STANDING THE SERVER STATED FOR IT. A certified pack is
 * `Platform-certified` because the pack routes serve only the platform's own
 * validated files. A saved dashboard carries the `badge` on its own row, mapped by
 * `components/bi/builder.ts::savedCertification` — `Bank-certified` or `Personal`
 * — and never inferred from which list it arrived in or from who owns it.
 *
 * NEITHER LIST IS ITSELF A DISCLOSURE. It names views, not figures: the member ids
 * a view reads are only shown once they have been authorized for the reader, which
 * happens when the dashboard is opened. The per-tile badge on a certified pack is
 * the institution's own reconciliation verdict for the date; a reader who may not
 * read the checks is shown no badge rather than a grey one, because "not assessed"
 * would be a false statement about a book that was assessed.
 */

import Link from "next/link";
import { ArrowRight, LayoutGrid, Plus } from "lucide-react";
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
  useSavedDashboardList,
} from "@/components/bi/dashboards";
import { isBiUnavailable, useBiTrust } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { fmtInt } from "@/lib/format";

export default function DashboardsPage() {
  const { bank, period } = useBankContext();
  const asOf = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";

  const packs = useDashboardList(bank?.id, asOf);
  const saved = useSavedDashboardList(bank?.id);
  const trust = useBiTrust(bank?.id, asOf);
  const { dashboards } = packs;
  const mine = saved.dashboards;

  // "Nothing at all" is ONE state and is told once. It is only reached when both
  // routes have answered and both answered with nothing, and it then replaces the
  // two sections rather than sitting inside one of them — two empty statements on
  // one page read as two different problems.
  const nothingPublished = Boolean(packs.data) && dashboards.length === 0;
  const nothingSaved = Boolean(saved.data) && mine.length === 0;
  const nothingAtAll = nothingPublished && nothingSaved;

  return (
    <>
      <PageHeader
        title="Dashboards"
        subtitle="Prepared views over the measures your access covers. Each tile on a dashboard is authorized for you, not for whoever built it."
        asOf={asOf}
        action={
          <Link
            href="/dashboards/new"
            className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white hover:bg-action/90"
          >
            <Plus size={13} aria-hidden />
            New dashboard
          </Link>
        }
      />

      <PageContainer className="space-y-8 py-6">
        {nothingAtAll && (
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

        {!nothingAtAll && (
          <section className="space-y-3">
            <div>
              <h2 className="text-h3 text-navy">Saved at this institution</h2>
              <p className="text-caption leading-relaxed text-slate">
                Dashboards your colleagues saved and shared with you, and the
                ones you saved yourself. Only the person who saved one can
                change it.
              </p>
            </div>

            {saved.isPending && (
              <div className="card space-y-2 p-5" aria-busy="true">
                <SkeletonLine width="60%" />
                <SkeletonLine width="80%" />
              </div>
            )}

            {saved.error && !isBiUnavailable(saved.error) && (
              <ErrorPanel
                error={saved.error}
                onRetry={() => void saved.refetch()}
                title="Could not load the saved dashboards"
              />
            )}

            {nothingSaved && (
              <div className="card p-5">
                <p className="text-body leading-relaxed text-slate">
                  Nothing has been saved here yet, and nobody has shared one
                  with you. Start from a question in Explore, or put views on a
                  canvas of your own.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Link
                    href="/dashboards/new"
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-action hover:bg-surface"
                  >
                    <Plus size={13} aria-hidden />
                    New dashboard
                  </Link>
                  <Link
                    href="/explore"
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-action hover:bg-surface"
                  >
                    Open Explore
                    <ArrowRight size={13} aria-hidden />
                  </Link>
                </div>
              </div>
            )}

            {mine.length > 0 && (
              <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {mine.map((dashboard) => (
                  <li key={dashboard.id}>
                    <Link
                      href={`/dashboards/${dashboard.id}`}
                      className="card block h-full p-5 hover:bg-surface/60"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <h3 className="text-h3 text-navy">{dashboard.title}</h3>
                        <span className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
                          {CERTIFICATION_LABELS[dashboard.certification]}
                        </span>
                      </div>
                      {dashboard.description.length > 0 && (
                        <p className="mt-1 text-caption leading-relaxed text-slate">
                          {dashboard.description}
                        </p>
                      )}
                      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-slate">
                        <span>
                          {dashboard.widgetCount === 1
                            ? "1 view"
                            : `${fmtInt(dashboard.widgetCount)} views`}
                        </span>
                        <span>
                          {dashboard.ownedByCaller
                            ? "Saved by you"
                            : `Saved by ${dashboard.ownerDisplayName ?? "a colleague"}`}
                        </span>
                        <span>Version {fmtInt(dashboard.version)}</span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {!nothingAtAll && (
          <section className="space-y-3">
            <div>
              <h2 className="text-h3 text-navy">Certified by the platform</h2>
              <p className="text-caption leading-relaxed text-slate">
                Published per licence class and never edited in place. Make a
                copy to arrange one your own way.
              </p>
            </div>

            {packs.isPending && (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {[0, 1, 2].map((index) => (
                  <div
                    key={index}
                    className="card space-y-2 p-5"
                    aria-busy="true"
                  >
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
                          <h3 className="text-h3 text-navy">
                            {dashboard.title}
                          </h3>
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
          </section>
        )}
      </PageContainer>
    </>
  );
}
