"use client";

/**
 * Explore — ask the catalogue a question of your own.
 *
 * The whole surface is built on the catalogue the server returned for THIS
 * reader. It is filtered member by member through the same decision the query
 * path makes, so every measure and field offered here is one the query route
 * will serve, and a member the reader has no sentence for is not on the page at
 * all — it is counted, not named.
 *
 * The question is a `BiQuery`: catalogue ids and typed values, never a column,
 * a table or a fragment of SQL. The server compiles it into one read-only
 * statement, authorizes every member it touches — a filter is a read, so the
 * narrowing is authorized too — and refuses the whole query if any one of them
 * is denied. There is no partial answer and no silent column drop.
 *
 * WHAT IS NOT HERE YET. The self-service grid — row groups, pivot and paging
 * over `…/bi/grid`'s server-side row model — is the next release of this page.
 * Until it lands the answer is shown as a chart or a plain table under the
 * interactive row cap, and a truncated answer says so rather than quietly
 * showing the first page as if it were the whole.
 */

import { useMemo, useState } from "react";
import { Table2 } from "lucide-react";
import type {
  BiCatalogueMeasureRead,
  BiFilter,
  BiQuery,
} from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import ExplainDrawer from "@/components/bi/ExplainDrawer";
import ExportMenu, { printExportOption } from "@/components/bi/ExportMenu";
import FilterBar from "@/components/bi/FilterBar";
import WidgetRenderer from "@/components/bi/WidgetRenderer";
import {
  designationLabel,
  grantSentence,
  moduleLabel,
} from "@/components/bi/labels";
import { biTimeFor } from "@/components/bi/query";
import type { BiWidgetKind, BiWidgetSpec } from "@/components/bi/types";
import { isBiUnavailable, useBiCatalogue, useBiQuery } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { fmtInt } from "@/lib/format";

const CHART_KINDS: readonly { kind: BiWidgetKind; label: string }[] = [
  { kind: "table", label: "Table" },
  { kind: "bar", label: "Bars" },
  { kind: "stacked_bar", label: "Stacked bars" },
  { kind: "line", label: "Line" },
  { kind: "pie", label: "Share" },
];

/**
 * The Data Engine route a reader is sent to when the answer is empty. The marts
 * are built from the canonical position book, so that is the dataset to supply.
 */
const EXPLORE_DATASET = {
  label: "Positions and balances for this date",
  href: "/data-engine/positions",
};

function toggle(values: readonly string[], id: string): string[] {
  return values.includes(id)
    ? values.filter((value) => value !== id)
    : [...values, id];
}

export default function ExplorePage() {
  const { bank, period } = useBankContext();
  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;

  const [measures, setMeasures] = useState<string[]>([]);
  const [dimensions, setDimensions] = useState<string[]>([]);
  const [filters, setFilters] = useState<BiFilter[]>([]);
  const [kind, setKind] = useState<BiWidgetKind>("table");
  const [explaining, setExplaining] = useState<string | null>(null);

  const catalogue = useBiCatalogue(bank?.id);

  const measureGroups = useMemo(() => {
    const groups = new Map<string, BiCatalogueMeasureRead[]>();
    for (const measure of catalogue.data?.measures ?? []) {
      const key = measure.module;
      groups.set(key, [...(groups.get(key) ?? []), measure]);
    }
    return [...groups.entries()].sort((left, right) =>
      moduleLabel(left[0]).localeCompare(moduleLabel(right[0])),
    );
  }, [catalogue.data]);

  /** Fields the chosen measures can actually be broken down by. */
  const availableDimensions = useMemo(() => {
    const allowed = new Set<string>();
    for (const measure of catalogue.data?.measures ?? []) {
      if (!measures.includes(measure.id)) continue;
      for (const dimension of measure.allowedDimensions) allowed.add(dimension);
    }
    return (catalogue.data?.dimensions ?? []).filter((dimension) =>
      measures.length === 0 ? false : allowed.has(dimension.id),
    );
  }, [catalogue.data, measures]);

  const query: BiQuery | null = useMemo(() => {
    if (measures.length === 0 || !asOf) return null;
    return {
      measures,
      dimensions: dimensions.filter((id) =>
        availableDimensions.some((dimension) => dimension.id === id),
      ),
      filters,
      time: biTimeFor({ asOf }),
    };
  }, [asOf, availableDimensions, dimensions, filters, measures]);

  const answer = useBiQuery(bank?.id, query);

  const spec: BiWidgetSpec | null = useMemo(() => {
    if (!query) return null;
    return {
      id: "explore",
      title: "Your question",
      subtitle: `${fmtInt(query.measures.length)} ${
        query.measures.length === 1 ? "measure" : "measures"
      } for ${asOf}`,
      kind,
      query,
      dataset: EXPLORE_DATASET,
      layout: { i: "explore", x: 0, y: 0, w: 12, h: 6 },
    };
  }, [asOf, kind, query]);

  if (isBiUnavailable(catalogue.error)) {
    return (
      <>
        <PageHeader title="Explore" />
        <PageContainer className="py-6">
          <EmptyState
            title="Business intelligence is not available here"
            description="This institution does not serve the analytics workspace. If you expected it, ask your organization owner to check with support."
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Explore"
        subtitle="Choose what to measure and how to break it down. Everything offered here is something your access already covers."
        asOf={asOf}
        action={<ExportMenu options={[printExportOption()]} />}
      />

      <PageContainer className="space-y-6 py-6">
        <FilterBar
          catalogue={catalogue.data}
          asOf={asOf}
          onAsOfChange={setChosenDate}
          filters={filters}
          onFiltersChange={setFilters}
        />

        {catalogue.isPending && (
          <div className="card space-y-2 p-5" aria-busy="true">
            <SkeletonLine width="60%" />
            <SkeletonLine width="45%" />
            <SkeletonLine width="52%" />
          </div>
        )}

        {catalogue.error && (
          <ErrorPanel
            error={catalogue.error}
            onRetry={() => void catalogue.refetch()}
            title="Could not load the analytics catalogue"
          />
        )}

        {catalogue.data && catalogue.data.measures.length === 0 && (
          <EmptyState
            title="Nothing is available to analyse yet"
            description={`Your access does not cover any analytics measure for this institution. An organization owner can grant one — for example ${grantSentence(
              "credit",
              "aggregated",
            )}.`}
          />
        )}

        {catalogue.data && catalogue.data.measures.length > 0 && (
          <div className="grid gap-4 lg:grid-cols-2">
            <SectionCard
              title="Measure"
              subtitle="What the answer counts, sums or averages."
            >
              <div className="max-h-80 space-y-4 overflow-y-auto pr-1">
                {measureGroups.map(([module, entries]) => (
                  <div key={module}>
                    <p className="mb-1 text-micro font-medium uppercase tracking-wider text-slate">
                      {moduleLabel(module)}
                    </p>
                    <ul className="space-y-0.5">
                      {entries.map((measure) => (
                        <li key={measure.id}>
                          <label className="flex items-start gap-2 rounded px-1 py-1 hover:bg-surface">
                            <input
                              type="checkbox"
                              checked={measures.includes(measure.id)}
                              onChange={() =>
                                setMeasures((current) =>
                                  toggle(current, measure.id),
                                )
                              }
                              className="mt-0.5"
                            />
                            <span className="min-w-0">
                              <span className="block text-body text-navy">
                                {measure.label}
                              </span>
                              <span className="block text-caption text-slate">
                                {measure.description}
                              </span>
                              {designationLabel(
                                measure.advisoryDesignation ?? null,
                              ) && (
                                <span className="mt-0.5 inline-block rounded border border-border bg-surface px-1.5 py-0.5 text-micro text-slate">
                                  {designationLabel(
                                    measure.advisoryDesignation ?? null,
                                  )}
                                </span>
                              )}
                            </span>
                          </label>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard
              title="Break down by"
              subtitle="Only fields the chosen measures can be grouped by are offered."
            >
              {measures.length === 0 ? (
                <p className="text-body text-slate">
                  Choose a measure first — the fields it can be grouped by
                  depend on what is being measured.
                </p>
              ) : availableDimensions.length === 0 ? (
                <p className="text-body text-slate">
                  These measures are reported for the institution as a whole and
                  cannot be broken down further.
                </p>
              ) : (
                <ul className="max-h-80 space-y-0.5 overflow-y-auto pr-1">
                  {availableDimensions.map((dimension) => (
                    <li key={dimension.id}>
                      <label className="flex items-start gap-2 rounded px-1 py-1 hover:bg-surface">
                        <input
                          type="checkbox"
                          checked={dimensions.includes(dimension.id)}
                          onChange={() =>
                            setDimensions((current) =>
                              toggle(current, dimension.id),
                            )
                          }
                          className="mt-0.5"
                        />
                        <span className="min-w-0">
                          <span className="block text-body text-navy">
                            {dimension.label}
                          </span>
                          <span className="block text-caption text-slate">
                            {dimension.description}
                          </span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </SectionCard>
          </div>
        )}

        {!spec && catalogue.data && catalogue.data.measures.length > 0 && (
          <EmptyState
            Icon={Table2}
            title="Choose a measure to see an answer"
            description="Nothing is queried until you do — this page asks the server only for the question you build."
          />
        )}

        {spec && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {CHART_KINDS.map((option) => (
                <button
                  key={option.kind}
                  type="button"
                  onClick={() => setKind(option.kind)}
                  aria-pressed={kind === option.kind}
                  className={`rounded-md border px-2.5 py-1 text-caption font-medium ${
                    kind === option.kind
                      ? "border-action/30 bg-action-light text-action"
                      : "border-border text-slate hover:bg-surface"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <WidgetRenderer
              spec={spec}
              result={answer.data}
              isLoading={answer.isPending}
              error={answer.error}
              onRetry={() => void answer.refetch()}
              onExplain={setExplaining}
              actions={<ExportMenu options={[printExportOption()]} />}
            />
          </div>
        )}
      </PageContainer>

      <ExplainDrawer
        bankId={bank?.id}
        measure={explaining}
        query={query}
        onClose={() => setExplaining(null)}
      />
    </>
  );
}
