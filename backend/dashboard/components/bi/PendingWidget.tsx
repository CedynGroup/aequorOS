"use client";

/**
 * A widget whose figure is waiting on the PLATFORM, not on the bank.
 *
 * This is deliberately a different tile from "Needs data". A bank that pushes its
 * position book every night and is told it has not supplied something is being
 * given a false statement about its own book, and the person who reads it goes
 * looking for an upload that will never fix anything. So a figure the platform
 * has not published yet says exactly that, and offers no upload.
 *
 * And it shows no number. An unmeasured figure drawn as a chart is a flat line on
 * the baseline; drawn as a tile it is a zero that clears every floor. Neither is
 * a measurement, so neither is shown.
 *
 * The caption is the pack file's own, which already names what is outstanding.
 */

import { Clock } from "lucide-react";
import type { BiPendingCapability } from "./types";

/**
 * What is outstanding, in one sentence per kind. Each states who it is waiting
 * on, because that is the only actionable part of the answer.
 */
const OUTSTANDING: Readonly<Record<BiPendingCapability, string>> = {
  catalogue_member:
    "The platform does not publish this figure as a measure yet, so there is nothing to read for it.",
  governed_limit:
    "The limits this view compares against are held in the bank's own register and are not resolved for display yet.",
  mart_field:
    "The analytics tables do not carry the field this view groups by yet, so the breakdown cannot be made.",
};

export default function PendingWidget({
  title,
  caption,
  capability,
  /** Pixel height, so the widget keeps the place the pack laid out. */
  height,
  className = "",
}: {
  title: string;
  caption: string;
  capability: BiPendingCapability;
  height?: number;
  className?: string;
}) {
  return (
    <section
      className={`card flex flex-col gap-2 p-5 ${className}`}
      style={height ? { minHeight: height } : undefined}
    >
      <div className="flex items-start gap-2">
        <Clock size={15} className="mt-0.5 shrink-0 text-slate" aria-hidden />
        <h3 className="text-h3 text-navy">{title}</h3>
      </div>
      {caption.length > 0 && (
        <p className="text-caption leading-relaxed text-slate">{caption}</p>
      )}
      <p className="text-caption leading-relaxed text-slate">
        {OUTSTANDING[capability]} Nothing is shown in its place — an unmeasured
        figure is not a zero.
      </p>
    </section>
  );
}
