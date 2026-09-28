"use client";

/**
 * Arranging a dashboard's definition.
 *
 * THREE RULES DECIDE WHETHER THIS ROUTE RESOLVES, and each of them refuses a
 * different thing.
 *
 * ONLY THE OWNER EDITS. A saved dashboard belongs to the person who saved it;
 * nobody else may change or delete it, however widely it is shared. Sharing a view
 * is not sharing control of it. The server decides that — reachability first, so a
 * document this identity may not open is not found rather than forbidden, then
 * ownership — and this page renders the refusal rather than an editor with nowhere
 * to save to.
 *
 * A CERTIFIED PACK IS NEVER EDITED IN PLACE. It is certified content with a
 * version behind it, so an editable version of one is a COPY that becomes the
 * copier's own personal dashboard; the copy is made from the file the platform
 * holds, on the pack's own page. A pack key reaching this route is not found,
 * because there is nothing here that could honestly be done with it.
 *
 * A CANVAS THIS CLIENT CANNOT RESTATE IS NOT REARRANGED. Saving replaces the
 * canvas as a whole, so every view's authored definition has to be written down —
 * and two of them cannot be recovered from what the read route serves: a view this
 * reader is refused (which arrives carrying only its geometry) and a view that
 * reads a relative period (which arrives with that period already resolved into
 * dates, ambiguously). `components/bi/builder.ts::draftFromDashboard` refuses in
 * both cases and the reason is shown; nothing is changed and nothing is dropped.
 */

import { use, useMemo } from "react";
import Link from "next/link";
import { notFound, useRouter } from "next/navigation";
import { LayoutGrid } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import DashboardBuilder from "@/components/bi/DashboardBuilder";
import {
  draftFromDashboard,
  draftSpec,
  isSavedDashboardId,
  unrestatableReason,
} from "@/components/bi/builder";
import type { ExploreCatalogue } from "@/components/bi/exploreQuery";
import {
  isBiUnavailable,
  useBiCatalogue,
  useBiSavedDashboard,
  useUpdateBiSavedDashboard,
} from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";

export default function EditDashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { bank, period } = useBankContext();
  const asOf = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";

  // A certified pack key has nothing to edit here. Decided before any read, so
  // the refusal costs no request: whether a view may be edited is a property of
  // its standing, not of its contents.
  const editable = isSavedDashboardId(id);

  const catalogueQuery = useBiCatalogue(bank?.id, editable);
  const saved = useBiSavedDashboard(bank?.id, editable ? id : null, asOf);
  const update = useUpdateBiSavedDashboard(bank?.id);

  const catalogue: ExploreCatalogue = useMemo(
    () => ({
      measures: catalogueQuery.data?.measures ?? [],
      dimensions: catalogueQuery.data?.dimensions ?? [],
    }),
    [catalogueQuery.data],
  );

  const restated = useMemo(
    () => (saved.data ? draftFromDashboard(saved.data) : null),
    [saved.data],
  );

  if (!editable) notFound();
  if (isBiUnavailable(saved.error)) notFound();

  const header = (
    <PageHeader
      breadcrumbs={[
        { label: "Dashboards", href: "/dashboards" },
        { label: saved.data?.title ?? "Dashboard", href: `/dashboards/${id}` },
      ]}
      title="Arrange this dashboard"
      subtitle="Move and resize the views, change what they measure, and save. Every save is kept in the history."
    />
  );

  // The server answers 403 for a document this identity can open but does not
  // own. It is a real answer, not a failure, so it is said plainly.
  const notTheOwner = saved.data?.ownedByCaller === false;

  return (
    <>
      {header}
      <PageContainer className="space-y-4 py-6">
        {(saved.isPending || catalogueQuery.isPending) && (
          <div className="card space-y-2 p-5" aria-busy="true">
            <SkeletonLine width="55%" />
            <SkeletonLine width="80%" />
          </div>
        )}

        {saved.error && !isBiUnavailable(saved.error) && (
          <ErrorPanel
            error={saved.error}
            onRetry={() => void saved.refetch()}
            title="Could not load this dashboard"
          />
        )}

        {notTheOwner && (
          <EmptyState
            Icon={LayoutGrid}
            title="Only the person who saved this dashboard can change it"
            description="Sharing a view is not sharing control of it. You can open it, read the views your access covers, and make a copy of your own from Explore."
            action={
              <Link
                href={`/dashboards/${id}`}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-action hover:bg-surface"
              >
                Back to the dashboard
              </Link>
            }
          />
        )}

        {saved.data?.ownedByCaller === true &&
          restated !== null &&
          "unrestatable" in restated && (
            <EmptyState
              Icon={LayoutGrid}
              title="This dashboard cannot be rearranged here"
              description={unrestatableReason(restated.unrestatable)}
              action={
                <Link
                  href={`/dashboards/${id}`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-action hover:bg-surface"
                >
                  Back to the dashboard
                </Link>
              }
            />
          )}

        {saved.data?.ownedByCaller === true &&
          catalogueQuery.data &&
          restated !== null &&
          "draft" in restated && (
            <DashboardBuilder
              key={`${id}:${saved.data.version}`}
              catalogue={catalogue}
              initialDraft={restated.draft}
              mode="edit"
              saving={update.isPending}
              error={update.error}
              cancelHref={`/dashboards/${id}`}
              onSave={(draft, changeNote) =>
                update.mutate(
                  {
                    dashboardId: id,
                    body: {
                      title: draft.title.trim(),
                      description: draft.description.trim(),
                      visibility: draft.visibility,
                      visibilityRole: draft.visibilityRole,
                      spec: draftSpec(draft),
                      changeNote,
                    },
                  },
                  { onSuccess: () => router.push(`/dashboards/${id}`) },
                )
              }
            />
          )}
      </PageContainer>
    </>
  );
}
