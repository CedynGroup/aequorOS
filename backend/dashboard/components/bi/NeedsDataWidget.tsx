"use client";

/**
 * What a reader sees where a widget has nothing to draw.
 *
 * NEVER A ZERO, never an empty chart, never a flat line at the baseline. A
 * widget with no rows has not measured zero — it has not measured. Drawn as a
 * chart those two are indistinguishable, and the fabricated one clears every
 * floor, sits below every target and reads on a board pack as a real result.
 *
 * So the absence is stated in words, named by the DATASET the institution has
 * not supplied yet, with the Data Engine route that accepts it. That turns a
 * blank tile into the one instruction that fixes it.
 *
 * The WIDGET's own title is carried too when the caller has one. A dashboard of
 * four gaps that all read "Needs data: Positions and balances" tells a reader
 * that something is missing and not which view is missing — and the title is the
 * pack's own words about a view this reader was GRANTED, so withholding it
 * protects nobody. A refusal is the other component entirely.
 */

import Link from "next/link";
import { ArrowRight, Database } from "lucide-react";
import type { BiDatasetRequirement } from "./types";

export default function NeedsDataWidget({
  dataset,
  /** The view this gap belongs to, in the words its author gave it. */
  title,
  caption,
  /** Pixel height, so a missing widget keeps the layout the pack laid out. */
  height,
  className = "",
}: {
  dataset: BiDatasetRequirement;
  title?: string;
  caption?: string;
  height?: number;
  className?: string;
}) {
  return (
    <section
      className={`card flex flex-col items-center justify-center gap-2 p-6 text-center ${className}`}
      style={height ? { minHeight: height } : undefined}
    >
      <div className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-surface text-slate">
        <Database size={16} aria-hidden />
      </div>
      {title && <h3 className="text-h3 text-navy">{title}</h3>}
      {caption && (
        <p className="max-w-xs text-caption leading-relaxed text-slate">
          {caption}
        </p>
      )}
      <p className="text-body font-medium text-navy">
        Needs data: {dataset.label}
      </p>
      <p className="max-w-xs text-caption leading-relaxed text-slate">
        This view has nothing to show for the selected date because this
        institution has not supplied {dataset.label.toLowerCase()} for it.
      </p>
      <Link
        href={dataset.href}
        className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-caption font-medium text-action hover:bg-surface"
      >
        Open the Data Engine template
        <ArrowRight size={13} aria-hidden />
      </Link>
    </section>
  );
}
