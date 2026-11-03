"use client";

/**
 * What a reader sees where a widget has nothing to draw.
 *
 * NEVER A ZERO, never an empty chart, never a flat line at the baseline. A
 * widget with no rows has not measured zero — it has not measured. Drawn as a
 * chart those two are indistinguishable, and the fabricated one clears every
 * floor, sits below every target and reads on a board pack as a real result.
 *
 * TWO ABSENCES, and the tile must not confuse them.
 *
 * When the widget's author NAMED the dataset its figure degrades without
 * (`BiPackWidget.needs_data`), the absence is stated by that dataset, with the
 * Data Engine route that accepts it. That turns a blank tile into the one
 * instruction that fixes it.
 *
 * When no dataset was named — `dataset` is `null` — the tile says it does not
 * know why it is empty. It used to fall back to "positions", and every query
 * widget with no `needs_data` then told a reader, on a date with no minted
 * official run, that the institution had not uploaded its positions. Positions
 * are pushed nightly; what was missing was the run. Asserting a cause the
 * platform does not know is the false statement `PendingWidget` exists to
 * prevent, arriving through a default. So the unknown case names no dataset,
 * offers no upload, and points at the surface that can answer "why": the
 * ingestion history.
 *
 * A third qualification rides on both: when the reader's access covers PART of
 * the book, an empty answer is not an empty book. `CoverageEmptyMeaning` says so
 * in the sentence `lib/api/dataScope.ts` composed, and only when the coverage is
 * known to be narrowed.
 *
 * The WIDGET's own title is carried too when the caller has one. A dashboard of
 * four gaps that all read "Needs data: Positions and balances" tells a reader
 * that something is missing and not which view is missing — and the title is the
 * pack's own words about a view this reader was GRANTED, so withholding it
 * protects nobody. A refusal is the other component entirely.
 */

import Link from "next/link";
import { ArrowRight, CircleHelp, Database } from "lucide-react";
import { CoverageEmptyMeaning } from "@/components/access/CoverageNotice";
import type { ReaderCoverage } from "@/lib/api/dataScope";
import { panelSurface } from "./labels";
import type { BiDatasetRequirement } from "./types";

/**
 * The platform surface that can say why a figure is missing. Resolved through
 * the same panel-surface map a pack uses, so the door here and the door on a
 * pack tile lead to one place.
 */
const WHY_SURFACE = panelSurface("ingestion_quality");

export default function NeedsDataWidget({
  dataset,
  /** The view this gap belongs to, in the words its author gave it. */
  title,
  caption,
  /** How much of the book the reader's access covers, when the caller knows. */
  coverage,
  /** Pixel height, so a missing widget keeps the layout the pack laid out. */
  height,
  className = "",
}: {
  dataset: BiDatasetRequirement | null;
  title?: string;
  caption?: string;
  coverage?: ReaderCoverage;
  height?: number;
  className?: string;
}) {
  return (
    <section
      className={`card flex flex-col items-center justify-center gap-2 p-6 text-center ${className}`}
      style={height ? { minHeight: height } : undefined}
    >
      <div className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-surface text-slate">
        {dataset ? (
          <Database size={16} aria-hidden />
        ) : (
          <CircleHelp size={16} aria-hidden />
        )}
      </div>
      {title && <h3 className="text-h3 text-navy">{title}</h3>}
      {caption && (
        <p className="max-w-xs text-caption leading-relaxed text-slate">
          {caption}
        </p>
      )}

      {dataset ? (
        <>
          <p className="text-body font-medium text-navy">
            Needs data: {dataset.label}
          </p>
          <p className="max-w-xs text-caption leading-relaxed text-slate">
            This view has nothing to show for the selected date. It is built
            from {dataset.label.toLowerCase()}; once that dataset is supplied,
            the figure appears.
          </p>
          {coverage && (
            <CoverageEmptyMeaning coverage={coverage} className="max-w-xs" />
          )}
          <Link
            href={dataset.href}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-caption font-medium text-action hover:bg-surface"
          >
            Open the Data Engine template
            <ArrowRight size={13} aria-hidden />
          </Link>
        </>
      ) : (
        <>
          <p className="text-body font-medium text-navy">
            No figure for this date
          </p>
          <p className="max-w-xs text-caption leading-relaxed text-slate">
            Nothing has been measured for the selected date, and this view
            cannot say why: the figure may not have been computed for this date
            yet, or the data behind it may not have arrived. Nothing is shown in
            its place — an unmeasured figure is not a zero.
          </p>
          {coverage && (
            <CoverageEmptyMeaning coverage={coverage} className="max-w-xs" />
          )}
          {WHY_SURFACE !== null && (
            <Link
              href={WHY_SURFACE.href}
              className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-caption font-medium text-action hover:bg-surface"
            >
              {WHY_SURFACE.label}
              <ArrowRight size={13} aria-hidden />
            </Link>
          )}
        </>
      )}
    </section>
  );
}
