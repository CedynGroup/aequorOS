"use client";

/**
 * A saved dashboard's history: who changed it, when, and what they said about it.
 *
 * APPEND-ONLY, AND NOT BY CONVENTION. `bi_dashboard_versions` blocks UPDATE at
 * the database — a trigger, a revoked grant and a restrictive policy — so this
 * list can be extended and never rewritten. It is shown to everyone the dashboard
 * reaches, not only to its owner, because a person reading a figure off somebody
 * else's dashboard is entitled to know when the view that produced it last moved.
 *
 * NO CANVAS IS SERVED HERE, AND THAT IS THE ROUTE'S CHOICE. An earlier layout
 * would have to be authorized widget by widget for this reader before it could be
 * shown safely, so the history answers the question it can answer honestly — who,
 * when, and what changed — and does not offer a restore that would need a second
 * authorization path nobody has written.
 */

import { History } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { fmtTimestamp } from "@/lib/api/values";
import { fmtInt } from "@/lib/format";
import type { BiDashboardVersionRead } from "@aequoros/risk-service-api";

export type VersionHistoryProps = Readonly<{
  versions: readonly BiDashboardVersionRead[] | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
}>;

export default function VersionHistory({
  versions,
  isLoading,
  error,
  onRetry,
}: VersionHistoryProps) {
  return (
    <SectionCard
      title="History"
      subtitle="Every save is kept. Nothing here can be edited or removed."
    >
      {isLoading && (
        <div aria-busy="true" className="space-y-2">
          <SkeletonLine width="70%" />
          <SkeletonLine width="55%" />
        </div>
      )}

      {Boolean(error) && (
        <ErrorPanel
          error={error}
          onRetry={onRetry}
          title="Could not read this dashboard's history"
        />
      )}

      {versions && (
        <ol className="space-y-3">
          {[...versions]
            .sort((left, right) => right.version - left.version)
            .map((entry) => (
              <li key={entry.version} className="flex items-start gap-3">
                <span
                  className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface text-slate"
                  aria-hidden
                >
                  <History size={12} />
                </span>
                <div className="min-w-0">
                  <p className="text-body text-navy">
                    Version {fmtInt(entry.version)}
                    {entry.current && (
                      <span className="ml-2 rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
                        Shown now
                      </span>
                    )}
                  </p>
                  <p className="text-caption text-slate">
                    {entry.createdByDisplayName ?? "A colleague"} ·{" "}
                    <span className="font-mono tnum">
                      {fmtTimestamp(entry.createdAt)}
                    </span>{" "}
                    ·{" "}
                    {entry.widgetCount === 1
                      ? "1 view"
                      : `${fmtInt(entry.widgetCount)} views`}
                  </p>
                  <p className="text-caption leading-relaxed text-slate">
                    {entry.changeNote.length > 0
                      ? entry.changeNote
                      : entry.version === 1
                        ? "Saved for the first time."
                        : "Saved with no note."}
                  </p>
                </div>
              </li>
            ))}
        </ol>
      )}
    </SectionCard>
  );
}
