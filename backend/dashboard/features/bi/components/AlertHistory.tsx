"use client";

/**
 * What one threshold alert has recorded.
 *
 * Every row here states a figure, so the server refuses this read unless the
 * caller's own bindings cover the alert's figure — including when the caller owns
 * the alert. A 403 is therefore an ordinary state of this panel rather than an
 * error: the alert's own row already carries the sentence explaining it, so this
 * repeats it plainly and offers nothing to retry.
 *
 * A row is written on a TRANSITION, not on every evaluation, which is why a month
 * in breach shows one entry. That is stated, because a history with four rows in a
 * year otherwise reads as a feature that is not running.
 */

import { ArrowDownRight, ArrowUpRight, CircleSlash } from "lucide-react";
import EmptyState from "@/components/ui/EmptyState";
import type { BiAlertEventRead } from "@/lib/api/bi";

export default function AlertHistory({
  events,
  loading,
  withheldMessage,
}: {
  events: readonly BiAlertEventRead[];
  loading: boolean;
  /** Set when the server refused this read; the alert row's own sentence. */
  withheldMessage: string | null;
}) {
  if (withheldMessage) {
    return (
      <p className="rounded-md bg-surface px-3 py-2 text-caption leading-relaxed text-slate">
        {withheldMessage}
      </p>
    );
  }
  if (loading) {
    return (
      <p className="text-caption text-slate">Reading what was recorded…</p>
    );
  }
  if (events.length === 0) {
    return (
      <EmptyState
        Icon={CircleSlash}
        title="Nothing recorded yet"
        description="This alert is judged each time the institution's figures are rebuilt for a reporting date, and an entry appears here only when the verdict CHANGES — so a book that stays within its threshold records nothing."
      />
    );
  }
  return (
    <ul className="flex flex-col divide-y divide-border-light">
      {events.map((event) => (
        <li key={event.id} className="flex items-start gap-2 py-3">
          <span className="mt-0.5 shrink-0 text-slate">
            {event.state === "breached" ? (
              <ArrowUpRight size={14} className="text-warning" aria-hidden />
            ) : event.state === "cleared" ? (
              <ArrowDownRight size={14} className="text-success" aria-hidden />
            ) : (
              <CircleSlash size={14} aria-hidden />
            )}
          </span>
          <div className="flex flex-col gap-0.5">
            <p className="text-body leading-relaxed text-navy">
              {event.detail}
            </p>
            <p className="text-micro text-slate">
              Recorded for the reporting date {event.asOfDate}
              {event.thresholdBasis === "governed_limit"
                ? ", against the limit governed for this figure"
                : ""}
              .
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}
