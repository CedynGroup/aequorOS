"use client";

/**
 * One saved dashboard, read.
 *
 * SHARING NEVER SHARES DATA, AND THIS IS WHERE THAT IS VISIBLE. The document is
 * resolved for the reader who opened it, not for the person who saved it: every
 * view is authorized against the reader's own bindings, and a view they are
 * refused arrives carrying its place on the canvas and nothing else — no heading,
 * no figure, no field. It draws as "Access restricted", which is the same tile a
 * certified pack shows for the same reason and through the same component.
 *
 * ONLY THE OWNER CHANGES OR DELETES IT, and the controls for doing so are only
 * rendered for the owner — `owned_by_caller` on the server's own answer, never a
 * scalar role and never an inference. The server refuses a non-owner anyway; this
 * is so a colleague is not offered a button that answers 403.
 *
 * A DASHBOARD THIS READER MAY NOT REACH IS NOT FOUND. Reachability is decided
 * before ownership on the server, so a private document cannot be enumerated by
 * id — and this surface does not enumerate one either: it asks for exactly the id
 * in the address and renders the 404 as not-found.
 *
 * THE REPORTING DATE IS THE DATE THE DOCUMENT WAS ASKED FOR. Changing it re-reads
 * the whole document so that every view moves together, exactly as a certified
 * pack does.
 *
 * AND THE DOCUMENT SAYS HOW MUCH OF THE BOOK ITS FIGURES COVER. Every view here is
 * answered under the reader's own data scope, but the dashboard payload discloses
 * no scope, so the coverage is derived from the addresses the document's own views
 * read and the per-capability scope on `/auth/me`. It is stated at `surface`
 * precision: two views of one dashboard can be covered differently, so the
 * sentence says part of the document is narrowed rather than inventing which
 * tile — and it fails closed when a view's figures cannot be resolved at all.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { notFound, useRouter } from "next/navigation";
import { Pencil, Trash2 } from "lucide-react";
import type { BiFilter, BiQuery } from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import CoverageNotice from "@/components/access/CoverageNotice";
import { useBankContext } from "@/components/shell/BankContext";
import DashboardCanvas from "@/components/bi/DashboardCanvas";
import ExplainDrawer from "@/components/bi/ExplainDrawer";
import ExportActions from "@/components/bi/ExportActions";
import ExportMenu, { printExportOption } from "@/components/bi/ExportMenu";
import FilterBar from "@/components/bi/FilterBar";
import SharePanel from "@/components/bi/SharePanel";
import TrustBadge from "@/components/bi/TrustBadge";
import VersionHistory from "@/components/bi/VersionHistory";
import {
  CERTIFICATION_LABELS,
  shownBesideRefusalsOnSaved,
  useSavedDashboard,
} from "@/components/bi/dashboards";
import { mergeAddresses, questionAddresses } from "@/components/bi/measures";
import {
  isBiAccessDenied,
  isBiUnavailable,
  useBiCatalogue,
  useBiDashboardShares,
  useBiDashboardVersions,
  useBiTrust,
  useDeleteBiSavedDashboard,
  useSetBiDashboardShares,
} from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { coverageForFigures } from "@/lib/api/dataScope";
import { useOrganizationUsers } from "@/lib/api/hooks";
import { fmtInt } from "@/lib/format";

/**
 * A saved dashboard's own version of the "some views are outside your access"
 * note. The server writes the sentence; this adds the one thing it cannot know —
 * that tiles reading no figure of their own are still on screen underneath it.
 */
const STILL_SHOWN =
  "The views still shown read no figure of their own: they open another part of the platform, or name what is outstanding.";

export default function SavedDashboardScreen({ id }: { id: string }) {
  const router = useRouter();
  const { bank, period, institutionCapabilities, authorityPending } =
    useBankContext();

  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;
  const [filters, setFilters] = useState<BiFilter[]>([]);
  const [explaining, setExplaining] = useState<{
    measure: string;
    query: BiQuery;
  } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const catalogue = useBiCatalogue(bank?.id);
  const trust = useBiTrust(bank?.id, asOf);
  const saved = useSavedDashboard(bank?.id, id, asOf);
  const dashboard = saved.dashboard;
  const isOwner = dashboard?.ownedByCaller === true;

  const versions = useBiDashboardVersions(bank?.id, id);
  // Only the owner may read the list, and only a dashboard shared BY NAME has one:
  // the route refuses it for any other reachability rule, and a save with any other
  // rule deletes the names outright.
  const shares = useBiDashboardShares(
    bank?.id,
    id,
    isOwner && dashboard?.visibility === "users",
  );
  const setShares = useSetBiDashboardShares(bank?.id);
  const remove = useDeleteBiSavedDashboard(bank?.id);
  /**
   * How much of the institution's book this document's figures cover.
   *
   * Resolved from the views' OWN questions rather than from the reader's whole
   * catalogue, so a dashboard of only institution-wide figures says nothing even
   * for a reader who holds a branch grant elsewhere. A refused view contributes
   * nothing, which is right: it draws no figure. No calculated measure can reach
   * a dashboard widget (the composer offers only catalogue members), so an
   * unresolvable measure id here is a genuine reason to fail closed.
   */
  const coverage = useMemo(() => {
    const published = catalogue.data?.measures ?? [];
    const dimensions = catalogue.data?.dimensions ?? [];
    const parts = (dashboard?.widgets ?? [])
      .filter((widget) => widget.state === "figure")
      .map((widget) =>
        questionAddresses(
          {
            measures: widget.spec.query.measures,
            dimensions: widget.spec.query.dimensions ?? [],
            filterMembers: (widget.spec.query.filters ?? []).map(
              (filter) => filter.member,
            ),
          },
          published,
          dimensions,
          [],
        ),
      );
    return coverageForFigures(institutionCapabilities, mergeAddresses(parts), {
      pending: authorityPending || catalogue.isPending || saved.isPending,
      precision: "surface",
    });
  }, [
    authorityPending,
    catalogue.data,
    catalogue.isPending,
    dashboard?.widgets,
    institutionCapabilities,
    saved.isPending,
  ]);

  // The directory is its own Account authority, which the owner of a dashboard
  // may well not hold. It is asked for only when there is a list to add to, and
  // a refusal is a state the panel renders rather than an error.
  const directory = useOrganizationUsers(isOwner);

  // THE PERSON WHO JUST DELETED IT MUST NOT BE SHOWN A NOT-FOUND PAGE FOR THE ACT
  // THEY ASKED FOR.
  //
  // A deleted dashboard answers 404 on the next read, which is correct. But the
  // saved-dashboard reads are invalidated as part of the delete, and the refetched
  // 404 arrives BEFORE the navigation away does — so without this the owner's own
  // delete rendered "This page could not be found" at the document's own address
  // and never left it. Measured, not theoretical: the browser journey landed there
  // in three of five runs.
  //
  // The guard opens the moment the delete is SENT rather than when it succeeds,
  // because that is the window the race lives in: React Query settles the
  // mutation's own `onSuccess` — the invalidation, and therefore the refetch —
  // before it marks the mutation successful. A failed delete (a non-owner's 403)
  // falls through to the document, which is where its refusal belongs.
  if (remove.isPending || remove.isSuccess) {
    return (
      <>
        <PageHeader
          breadcrumbs={[{ label: "Dashboards", href: "/dashboards" }]}
          title={remove.isSuccess ? "Dashboard deleted" : "Deleting dashboard"}
        />
        <PageContainer className="py-6">
          <p role="status" className="text-body leading-relaxed text-slate">
            {remove.isSuccess
              ? "This dashboard, its history and everyone it reached have been removed. Taking you back to Dashboards."
              : "Removing this dashboard, its history and everyone it reached."}
          </p>
        </PageContainer>
      </>
    );
  }

  // 404 is the answer both for a document this identity may not reach and for a
  // deployment that serves no analytics. Decided only once the route has answered.
  if (isBiUnavailable(saved.error)) notFound();

  if (dashboard === null) {
    return (
      <>
        <PageHeader
          breadcrumbs={[{ label: "Dashboards", href: "/dashboards" }]}
          title="Dashboard"
          asOf={asOf}
        />
        <PageContainer className="py-6">
          {saved.error ? (
            <ErrorPanel
              error={saved.error}
              onRetry={() => void saved.refetch()}
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

  const stillShown = shownBesideRefusalsOnSaved(dashboard);

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
              Version {fmtInt(dashboard.version)}
            </span>
            {trust.data && (
              <TrustBadge
                status={trust.data.status}
                failingChecks={(trust.data.checks ?? [])
                  .filter((check) => check.status !== "green")
                  .map((check) => check.checkId)}
              />
            )}
            {isOwner && (
              <Link
                href={`/dashboards/${dashboard.id}/edit`}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-caption font-medium text-action hover:bg-surface"
              >
                <Pencil size={13} aria-hidden />
                Arrange
              </Link>
            )}
            <ExportMenu options={[printExportOption()]} />
          </div>
        }
      />

      <PageContainer className="space-y-6 py-6">
        {!isOwner && (
          <p className="text-caption leading-relaxed text-slate">
            {dashboard.ownerDisplayName
              ? `${dashboard.ownerDisplayName} saved this dashboard and is the only person who can change it.`
              : "A colleague saved this dashboard and is the only person who can change it."}{" "}
            What you see on it is authorized for you, not for them.
          </p>
        )}

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

        <CoverageNotice coverage={coverage} />

        <DashboardCanvas
          bankId={bank?.id}
          widgets={dashboard.widgets}
          filters={filters}
          onExplain={(measure, query) => setExplaining({ measure, query })}
          widgetActions={(query) => (
            <ExportActions bankId={bank?.id} query={query} />
          )}
        />

        {isOwner && (
          <SharePanel
            visibility={dashboard.visibility}
            visibilityRole={dashboard.visibilityRole}
            shares={shares.data?.shares}
            sharesLoading={shares.isPending}
            sharesError={shares.error}
            candidates={directory.data?.users.map((person) => ({
              id: person.id,
              displayName: person.displayName ?? null,
              email: person.email,
              isActive: person.isActive,
            }))}
            directoryRefused={Boolean(directory.error)}
            directoryLoading={directory.isPending}
            editHref={`/dashboards/${dashboard.id}/edit`}
            saving={setShares.isPending}
            saveError={setShares.error}
            onSave={(userIds) =>
              setShares.mutate({ dashboardId: dashboard.id, userIds })
            }
          />
        )}

        <VersionHistory
          versions={versions.data?.versions}
          isLoading={versions.isPending}
          error={versions.error}
          onRetry={() => void versions.refetch()}
        />

        {isOwner && (
          <div className="card space-y-3 p-5">
            <div>
              <h2 className="text-h3 text-navy">Delete this dashboard</h2>
              <p className="mt-1 text-caption leading-relaxed text-slate">
                It goes completely: the canvas, every version in the history,
                and everyone it is shared with. Nothing about the underlying
                figures changes, and this cannot be undone.
              </p>
            </div>
            {Boolean(remove.error) &&
              (isBiAccessDenied(remove.error) ? (
                <p role="status" className="text-caption text-critical">
                  Only the person who saved this dashboard can delete it.
                </p>
              ) : (
                <ErrorPanel
                  error={remove.error}
                  title="This dashboard was not deleted"
                />
              ))}
            {confirmingDelete ? (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={remove.isPending}
                  onClick={() =>
                    remove.mutate(dashboard.id, {
                      onSuccess: () => router.push("/dashboards"),
                    })
                  }
                  className="rounded-md bg-critical px-3 py-2 text-caption font-medium text-white hover:bg-critical/90 disabled:opacity-50"
                >
                  {remove.isPending ? "Deleting…" : "Delete it and its history"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(false)}
                  className="rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface"
                >
                  Keep it
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmingDelete(true)}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-critical hover:bg-critical-light"
              >
                <Trash2 size={13} aria-hidden />
                Delete
              </button>
            )}
          </div>
        )}
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
