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
 * TWO WAYS TO READ ONE ANSWER, both of the same question.
 *
 * * A SUMMARY: a chart or a compact table of the whole answer, under the
 *   interactive row cap. This is the view for a question with a handful of
 *   groups, and it is where the trend and share shapes live.
 * * The GRID: the same question paged through `POST …/bi/grid`, at whatever
 *   grain it returns, with server-computed subtotals, one field spread across
 *   the columns, and only the largest groups kept if that is what was asked.
 *   This is the view for a question whose answer is longer than a screen.
 *
 * The shaping controls belong to the grid because the shapes they produce are
 * table shapes — a pivoted answer is a table, not a line. Switching views
 * therefore changes the question, which is why the toggle says so.
 *
 * NOTHING IS EXPORTED FROM THE GRID ITSELF. AG Grid's own CSV export is
 * disabled in `components/bi/PivotGridCanvas.tsx`; every way out of this page
 * goes through the governed Export menu, which authorizes, audits and
 * watermarks what it releases.
 */

import { useMemo, useState } from "react";
import { Table2 } from "lucide-react";
import type { BiCatalogueMeasureRead, BiFilter } from "@aequoros/risk-service-api";
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
import GridShapeControls from "@/components/bi/GridShapeControls";
import PivotGrid from "@/components/bi/PivotGrid";
import WidgetRenderer from "@/components/bi/WidgetRenderer";
import {
  designationLabel,
  grantSentence,
  moduleLabel,
} from "@/components/bi/labels";
import { biTimeFor } from "@/components/bi/query";
import {
  BI_DIMENSION_CAP,
  buildExploreQuery,
  EMPTY_SHAPE,
  measureIsCompatible,
  sliceableDimensions,
  timeBehaviourLabel,
  type ExploreCatalogue,
  type ExploreShape,
} from "@/components/bi/exploreQuery";
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

/** Which reading of the answer is on screen. */
type AnswerView = "summary" | "grid";

const ANSWER_VIEWS: readonly { view: AnswerView; label: string }[] = [
  { view: "summary", label: "Summary" },
  { view: "grid", label: "Full grid" },
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

  const [shape, setShape] = useState<ExploreShape>(EMPTY_SHAPE);
  const [kind, setKind] = useState<BiWidgetKind>("table");
  const [view, setView] = useState<AnswerView>("summary");
  const [explaining, setExplaining] = useState<string | null>(null);

  const catalogueQuery = useBiCatalogue(bank?.id);

  const catalogue: ExploreCatalogue = useMemo(
    () => ({
      measures: catalogueQuery.data?.measures ?? [],
      dimensions: catalogueQuery.data?.dimensions ?? [],
    }),
    [catalogueQuery.data],
  );

  const measureGroups = useMemo(() => {
    const groups = new Map<string, BiCatalogueMeasureRead[]>();
    for (const measure of catalogue.measures) {
      const key = measure.module;
      groups.set(key, [...(groups.get(key) ?? []), measure]);
    }
    return [...groups.entries()].sort((left, right) =>
      moduleLabel(left[0]).localeCompare(moduleLabel(right[0])),
    );
  }, [catalogue]);

  /** Fields the chosen measures can actually be broken down by. */
  const availableDimensions = useMemo(
    () => sliceableDimensions(catalogue, shape.measures),
    [catalogue, shape.measures],
  );

  /**
   * The summary reading asks the plain question; the grid adds the shapes that
   * only a table can carry. Both go through one builder, so neither can produce
   * a combination the compiler refuses.
   */
  const plan = useMemo(() => {
    if (!asOf) return { query: null, problems: [] as const };
    const asked: ExploreShape =
      view === "grid"
        ? shape
        : { ...shape, pivot: null, topN: null, subtotals: false, sort: [] };
    return buildExploreQuery(asked, catalogue, biTimeFor({ asOf }));
  }, [asOf, catalogue, shape, view]);

  const query = plan.query;
  const blocking = plan.problems.filter((problem) => problem.blocking);
  const advisory = plan.problems.filter((problem) => !problem.blocking);

  const summaryAnswer = useBiQuery(
    bank?.id,
    view === "summary" ? query : null,
  );

  const spec: BiWidgetSpec | null = useMemo(() => {
    if (!query || view !== "summary") return null;
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
  }, [asOf, kind, query, view]);

  if (isBiUnavailable(catalogueQuery.error)) {
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

  const hasMeasures = catalogue.measures.length > 0;

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
          catalogue={catalogueQuery.data}
          asOf={asOf}
          onAsOfChange={setChosenDate}
          filters={shape.filters}
          onFiltersChange={(filters: BiFilter[]) =>
            setShape((current) => ({ ...current, filters }))
          }
        />

        {catalogueQuery.isPending && (
          <div className="card space-y-2 p-5" aria-busy="true">
            <SkeletonLine width="60%" />
            <SkeletonLine width="45%" />
            <SkeletonLine width="52%" />
          </div>
        )}

        {catalogueQuery.error && (
          <ErrorPanel
            error={catalogueQuery.error}
            onRetry={() => void catalogueQuery.refetch()}
            title="Could not load the analytics catalogue"
          />
        )}

        {catalogueQuery.data && !hasMeasures && (
          <EmptyState
            title="Nothing is available to analyse yet"
            description={`Your access does not cover any analytics measure for this institution. An organization owner can grant one — for example ${grantSentence(
              "credit",
              "aggregated",
            )}.`}
          />
        )}

        {catalogueQuery.data && hasMeasures && (
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
                      {entries.map((measure) => {
                        const selectable = measureIsCompatible(
                          catalogue,
                          shape.measures,
                          measure.id,
                        );
                        return (
                          <li key={measure.id}>
                            <label
                              className={`flex items-start gap-2 rounded px-1 py-1 ${
                                selectable
                                  ? "hover:bg-surface"
                                  : "opacity-60"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={shape.measures.includes(measure.id)}
                                disabled={!selectable}
                                onChange={() =>
                                  setShape((current) => ({
                                    ...current,
                                    measures: toggle(
                                      current.measures,
                                      measure.id,
                                    ),
                                  }))
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
                                {!selectable && (
                                  <span className="block text-caption text-slate">
                                    Reported as a{" "}
                                    {timeBehaviourLabel(measure.timeBehaviour)},
                                    which cannot share an answer with what you
                                    have already chosen.
                                  </span>
                                )}
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
                        );
                      })}
                    </ul>
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard
              title="Break down by"
              subtitle="Only fields the chosen measures can be grouped by are offered."
            >
              {shape.measures.length === 0 ? (
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
                  {availableDimensions.map((dimension) => {
                    const chosen = shape.dimensions.includes(dimension.id);
                    const atCap =
                      !chosen && shape.dimensions.length >= BI_DIMENSION_CAP;
                    return (
                      <li key={dimension.id}>
                        <label
                          className={`flex items-start gap-2 rounded px-1 py-1 ${
                            atCap ? "opacity-60" : "hover:bg-surface"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={chosen}
                            disabled={atCap}
                            onChange={() =>
                              setShape((current) => ({
                                ...current,
                                dimensions: toggle(
                                  current.dimensions,
                                  dimension.id,
                                ),
                                pivot:
                                  current.pivot === dimension.id
                                    ? null
                                    : current.pivot,
                              }))
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
                    );
                  })}
                </ul>
              )}
            </SectionCard>
          </div>
        )}

        {hasMeasures && shape.measures.length > 0 && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {ANSWER_VIEWS.map((option) => (
                <button
                  key={option.view}
                  type="button"
                  onClick={() => setView(option.view)}
                  aria-pressed={view === option.view}
                  className={`rounded-md border px-2.5 py-1 text-caption font-medium ${
                    view === option.view
                      ? "border-action/30 bg-action-light text-action"
                      : "border-border text-slate hover:bg-surface"
                  }`}
                >
                  {option.label}
                </button>
              ))}
              <span className="text-caption text-slate">
                {view === "summary"
                  ? "The whole answer as a chart or a compact table."
                  : "The answer paged row by row, with subtotals and columns you choose."}
              </span>
            </div>

            {view === "grid" && (
              <GridShapeControls
                catalogue={catalogue}
                shape={shape}
                onShapeChange={setShape}
              />
            )}

            {advisory.length > 0 && (
              <ul className="space-y-1" role="status">
                {advisory.map((problem) => (
                  <li key={problem.id} className="text-caption text-slate">
                    {problem.message}
                  </li>
                ))}
              </ul>
            )}

            {blocking.length > 0 && (
              <div className="rounded-md border border-warn/30 bg-warn-light px-3 py-2">
                <ul className="space-y-1">
                  {blocking.map((problem) => (
                    <li key={problem.id} className="text-caption text-navy">
                      {problem.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {!query && hasMeasures && blocking.length === 0 && (
          <EmptyState
            Icon={Table2}
            title="Choose a measure to see an answer"
            description="Nothing is queried until you do — this page asks the server only for the question you build."
          />
        )}

        {query && view === "summary" && spec && (
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
              result={summaryAnswer.data}
              isLoading={summaryAnswer.isPending}
              error={summaryAnswer.error}
              onRetry={() => void summaryAnswer.refetch()}
              onExplain={setExplaining}
              actions={<ExportMenu options={[printExportOption()]} />}
            />
          </div>
        )}

        {query && view === "grid" && (
          <SectionCard
            title="Your question, row by row"
            subtitle="Each page is fetched and authorized on its own. Sorting is applied by the server to the whole answer, not to the page on screen."
            actions={<ExportMenu options={[printExportOption()]} />}
            noPadding
          >
            <div className="p-4">
              <PivotGrid
                bankId={bank?.id}
                query={query}
                dataset={EXPLORE_DATASET}
              />
            </div>
          </SectionCard>
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
