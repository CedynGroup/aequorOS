"use client";

/**
 * The submission deadline, exactly as the server computed it.
 *
 * D-024 / D-034: the due date, the days remaining and the traffic light all
 * arrive on the readiness payload — the amber window is the governed parameter
 * `icaap_deadline_amber_days`, not a number written down here. This component
 * compares nothing and counts nothing; it chooses words and a colour for a
 * verdict the platform already made.
 *
 * THREE STATES, and the middle one is the reason this comment exists:
 *
 *  1. A due date with a rag of green / amber / red — colour it.
 *  2. A due date with `rag: "none"` — the date is real and the countdown is
 *     real, but no band is configured to colour it with. It renders as an
 *     UNCOLOURED countdown. It must never read as "no deadline": the filing
 *     obligation exists whether or not anyone has configured a warning window,
 *     and a neutral chip saying "Due 31 Mar 2027 · 12 days left" is honest
 *     where a grey "no deadline" would be false.
 *  3. No due date at all — the cycle is filed in a timely manner with no fixed
 *     date, which is what it says.
 */

import { CalendarClock } from "lucide-react";
import type { IcaapDeadlineStatusRead } from "@/lib/api/icaap";
import { fmtDateValue, toDate } from "./format";

const TONE: Record<string, string> = {
  green: "bg-success-light text-success border-success/20",
  amber: "bg-warning-light text-warning border-warning/20",
  red: "bg-critical-light text-critical border-critical/20",
  none: "bg-surface text-slate border-border",
};

function remainingCopy(days: number | null | undefined): string | null {
  if (days === null || days === undefined) return null;
  if (days < 0) {
    const overdue = Math.abs(days);
    return `${overdue} ${overdue === 1 ? "day" : "days"} overdue`;
  }
  if (days === 0) return "due today";
  return `${days} ${days === 1 ? "day" : "days"} left`;
}

export default function DeadlineBadge({
  deadline,
  className = "",
}: {
  deadline: IcaapDeadlineStatusRead;
  className?: string;
}) {
  const dueDate = toDate(deadline.dueDate);
  const remaining = remainingCopy(deadline.daysRemaining);
  const tone = TONE[deadline.rag] ?? TONE.none;

  const label = dueDate
    ? `Due ${fmtDateValue(dueDate)}${remaining ? ` · ${remaining}` : ""}`
    : "Timely — no fixed date";

  const title =
    dueDate && deadline.rag === "none"
      ? "The deadline stands. No warning window is configured for this framework, so the date is shown without a status colour."
      : undefined;

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-caption font-medium ${tone} ${className}`}
    >
      <CalendarClock size={12} aria-hidden />
      {label}
    </span>
  );
}
