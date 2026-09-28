"use client";

/**
 * The sentence that tells a reader the figures beside it cover part of a book.
 *
 * Phase 4 made a grant's data scope real, and the backend honours it everywhere:
 * a branch- or region-scoped reader's rows, totals, pages, facets and counts are
 * all computed over their slice. Nothing in the browser said so, so a scoped
 * total was drawn exactly as the institution's own book would be — and a number
 * on a screen is what gets quoted into a board paper.
 *
 * A BANNER, NOT A BADGE, and it sits ABOVE the figures it qualifies. A badge is
 * a thing a reader can miss; the qualification also rides on each figure's own
 * label through `coverageFigureLabel`, so the two disclosures are independent and
 * a reader who skips one still meets the other.
 *
 * It renders NOTHING while the coverage is still being loaded — an access
 * warning that flashes on every page load is a warning nobody reads — and it
 * renders the fail-closed sentence whenever the coverage could not be
 * established. Deciding which of those applies is `lib/api/dataScope.ts`'s job,
 * not this component's: everything here is copy the module already composed, so
 * no surface can word one coverage differently from another.
 */

import { Layers } from "lucide-react";
import { showsCoverageNotice, type ReaderCoverage } from "@/lib/api/dataScope";

export default function CoverageNotice({
  coverage,
  className = "",
}: {
  coverage: ReaderCoverage;
  className?: string;
}) {
  if (!showsCoverageNotice(coverage)) return null;
  return (
    <section
      // `status`, not `alert`: it describes what is on screen rather than
      // interrupting, and it is announced when the coverage changes.
      role="status"
      aria-live="polite"
      className={`flex items-start gap-2.5 rounded-md border border-warn/30 bg-warn-light px-3 py-2.5 ${className}`}
      data-testid="coverage-notice"
      data-coverage-determined={coverage.determined ? "yes" : "no"}
    >
      <Layers size={15} className="mt-0.5 shrink-0 text-slate" aria-hidden />
      <div className="min-w-0 space-y-1">
        <p className="text-caption text-navy">{coverage.statement}</p>
        {coverage.detail && (
          <p className="text-caption text-slate">{coverage.detail}</p>
        )}
        {coverage.correction && (
          <p className="text-caption text-slate">{coverage.correction}</p>
        )}
      </div>
    </section>
  );
}

/**
 * The same coverage stated where an answer came back EMPTY.
 *
 * An empty result under a narrowed scope is not an empty book, and the two are
 * indistinguishable on screen — which is the "missing data is never zero" rule
 * applied to access rather than to ingestion. A surface whose empty state says
 * "this institution has none" must say this instead.
 */
export function CoverageEmptyMeaning({
  coverage,
  className = "",
}: {
  coverage: ReaderCoverage;
  className?: string;
}) {
  if (!coverage.qualified || coverage.pending || !coverage.emptyMeaning) {
    return null;
  }
  return (
    <p
      className={`text-caption text-slate ${className}`}
      data-testid="coverage-empty-meaning"
    >
      {coverage.emptyMeaning}
    </p>
  );
}
