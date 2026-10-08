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
 * THE INSTITUTION'S OWN FIGURES ARE OFFERED BESIDE THE PLATFORM'S. A calculated
 * measure certified by this bank (`/explore/measures`) is a figure like any other
 * once two people have stood behind it, so it appears in the measure list under
 * its own heading — and it is DERIVED into a catalogue entry from the figures its
 * formula names (`components/bi/measures.ts::calculatedMeasureOffer`), so every
 * rule about what a question may do with it is a rule about those figures. Only
 * CERTIFIED ones are offered: the compiler resolves no other kind, so a draft in
 * the list would be a guaranteed refusal wearing a checkbox.
 *
 * AND THE ANSWER SAYS HOW MUCH OF THE BOOK IT COVERS. A grant carries a data
 * scope, and the server applies it to every figure, page and subtotal here — but
 * the query payload discloses no scope, so the only honest source in the browser
 * is the per-capability scope `/auth/me` projects. The question's own ids are
 * resolved to the (module, sensitivity) addresses they read
 * (`measures.questionAddresses` — a bank formula is resolved to the FIGURES its
 * text names, never to itself, because that is what the server authorizes), and
 * the coverage of those addresses is stated above the answer and on each figure's
 * own label. It fails closed: an address that cannot be resolved, or a projection
 * carrying no scope, is never read as the whole institution.
 *
 * NOTHING IS EXPORTED FROM THE GRID ITSELF. AG Grid's own CSV export is
 * disabled in `components/bi/PivotGridCanvas.tsx`; every way out of this page
 * goes through the governed Export menu, which authorizes, audits and
 * watermarks what it releases. The menu offers the same question in three
 * artifacts plus the browser's own print, and each governed one is answered by
 * `POST …/bi/export` — re-authorized, classified from the catalogue members the
 * question touches, audited and stamped. A reader whose access does not cover an
 * export of these figures is told so; no partial file is ever produced.
 */

import { useMemo, useState } from "react";
import { Table2 } from "lucide-react";
import type {
  BiCatalogueMeasureRead,
  BiFilter,
} from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import CoverageNotice from "@/components/access/CoverageNotice";
import { useBankContext } from "@/components/shell/BankContext";
import ExplainDrawer from "@/components/bi/ExplainDrawer";
import ExportActions from "@/components/bi/ExportActions";
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
  hasCalculatedMeasure,
  incompatibleReason,
  measureIsCompatible,
  sliceableDimensions,
  type ExploreCatalogue,
  type ExploreShape,
} from "@/components/bi/exploreQuery";
import {
  calculatedMeasureOffer,
  questionAddresses,
  unusableSentence,
} from "@/components/bi/measures";
import type { BiWidgetKind, BiWidgetSpec } from "@/components/bi/types";
import {
  isBiUnavailable,
  useBiCatalogue,
  useBiMeasures,
  useBiQuery,
} from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import { coverageForFigures } from "@/lib/api/dataScope";
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

function toggle(values: readonly string[], id: string): string[] {
  return values.includes(id)
    ? values.filter((value) => value !== id)
    : [...values, id];
}

/**
 * One figure in the picker — a platform member or one of this institution's own
 * formulas, drawn identically because by this point they are the same kind of
 * thing: a catalogue entry with allowed dimensions and a time behaviour.
 *
 * When it cannot be chosen, the REASON is the one that actually applies
 * (`incompatibleReason`). It was a single hardcoded sentence about time behaviour
 * while that was the only reason there was; a disabled option whose stated reason
 * is the wrong one sends a reader to remove the wrong measure.
 */
function MeasureOption({
  measure,
  catalogue,
  chosen,
  onToggle,
}: {
  measure: BiCatalogueMeasureRead;
  catalogue: ExploreCatalogue;
  chosen: readonly string[];
  onToggle: (id: string) => void;
}) {
  const selectable = measureIsCompatible(catalogue, chosen, measure.id);
  const reason = incompatibleReason(catalogue, chosen, measure.id);
  const designation = designationLabel(measure.advisoryDesignation ?? null);
  return (
    <label
      className={`flex items-start gap-2 rounded px-1 py-1 ${
        selectable ? "hover:bg-surface" : "opacity-60"
      }`}
    >
      <input
        type="checkbox"
        checked={chosen.includes(measure.id)}
        disabled={!selectable}
        onChange={() => onToggle(measure.id)}
        className="mt-0.5"
      />
      <span className="min-w-0">
        <span className="block text-body text-navy">{measure.label}</span>
        <span className="block text-caption text-slate">
          {measure.description}
        </span>
        {reason !== null && (
          <span className="block text-caption text-slate">{reason}</span>
        )}
        {designation && (
          <span className="mt-0.5 inline-block rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro text-slate">
            {designation}
          </span>
        )}
      </span>
    </label>
  );
}

export default function ExplorePage() {
  const { bank, period, institutionCapabilities, authorityPending } =
    useBankContext();
  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;

  const [shape, setShape] = useState<ExploreShape>(EMPTY_SHAPE);
  const [kind, setKind] = useState<BiWidgetKind>("table");
  const [view, setView] = useState<AnswerView>("summary");
  const [explaining, setExplaining] = useState<string | null>(null);

  const catalogueQuery = useBiCatalogue(bank?.id);
  const measuresQuery = useBiMeasures(bank?.id);

  const published = useMemo(
    () => catalogueQuery.data?.measures ?? [],
    [catalogueQuery.data],
  );

  /**
   * This institution's own certified formulas, turned into catalogue entries.
   *
   * Only the ones the derivation could complete honestly: a formula whose figures
   * are not all in this reader's catalogue, or which mixes a position with a
   * movement, is named with the reason rather than offered — see
   * `calculatedMeasureOffer`. A draft is not here at all: the compiler resolves
   * only certified measures, so an uncertified checkbox would guarantee a refusal.
   */
  const bankDefined = useMemo(() => {
    const usable: BiCatalogueMeasureRead[] = [];
    const withheld: { label: string; why: string }[] = [];
    for (const measure of measuresQuery.data?.measures ?? []) {
      const offer = calculatedMeasureOffer(measure, published);
      if (offer.usable) {
        usable.push(offer.entry);
      } else if (offer.reason !== "not_certified") {
        withheld.push({
          label: measure.label,
          why: unusableSentence(offer.reason),
        });
      }
    }
    return { usable, withheld };
  }, [measuresQuery.data, published]);

  const catalogue: ExploreCatalogue = useMemo(
    () => ({
      measures: [...published, ...bankDefined.usable],
      dimensions: catalogueQuery.data?.dimensions ?? [],
    }),
    [bankDefined.usable, catalogueQuery.data, published],
  );

  const measureGroups = useMemo(() => {
    const groups = new Map<string, BiCatalogueMeasureRead[]>();
    for (const measure of published) {
      const key = measure.module;
      groups.set(key, [...(groups.get(key) ?? []), measure]);
    }
    return [...groups.entries()].sort((left, right) =>
      moduleLabel(left[0]).localeCompare(moduleLabel(right[0])),
    );
  }, [published]);

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

  /**
   * How much of the institution's book THIS question's answer covers.
   *
   * Resolved from the question the reader built, not from the page: the measures,
   * the fields it is broken down by and the fields it is filtered on are all
   * reads, and the BI authorization path evaluates a sentence for each distinct
   * (module, sensitivity) among them — a filter can itself disclose. The
   * reduction then mirrors the server's: the narrowest sentence wins, and two
   * narrow sentences that disagree are refused rather than guessed at.
   */
  const coverage = useMemo(() => {
    const addresses = questionAddresses(
      {
        measures: shape.measures,
        dimensions: shape.dimensions,
        filterMembers: shape.filters.map((filter) => filter.member),
      },
      published,
      catalogue.dimensions,
      measuresQuery.data?.measures ?? [],
    );
    return coverageForFigures(institutionCapabilities, addresses, {
      pending: authorityPending || catalogueQuery.isPending,
    });
  }, [
    authorityPending,
    catalogue.dimensions,
    catalogueQuery.isPending,
    institutionCapabilities,
    measuresQuery.data,
    published,
    shape.dimensions,
    shape.filters,
    shape.measures,
  ]);

  const summaryAnswer = useBiQuery(bank?.id, view === "summary" ? query : null);

  const spec: BiWidgetSpec | null = useMemo(() => {
    if (!query || view !== "summary") return null;
    return {
      id: "explore",
      title: "Your question",
      // The coverage rides on the answer's own caption as well as on the banner:
      // a chart is the thing that gets screenshotted out of this page, and the
      // caption travels with it where the banner does not.
      subtitle: `${fmtInt(query.measures.length)} ${
        query.measures.length === 1 ? "measure" : "measures"
      } for ${asOf}${coverage.suffix ? `, ${coverage.suffix}` : ""}`,
      kind,
      query,
      // No dataset is NAMED for a question a reader composed: its measures may
      // be engine copies, mart computations or a bank formula, and an empty
      // answer says nothing about which input is missing. This page used to
      // send every empty answer to the positions template — a false cause for a
      // capital ratio whose official run had simply not been minted.
      dataset: null,
      layout: { i: "explore", x: 0, y: 0, w: 12, h: 6 },
    };
  }, [asOf, coverage.suffix, kind, query, view]);

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
  const explainWithheld = hasCalculatedMeasure(catalogue, shape.measures);

  return (
    <>
      <PageHeader
        title="Explore"
        subtitle="Choose what to measure and how to break it down. Everything offered here is something your access already covers."
        asOf={asOf}
        action={<ExportActions bankId={bank?.id} query={query} />}
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
                      {entries.map((measure) => (
                        <li key={measure.id}>
                          <MeasureOption
                            measure={measure}
                            catalogue={catalogue}
                            chosen={shape.measures}
                            onToggle={(id) =>
                              setShape((current) => ({
                                ...current,
                                measures: toggle(current.measures, id),
                              }))
                            }
                          />
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}

                {/*
                  This institution's own certified formulas. Their own heading
                  rather than a module's, because a formula spans whatever modules
                  its figures need — the server evaluates a sentence per pair, so
                  filing it under one of them would understate what it reads.
                */}
                {bankDefined.usable.length > 0 && (
                  <div>
                    <p className="mb-1 text-micro font-medium uppercase tracking-wider text-slate">
                      Certified by this institution
                    </p>
                    <ul className="space-y-0.5">
                      {bankDefined.usable.map((measure) => (
                        <li key={measure.id}>
                          <MeasureOption
                            measure={measure}
                            catalogue={catalogue}
                            chosen={shape.measures}
                            onToggle={(id) =>
                              setShape((current) => ({
                                ...current,
                                measures: toggle(current.measures, id),
                              }))
                            }
                          />
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {bankDefined.withheld.length > 0 && (
                  <ul className="space-y-1 border-t border-border-light pt-2">
                    {bankDefined.withheld.map((entry) => (
                      <li key={entry.label} className="text-caption text-slate">
                        <span className="text-navy">{entry.label}</span> —{" "}
                        {entry.why}
                      </li>
                    ))}
                  </ul>
                )}
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

        {/*
          ABOVE THE ANSWER, and above the view toggle, because it qualifies BOTH
          readings of the question: the summary's chart and the grid's subtotals
          are the same figures over the same slice.
        */}
        <CoverageNotice coverage={coverage} />

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

        {/*
          Shown exactly when no measure has been chosen, which is what it says.
          It used to be gated on `blocking.length === 0` and was therefore
          unreachable: the one path that returns a null query also raises the
          blocking `no-measure` problem, whose own message renders inside a branch
          that requires a measure to have been chosen — so a reader landing here
          was given no instruction beside the answer area at all.
        */}
        {hasMeasures && shape.measures.length === 0 && (
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

            {/*
              WHERE A FIGURE CAME FROM IS A QUESTION ABOUT A PUBLISHED FIGURE.
              `POST …/bi/explain` resolves the measure against the STATIC
              catalogue, so it has no answer for one of this institution's own
              formulas — and the formula's own provenance is its text, which is on
              `/explore/measures` in full with the figures it reads. The control is
              therefore withheld for such a question, with the reason said out
              loud, rather than offered and answered with an error.
            */}
            {explainWithheld && (
              <p className="text-caption text-slate">
                One of the figures in this answer is a formula your institution
                defined. How it is worked out is its own formula — see it under
                Calculated measures — so the per-figure provenance panel is not
                offered for this question.
              </p>
            )}

            <WidgetRenderer
              spec={spec}
              result={summaryAnswer.data}
              isLoading={summaryAnswer.isPending}
              error={summaryAnswer.error}
              onRetry={() => void summaryAnswer.refetch()}
              onExplain={explainWithheld ? undefined : setExplaining}
              actions={<ExportActions bankId={bank?.id} query={query} />}
              // An empty answer under a narrowed scope is not an empty book;
              // the coverage carries the sentence that says so.
              coverage={coverage}
              // The query AS SUBMITTED, which is what a drill-through needs: it
              // carries the reader's date and every filter they narrowed by, so
              // the rows behind a figure are the figure's own rows.
              query={query}
            />
          </div>
        )}

        {query && view === "grid" && (
          <SectionCard
            title="Your question, row by row"
            subtitle={`Each page is fetched and authorized on its own. Sorting is applied by the server to the whole answer, not to the page on screen.${
              coverage.hint ? ` ${coverage.hint}.` : ""
            }`}
            actions={<ExportActions bankId={bank?.id} query={query} />}
            noPadding
          >
            <div className="p-4">
              <PivotGrid
                bankId={bank?.id}
                query={query}
                dataset={null}
                coverage={coverage}
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
