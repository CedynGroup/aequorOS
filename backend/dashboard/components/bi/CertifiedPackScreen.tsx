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
 * A CERTIFIED PACK IS NEVER EDITED IN PLACE, SO THE AFFORDANCE IS A COPY. It is
 * certified content with a version behind it; letting a reader rearrange the
 * original would leave two different things wearing one badge. The copy is made
 * SERVER-SIDE from the file the platform holds — this client never sends a canvas
 * — and it becomes a Personal dashboard the copier owns.
 *
 * A pack id this institution is not served resolves to nothing and is not found.
 *
 * AND IT SAYS HOW MUCH OF THE BOOK ITS FIGURES COVER. A pack is authored by the
 * platform, but it is ANSWERED under the reader's own data scope, and the pack
 * payload discloses no scope — so the coverage is derived from the addresses the
 * pack's own views read and the per-capability scope on `/auth/me`, and fails
 * closed rather than reading as the institution's whole book.
 */

import { useMemo, useState } from "react";
import { notFound, useRouter } from "next/navigation";
import { Copy } from "lucide-react";
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
import TrustBadge from "@/components/bi/TrustBadge";
import {
  CERTIFICATION_LABELS,
  shownBesideRefusals,
  useDashboard,
} from "@/components/bi/dashboards";
import { DASHBOARD_TITLE_MAX } from "@/components/bi/builder";
import { mergeAddresses, questionAddresses } from "@/components/bi/measures";
import {
  biRefusalSentence,
  isBiAccessDenied,
  isBiUnavailable,
  useBiCatalogue,
  useBiTrust,
  useCreateBiSavedDashboard,
} from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { coverageForFigures } from "@/lib/api/dataScope";

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

/**
 * A copy is refused when the copier does not hold every figure on the pack.
 *
 * Deliberate on the server's part: a canvas may only be saved by somebody who may
 * ask every question on it, which is what keeps a refusal marker a property of
 * SHARING rather than something an author can produce for themselves. The refusal
 * names no figure here, in the same words a refused view uses.
 */
const COPY_REFUSED =
  "Your access does not cover everything on this dashboard, so a copy cannot be made. An organization owner can grant it.";

export default function CertifiedPackScreen({ id }: { id: string }) {
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

  const catalogue = useBiCatalogue(bank?.id);
  const trust = useBiTrust(bank?.id, asOf);
  const pack = useDashboard(bank?.id, id, asOf);
  const copy = useCreateBiSavedDashboard(bank?.id);
  const dashboard = pack.dashboard;

  /**
   * How much of the institution's book this pack's figures cover. Same derivation
   * and same `surface` precision as a saved dashboard — a pack is authored by the
   * platform but ANSWERED under the reader's own scope, so a branch-scoped reader
   * of a board pack must not read its tiles as the institution's own figures.
   */
  const coverage = useMemo(() => {
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
          catalogue.data?.measures ?? [],
          catalogue.data?.dimensions ?? [],
          [],
        ),
      );
    return coverageForFigures(institutionCapabilities, mergeAddresses(parts), {
      pending: authorityPending || catalogue.isPending || pack.isPending,
      precision: "surface",
    });
  }, [
    authorityPending,
    catalogue.data,
    catalogue.isPending,
    dashboard?.widgets,
    institutionCapabilities,
    pack.isPending,
  ]);

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
            <button
              type="button"
              disabled={copy.isPending}
              onClick={() =>
                copy.mutate(
                  {
                    title: `Copy of ${dashboard.title}`.slice(
                      0,
                      DASHBOARD_TITLE_MAX,
                    ),
                    description: dashboard.description,
                    fromPack: dashboard.id,
                    visibility: "private",
                  },
                  {
                    onSuccess: (created) =>
                      router.push(`/dashboards/${created.id}`),
                  },
                )
              }
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-caption font-medium text-action hover:bg-surface disabled:opacity-50"
            >
              <Copy size={13} aria-hidden />
              {copy.isPending ? "Making a copy…" : "Make my own copy"}
            </button>
            <ExportMenu options={[printExportOption()]} />
          </div>
        }
      />

      <PageContainer className="space-y-6 py-6">
        {Boolean(copy.error) &&
          (isBiAccessDenied(copy.error) ? (
            <p
              role="status"
              className="rounded-md border border-border bg-surface px-4 py-3 text-caption leading-relaxed text-navy"
            >
              {COPY_REFUSED}
            </p>
          ) : biRefusalSentence(copy.error) !== null ? (
            // Any other refusal — a read-only staff session, for one — is the
            // server's own decision and is shown in the server's own words.
            <p
              role="status"
              className="rounded-md border border-border bg-surface px-4 py-3 text-caption leading-relaxed text-navy"
            >
              {biRefusalSentence(copy.error)}
            </p>
          ) : (
            <ErrorPanel error={copy.error} title="No copy was made" />
          ))}

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
