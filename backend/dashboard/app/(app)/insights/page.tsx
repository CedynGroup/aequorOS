"use client";

/**
 * Insights — what the platform can say about one reporting date.
 *
 * Two things are on this page today and both are live reads.
 *
 * THE RECONCILIATION POSITION. `GET …/bi/trust` returns every check for the
 * selected (institution, date) with its own evidence, and a check with no
 * stored result is reported as "not assessed" — never as a pass. The whole
 * payload is figures rather than metadata, so the route authorizes it like any
 * other data read: a principal missing any one of the pairs the checks disclose
 * is refused the page rather than shown a partial verdict.
 *
 * WHAT THIS READER MAY ANALYSE. The catalogue comes back already filtered by
 * the same decision the query path makes, so nothing on it can be selected and
 * then refused. Members the reader has no sentence for are returned as a COUNT
 * and never as ids.
 *
 * The generated statements — movements, attributions, projections and their
 * drivers — are composed server-side in `app/services/bi/insights/` and have no
 * route yet. `components/bi/InsightStrip.tsx` is the surface that renders them
 * and binds here the day that route lands; nothing on this page fabricates one
 * in the meantime.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Table2 } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import FilterBar from "@/components/bi/FilterBar";
import TrustBadge from "@/components/bi/TrustBadge";
import RestrictedWidget from "@/components/bi/RestrictedWidget";
import { moduleLabel, sensitivityLabel } from "@/components/bi/labels";
import { NOT_MEASURED } from "@/components/bi/result";
import {
  isBiAccessDenied,
  isBiUnavailable,
  useBiCatalogue,
  useBiTrust,
} from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { fmtTimestamp, labelize } from "@/lib/api/values";
import { fmtInt } from "@/lib/format";

function BiNotAvailable() {
  return (
    <EmptyState
      title="Business intelligence is not available here"
      description="This institution does not serve the analytics workspace. If you expected it, ask your organization owner to check with support."
    />
  );
}

export default function InsightsPage() {
  const { bank, period } = useBankContext();
  // The default follows the institution's latest computed snapshot until the
  // reader picks a date; a state initialiser would freeze today's date before
  // the bank payload had settled.
  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;

  const catalogue = useBiCatalogue(bank?.id);
  const trust = useBiTrust(bank?.id, asOf);

  const pairs = useMemo(() => {
    const seen = new Map<string, { module: string; sensitivity: string }>();
    for (const member of [
      ...(catalogue.data?.measures ?? []),
      ...(catalogue.data?.dimensions ?? []),
    ]) {
      seen.set(`${member.module}/${member.sensitivity}`, {
        module: member.module,
        sensitivity: member.sensitivity,
      });
    }
    return [...seen.values()].sort((left, right) =>
      moduleLabel(left.module).localeCompare(moduleLabel(right.module)),
    );
  }, [catalogue.data]);

  if (isBiUnavailable(catalogue.error)) {
    return (
      <>
        <PageHeader title="Insights" />
        <PageContainer className="py-6">
          <BiNotAvailable />
        </PageContainer>
      </>
    );
  }

  const checks = trust.data?.checks ?? [];
  const builds = trust.data?.builds ?? [];

  return (
    <>
      <PageHeader
        title="Insights"
        subtitle="Whether this institution's analytics reconcile to the position the platform computed, and what you can analyse."
        asOf={asOf}
      />

      <PageContainer className="space-y-6 py-6">
        <FilterBar
          catalogue={undefined}
          asOf={asOf}
          onAsOfChange={setChosenDate}
          filters={[]}
          onFiltersChange={() => undefined}
        />

        <SectionCard
          title="Reconciliation"
          subtitle="Each check compares an analytics figure with the position the platform already computed for this date."
          actions={
            trust.data ? (
              <TrustBadge
                status={trust.data.status}
                failingChecks={checks
                  .filter((check) => check.status !== "green")
                  .map((check) => check.checkId)}
              />
            ) : undefined
          }
        >
          {trust.isPending && (
            <div className="space-y-2" aria-busy="true">
              <SkeletonLine width="70%" />
              <SkeletonLine width="55%" />
              <SkeletonLine width="60%" />
            </div>
          )}

          {isBiAccessDenied(trust.error) && <RestrictedWidget />}

          {trust.error && !isBiAccessDenied(trust.error) && (
            <ErrorPanel
              error={trust.error}
              onRetry={() => void trust.refetch()}
              title="Could not load the reconciliation position"
            />
          )}

          {trust.data && checks.length === 0 && (
            <p className="text-body leading-relaxed text-slate">
              No reconciliation check has run for this date, so these analytics
              have not been compared with the computed position. That is
              reported as not assessed — it is not a pass.
            </p>
          )}

          {checks.length > 0 && (
            <ul className="divide-y divide-border-light">
              {checks.map((check) => (
                <li
                  key={check.checkId}
                  className="flex items-start justify-between gap-4 py-2.5 first:pt-0 last:pb-0"
                >
                  <div className="min-w-0">
                    <p className="text-body text-navy">{check.label}</p>
                    <p className="mt-0.5 text-caption text-slate">
                      {check.difference
                        ? `Difference ${check.difference} against a tolerance of ${
                            check.tolerance ?? NOT_MEASURED
                          }`
                        : "No difference was measured for this check."}
                    </p>
                  </div>
                  <TrustBadge status={check.status} size="compact" />
                </li>
              ))}
            </ul>
          )}

          {builds.length > 0 && (
            <ul className="mt-4 border-t border-border-light pt-3 text-caption text-slate">
              {builds.map((build) => (
                <li key={`${build.scope}:${build.fingerprint}`}>
                  {labelize(build.scope)} — {labelize(build.status)}
                  {build.finishedAt
                    ? `, ${fmtTimestamp(new Date(build.finishedAt))}`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard
          title="What you can analyse"
          subtitle="Filtered by your own access, so nothing listed here can be selected and then refused."
          actions={
            <Link
              href="/explore"
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-caption font-medium text-action hover:bg-surface"
            >
              <Table2 size={13} aria-hidden />
              Open Explore
              <ArrowRight size={13} aria-hidden />
            </Link>
          }
        >
          {catalogue.isPending && (
            <div className="space-y-2" aria-busy="true">
              <SkeletonLine width="60%" />
              <SkeletonLine width="45%" />
            </div>
          )}

          {catalogue.error && (
            <ErrorPanel
              error={catalogue.error}
              onRetry={() => void catalogue.refetch()}
              title="Could not load the analytics catalogue"
            />
          )}

          {catalogue.data && (
            <>
              <p className="text-body text-navy">
                {fmtInt(catalogue.data.measures.length)}{" "}
                {catalogue.data.measures.length === 1 ? "measure" : "measures"}{" "}
                and {fmtInt(catalogue.data.dimensions.length)}{" "}
                {catalogue.data.dimensions.length === 1 ? "field" : "fields"}{" "}
                are available to you.
              </p>
              {pairs.length > 0 && (
                <ul className="mt-3 flex flex-wrap gap-1.5">
                  {pairs.map((pair) => (
                    <li
                      key={`${pair.module}/${pair.sensitivity}`}
                      className="rounded border border-border bg-surface px-2 py-0.5 text-caption text-slate"
                    >
                      {moduleLabel(pair.module)} ·{" "}
                      {sensitivityLabel(pair.sensitivity)}
                    </li>
                  ))}
                </ul>
              )}
              {(catalogue.data.withheldMembers ?? 0) > 0 && (
                <p className="mt-3 text-caption leading-relaxed text-slate">
                  {fmtInt(catalogue.data.withheldMembers ?? 0)} further{" "}
                  {(catalogue.data.withheldMembers ?? 0) === 1
                    ? "field exists"
                    : "fields exist"}{" "}
                  that your access does not cover. An organization owner can
                  grant them.
                </p>
              )}
            </>
          )}
        </SectionCard>
      </PageContainer>
    </>
  );
}
