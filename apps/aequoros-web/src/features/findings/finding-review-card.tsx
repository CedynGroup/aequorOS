import type { ReactNode } from "react";

import { Badge } from "../../components/ui";

export type FindingReviewSummary = {
  title: string;
  summary: string;
  rationale?: string | null;
  severity: string;
  status: string;
};

export function FindingReviewCard({
  finding,
  metadata,
  evidence,
  children,
}: {
  finding: FindingReviewSummary;
  metadata?: ReactNode;
  evidence?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <article className="grid gap-2 rounded-md border border-[rgb(var(--border))] p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          tone={
            finding.severity === "high" || finding.severity === "critical"
              ? "danger"
              : "warning"
          }
        >
          {finding.severity}
        </Badge>
        <Badge>{finding.status}</Badge>
        <span className="font-medium">{finding.title}</span>
      </div>
      <div>{finding.summary}</div>
      {finding.rationale ? (
        <div className="text-[rgb(var(--muted-foreground))]">
          {finding.rationale}
        </div>
      ) : null}
      {metadata ? (
        <div className="text-[rgb(var(--muted-foreground))]">{metadata}</div>
      ) : null}
      {evidence}
      {children}
    </article>
  );
}
