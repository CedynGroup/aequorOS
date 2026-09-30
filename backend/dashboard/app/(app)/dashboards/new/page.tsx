"use client";

/**
 * Saving a dashboard of your own.
 *
 * WHAT IS BEING BUILT IS A SET OF QUESTIONS. Every figure offered comes from
 * `GET …/bi/catalogue`, which is filtered member by member through the same
 * decision the query path makes — so nothing on this form is something this
 * reader's access does not already cover. The server authorizes the whole canvas
 * again when it is saved: a person cannot keep a question they may not ask, which
 * is what keeps a refusal marker a property of SHARING rather than something an
 * author can produce for themselves.
 *
 * THE FIRST SAVE MAKES THE READER ITS OWNER, and ownership is the whole of the
 * rule: nobody else may change or delete it, however widely it is shared. Not an
 * account administrator and not an organization owner.
 *
 * There is no draft on the server and nothing is written until Save is pressed, so
 * leaving this page discards the arrangement and nothing else.
 */

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { LayoutGrid } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import DashboardBuilder from "@/components/bi/DashboardBuilder";
import { draftSpec, emptyDraft } from "@/components/bi/builder";
import { grantSentence } from "@/components/bi/labels";
import type { ExploreCatalogue } from "@/components/bi/exploreQuery";
import {
  isBiUnavailable,
  useBiCatalogue,
  useCreateBiSavedDashboard,
} from "@/lib/api/bi";

export default function NewDashboardPage() {
  const router = useRouter();
  const { bank } = useBankContext();
  const catalogueQuery = useBiCatalogue(bank?.id);
  const create = useCreateBiSavedDashboard(bank?.id);

  const catalogue: ExploreCatalogue = useMemo(
    () => ({
      measures: catalogueQuery.data?.measures ?? [],
      dimensions: catalogueQuery.data?.dimensions ?? [],
    }),
    [catalogueQuery.data],
  );

  const header = (
    <PageHeader
      breadcrumbs={[{ label: "Dashboards", href: "/dashboards" }]}
      title="New dashboard"
      subtitle="Put the views you want beside each other, and keep them. Everything offered is something your access already covers."
    />
  );

  if (isBiUnavailable(catalogueQuery.error)) {
    return (
      <>
        {header}
        <PageContainer className="py-6">
          <EmptyState
            Icon={LayoutGrid}
            title="Business intelligence is not available here"
            description="This institution does not serve the analytics workspace. If you expected it, ask your organization owner to check with support."
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      {header}
      <PageContainer className="py-6">
        {catalogueQuery.isPending && (
          <div className="card space-y-2 p-5" aria-busy="true">
            <SkeletonLine width="60%" />
            <SkeletonLine width="45%" />
          </div>
        )}

        {catalogueQuery.error && !isBiUnavailable(catalogueQuery.error) && (
          <ErrorPanel
            error={catalogueQuery.error}
            onRetry={() => void catalogueQuery.refetch()}
            title="Could not load the analytics catalogue"
          />
        )}

        {catalogueQuery.data && catalogue.measures.length === 0 && (
          <EmptyState
            Icon={LayoutGrid}
            title="Nothing is available to put on a dashboard yet"
            description={`Your access does not cover any analytics figure for this institution. An organization owner can grant one — for example ${grantSentence(
              "credit",
              "aggregated",
            )}.`}
          />
        )}

        {catalogueQuery.data && catalogue.measures.length > 0 && (
          <DashboardBuilder
            catalogue={catalogue}
            initialDraft={emptyDraft()}
            mode="create"
            saving={create.isPending}
            error={create.error}
            cancelHref="/dashboards"
            onSave={(draft) =>
              create.mutate(
                {
                  title: draft.title.trim(),
                  description: draft.description.trim(),
                  visibility: draft.visibility,
                  visibilityRole: draft.visibilityRole,
                  spec: draftSpec(draft),
                },
                {
                  onSuccess: (created) =>
                    router.push(`/dashboards/${created.id}`),
                },
              )
            }
          />
        )}
      </PageContainer>
    </>
  );
}
