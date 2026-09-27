"use client";

/**
 * Whether a BI figure reconciles to what the platform already files.
 *
 * FOUR states, and the fourth is the point. Green, amber and red are verdicts a
 * reconciliation check reached. GREY IS "NOT ASSESSED" — no check has run for
 * this institution and date, or the run could not reach one. It is not a pass,
 * it must never be drawn like a pass, and it is what every figure carries until
 * the checks have actually run. A badge that reads green over a check that did
 * not run is the one failure mode this component exists to prevent.
 *
 * Nothing here computes a status. The verdict arrives on the payload
 * (`BiTrustBadge` / `BiTrustRead`), and an unrecognised value degrades to grey
 * rather than to the most flattering neighbour.
 */

import {
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
} from "lucide-react";
/**
 * The four states, as the generated client names them. Declared here rather
 * than imported because the OpenAPI generator emits one enum per schema
 * (`BiTrustBadgeStatusEnum`, `BiTrustReadStatusEnum`, …) for what is one
 * vocabulary on the wire; every one of them is assignable to this.
 */
export type BiTrustStatus = "green" | "amber" | "red" | "grey";

type Presentation = {
  label: string;
  description: string;
  Icon: typeof ShieldCheck;
  className: string;
};

const PRESENTATION: Record<BiTrustStatus, Presentation> = {
  green: {
    label: "Reconciled",
    description:
      "These figures match the position the platform computed for this date.",
    Icon: ShieldCheck,
    className: "bg-success-light text-success border-success/20",
  },
  amber: {
    label: "Differences found",
    description:
      "These figures differ from the computed position by more than the tolerance for at least one check.",
    Icon: ShieldAlert,
    className: "bg-warning-light text-warning border-warning/20",
  },
  red: {
    label: "Does not reconcile",
    description:
      "At least one check failed. Treat these figures as indicative until the difference is resolved.",
    Icon: ShieldX,
    className: "bg-critical-light text-critical border-critical/20",
  },
  grey: {
    label: "Not assessed",
    description:
      "No reconciliation check has been completed for this date, so these figures have not been compared with the computed position.",
    Icon: ShieldQuestion,
    className: "bg-surface text-slate border-border",
  },
};

/** Never widen an unknown value into a pass. */
export function trustPresentation(status: string | null | undefined) {
  if (status === "green" || status === "amber" || status === "red") {
    return PRESENTATION[status];
  }
  return PRESENTATION.grey;
}

export default function TrustBadge({
  status,
  failingChecks = [],
  size = "default",
  className = "",
}: {
  status: BiTrustStatus | string | null | undefined;
  /** Check ids that did not pass, for the hover detail. Never figures. */
  failingChecks?: readonly string[];
  size?: "default" | "compact";
  className?: string;
}) {
  const presentation = trustPresentation(status);
  const { Icon } = presentation;
  const failing =
    failingChecks.length > 0
      ? ` Checks outstanding: ${failingChecks.join(", ")}.`
      : "";

  return (
    <span
      title={`${presentation.description}${failing}`}
      className={`inline-flex items-center gap-1.5 rounded border font-medium uppercase tracking-wider ${
        size === "compact"
          ? "px-1.5 py-0.5 text-micro"
          : "px-2 py-0.5 text-caption"
      } ${presentation.className} ${className}`}
    >
      <Icon size={size === "compact" ? 11 : 12} aria-hidden />
      {presentation.label}
    </span>
  );
}
