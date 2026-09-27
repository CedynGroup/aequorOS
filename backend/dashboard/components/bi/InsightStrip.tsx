"use client";

/**
 * What the platform is prepared to SAY about a reporting date.
 *
 * Every statement is composed server-side from typed facts
 * (`app/services/bi/insights/`), with its figures already formatted in the
 * institution's own unit and its reservations already attached — the advisory
 * basis of a measure, the trust state of the checks behind it, a gap in the
 * data it would have needed. The strip renders those words as given: it does
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
 */

import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import type { BiInsightsRead } from "@aequoros/risk-service-api";
import Sparkline from "@/components/ui/Sparkline";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { isBiAccessDenied } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
import RestrictedWidget from "./RestrictedWidget";
import TrustBadge from "./TrustBadge";
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
    trust: insight.trust,
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
        <TrustBadge
          status={insight.trust?.status}
          failingChecks={insight.trust?.failingChecks ?? []}
          size="compact"
        />
        {insight.qualifiers.map((qualifier) => (
          <span
            key={qualifier}
            className="rounded border border-border bg-surface px-1.5 py-0.5 text-micro text-slate"
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
