"use client";

/**
 * What the platform is prepared to SAY about a reporting date.
 *
 * Every statement is composed server-side from typed facts
 * (`app/services/bi/insights/`), with its figures already formatted in the
 * institution's own unit and its reservations already attached — the advisory
 * basis of a measure, a gap in the data it would have needed. The strip
 * renders those words as given: it does
 * not recompute a figure, re-round one, or drop a qualifier to make a sentence
 * read better.
 *
 * IT TAKES THE SERVER'S WHOLE ANSWER, NOT A LIST OF SENTENCES. That is the point
 * of the props below. "No statements" has two completely different meanings and
 * only the payload can tell them apart:
 *
 *  * `measuresRead > 0` and no statements — the figures WERE read and every
 *    movement fell inside the materiality threshold. *Nothing stands out.*
 *  * `measuresRead === 0` and no statements — nothing was computed for this date
 *    at all. Saying "nothing stands out" there would be a claim about the
 *    institution's figures made to a reader who was shown none of them, and the
 *    platform never made it.
 *
 * A reader whose access covers none of the headline measures is refused outright
 * (`403 bi_insights_authorization_denied`, naming no measure) rather than handed
 * an empty list, and that refusal is rendered as a refusal.
 *
 * It draws with the lightweight SVG `Sparkline`, never with an ECharts canvas.
 * The strip is meant to sit under the page header on landing pages including
 * the Command Center, and `scripts/assert-home-route-bundle.mjs` keeps the
 * charting runtime out of that route's initial bundle.
 *
 * THIS FILE ALSO HOSTS WHAT A LANDING PAGE MOUNTS. `docs/bi.md` §Insights layer
 * names the seams a module page carries, and both live here so that every
 * landing page wires them the same way and none can get the gating wrong on
 * its own: `LandingInsightStrip` (the strip, connected to the route and to the
 * feature flag) and `useKpiExplain` (the provenance drawer a `KpiStat`'s
 * `explain` prop opens). `landingSurfaces.test.ts` pins that every landing
 * page reaches both — "built and never mounted" is the defect this build has
 * shipped repeatedly (audit A360-7 S3).
 */

import { useCallback, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import type { BiInsightsRead } from "@aequoros/risk-service-api";
import Sparkline from "@/components/ui/Sparkline";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import {
  kpiExplainQuery,
  liveEngineMeasureId,
  type KpiExplainTarget,
} from "@/components/ui/KpiStat";
import { useBankContext } from "@/components/shell/BankContext";
import {
  biRefusalSentence,
  isBiAccessDenied,
  isBiUnavailable,
  useBiAvailability,
  useBiCatalogue,
  useBiInsights,
} from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import ExplainDrawer from "./ExplainDrawer";
import RefusedWidget from "./RefusedWidget";
import RestrictedWidget from "./RestrictedWidget";
import type { BiFavourability, BiInsight } from "./types";

const TREND_COLOR: Record<BiFavourability, string> = {
  favourable: "rgb(var(--ok))",
  adverse: "rgb(var(--crit))",
  neutral: "rgb(var(--text-muted))",
};

const ACCENT: Record<BiFavourability, string> = {
  favourable: "border-l-success",
  adverse: "border-l-critical",
  neutral: "border-l-border",
};

/** Nothing was computed for this date, which is not the same as nothing to say. */
const NOTHING_COMPUTED =
  "No analytics figures have been computed for this reporting date yet, so the platform has nothing to say about it.";
/** The figures were read and every movement fell inside the threshold. */
const NOTHING_STANDS_OUT = "Nothing stands out for this reporting date.";

/**
 * The server's statements in the shape the cards render.
 *
 * The one thing that changes is the DATE: the generated client parses
 * `format: date` into a `Date`, and everything in this app compares calendar
 * days. Nothing else is touched — in particular no figure is reformatted, and
 * `series` and `href` are left absent because the wire carries neither. The
 * sparkline and the "See the working" affordance are both conditional, so a
 * statement without them degrades to the sentence itself rather than to a
 * broken link or an invented trend.
 */
export function insightViews(
  read: BiInsightsRead | null | undefined,
): readonly BiInsight[] {
  return (read?.insights ?? []).map((insight) => ({
    id: insight.id,
    ruleId: insight.ruleId,
    statementClass: insight.statementClass,
    headline: insight.headline,
    detail: insight.detail,
    asOf: isoDay(insight.asOf) ?? "",
    measureIds: insight.measureIds,
    favourability: insight.favourability,
    emphasis: insight.emphasis,
    qualifiers: insight.qualifiers,
    certified: insight.certified,
  }));
}

function InsightCard({ insight }: { insight: BiInsight }) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-body font-medium leading-snug text-navy">
          {insight.headline}
        </p>
        {insight.series && insight.series.length > 1 && (
          <Sparkline
            data={[...insight.series]}
            color={TREND_COLOR[insight.favourability]}
          />
        )}
      </div>
      <p className="mt-1 text-caption leading-relaxed text-slate">
        {insight.detail}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {insight.qualifiers.map((qualifier) => (
          <span
            key={qualifier}
            className="rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro text-slate"
          >
            {qualifier}
          </span>
        ))}
        {insight.href && (
          <span className="inline-flex items-center gap-1 text-micro font-medium text-action">
            See the working
            <ArrowRight size={11} aria-hidden />
          </span>
        )}
      </div>
    </>
  );

  const className = `card min-w-[18rem] max-w-sm flex-1 border-l-2 p-4 ${
    ACCENT[insight.favourability]
  }`;

  return insight.href ? (
    <Link href={insight.href} className={`${className} hover:bg-surface/60`}>
      {body}
    </Link>
  ) : (
    <article className={className}>{body}</article>
  );
}

export default function InsightStrip({
  data,
  isLoading = false,
  error = null,
  onRetry,
}: {
  /** The whole `GET …/bi/insights` answer. Never a list assembled elsewhere. */
  data: BiInsightsRead | null | undefined;
  isLoading?: boolean;
  error?: unknown;
  onRetry?: () => void;
}) {
  if (isLoading) {
    return (
      <div className="flex gap-3 overflow-hidden" aria-busy="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="card min-w-[18rem] flex-1 space-y-2 p-4">
            <SkeletonLine width="85%" />
            <SkeletonLine width="65%" />
            <SkeletonLine width="40%" height={8} />
          </div>
        ))}
      </div>
    );
  }

  // The route refuses rather than serving an empty list to a reader who holds
  // none of the headline measures, and it names none of them in doing so.
  if (isBiAccessDenied(error)) return <RestrictedWidget />;

  // Every other refusal — an impersonated staff session, for one — arrives with
  // the server's own sentence and nothing to hide. It is rendered verbatim: "an
  // organization owner can grant it" would state a decision the server never
  // made, and an instruction the reader could not act on.
  const refusal = biRefusalSentence(error);
  if (refusal !== null) return <RefusedWidget sentence={refusal} />;

  if (error) {
    return (
      <ErrorPanel
        error={error}
        onRetry={onRetry}
        title="Could not load insights"
      />
    );
  }

  const insights = insightViews(data);

  if (insights.length === 0) {
    return (
      <div className="card flex items-center gap-3 px-5 py-4">
        <Sparkles size={16} className="shrink-0 text-slate" aria-hidden />
        <p className="text-body text-slate">
          {(data?.measuresRead ?? 0) > 0
            ? NOTHING_STANDS_OUT
            : NOTHING_COMPUTED}
        </p>
      </div>
    );
  }

  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      {insights.map((insight) => (
        <InsightCard key={insight.id} insight={insight} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// What a landing page mounts
// ---------------------------------------------------------------------------

/**
 * THE STRIP, CONNECTED. A landing page hands it the institution and the page's
 * own reporting date and mounts it directly under `PageHeader`; nothing else is
 * decided on the page. Three absences are rendered as absences, and the two
 * that mean "this surface is not here" render NOTHING rather than an error:
 *
 *  * `BI_ENABLED` is off, or not yet known to be on — `useBiAvailability`
 *    answers `false` on a failed flag read and `undefined` while loading, and
 *    both are treated as off so a module page never flashes a BI surface at a
 *    deployment that does not serve one.
 *  * The route answered 404 — the flag is off after all, or the institution is
 *    another tenant's. A feature that is off is not "forbidden"; it is not there.
 *  * No reporting date — nothing to speak about, so nothing is asked.
 *
 * A 403 is NOT one of those, and `InsightStrip` renders it as a refusal in one
 * of two shapes: a GRANT denial (the reader holds none of the headline measures)
 * is paraphrased, because its body names what was refused; every other refusal
 * (an impersonated staff session, say) is shown in the server's own sentence. A
 * date with nothing computed says so in words, never as an empty strip and never
 * as a figure. The insights hub (`app/(app)/insights/page.tsx`) mounts the raw
 * `InsightStrip` itself because it owns a date picker and a catalogue read of
 * its own; this is the shape every OTHER landing page uses.
 */
export function LandingInsightStrip({
  bankId,
  asOf,
  className = "",
}: {
  bankId: string | undefined;
  /** The page's reporting date, ISO day or `Date`. */
  asOf: Date | string | null | undefined;
  className?: string;
}) {
  const availability = useBiAvailability();
  const enabled = availability.biEnabled === true;
  const day = isoDay(asOf);
  const insights = useBiInsights(bankId, day, undefined, enabled);

  if (!enabled || !bankId || day === null) return null;
  if (isBiUnavailable(insights.error)) return null;

  const strip = (
    <InsightStrip
      data={insights.data}
      isLoading={insights.isPending}
      error={insights.error}
      onRetry={() => void insights.refetch()}
    />
  );
  return className ? <div className={className}>{strip}</div> : strip;
}

/**
 * THE PROVENANCE DRAWER A MODULE KPI OPENS.
 *
 * A module landing page shows the live engine's figures, and BI keeps a copy of
 * each as a `certified_engine` measure. `explainFor(metricId)` returns the
 * click handler for the KPI that shows the registry metric `metricId` — or
 * `undefined`, in which case `KpiStat` renders exactly as it always has —
 * and `drawer` is the one `ExplainDrawer` the page renders (anywhere in its
 * tree, once). Opening it asks `POST …/bi/explain` for that measure in a
 * point-in-time query at the page's reporting date, and the drawer shows the
 * measure's declaration and the engine row it is a copy of.
 *
 * `undefined` is returned, and no button is drawn, whenever the affordance would
 * not be honest:
 *
 *  * BI is off or not yet known to be on;
 *  * no institution or no reporting date;
 *  * the catalogue has not answered, or refused — the catalogue is filtered to
 *    what THIS reader may query, so a measure absent from it is one the drawer
 *    would 403 on, and offering the button would be offering a refusal;
 *  * the metric has no live copy in the catalogue, or has several and none of
 *    them is under the institution's own capital regime
 *    (`components/ui/KpiStat.tsx::liveEngineMeasureId`).
 */
export function useKpiExplain(
  bankId: string | undefined,
  asOf: Date | string | null | undefined,
): {
  explainFor: (metricId: string) => (() => void) | undefined;
  drawer: ReactNode;
} {
  const { institutionType } = useBankContext();
  const availability = useBiAvailability();
  const enabled = availability.biEnabled === true;
  const day = isoDay(asOf);
  const catalogue = useBiCatalogue(bankId, enabled);
  const [explaining, setExplaining] = useState<KpiExplainTarget | null>(null);

  const measures = catalogue.data?.measures;
  const capitalRegime = institutionType?.detail?.capitalRegime ?? null;

  const explainFor = useCallback(
    (metricId: string): (() => void) | undefined => {
      if (!enabled || !bankId || day === null || !measures) return undefined;
      const measure = liveEngineMeasureId(measures, metricId, capitalRegime);
      if (measure === null) return undefined;
      return () =>
        setExplaining({ measure, query: kpiExplainQuery(measure, day) });
    },
    [enabled, bankId, day, measures, capitalRegime],
  );

  const drawer =
    enabled && bankId ? (
      <ExplainDrawer
        bankId={bankId}
        measure={explaining?.measure ?? null}
        query={explaining?.query ?? null}
        onClose={() => setExplaining(null)}
      />
    ) : null;

  return { explainFor, drawer };
}
