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
 * It draws with the lightweight SVG `Sparkline`, never with an ECharts canvas.
 * The strip is meant to sit under the page header on landing pages including
 * the Command Center, and `scripts/assert-home-route-bundle.mjs` keeps the
 * charting runtime out of that route's initial bundle.
 *
 * No insights is a real state, not a failure: a date whose marts have not been
 * built, or whose movements are all unremarkable, honestly has none.
 */

import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import Sparkline from "@/components/ui/Sparkline";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
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
  insights,
  isLoading = false,
  error = null,
  onRetry,
  emptyMessage = "Nothing stands out for this reporting date.",
}: {
  insights: readonly BiInsight[];
  isLoading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyMessage?: string;
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

  if (error) {
    return (
      <ErrorPanel
        error={error}
        onRetry={onRetry}
        title="Could not load insights"
      />
    );
  }

  if (insights.length === 0) {
    return (
      <div className="card flex items-center gap-3 px-5 py-4">
        <Sparkles size={16} className="shrink-0 text-slate" aria-hidden />
        <p className="text-body text-slate">{emptyMessage}</p>
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
