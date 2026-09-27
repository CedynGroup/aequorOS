"use client";

/**
 * The action on a table row that reaches the rows behind its figure.
 *
 * It renders only what `components/bi/drill.ts` says can be honoured exactly —
 * one link per destination that can reproduce the row's slice, and nothing at
 * all when none can. There is deliberately no disabled control and no
 * explanation of what is missing: an action that cannot be trusted to land on
 * the figure's own rows should not be on the screen, and the reader is not owed
 * a lecture about the catalogue.
 */

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { DrillDestination } from "./drill";

/** The short form of each destination's label for a dense table cell. */
const CELL_LABELS: Readonly<Record<DrillDestination["id"], string>> = {
  loan_book: "Loan Book",
  positions: "Positions",
};

export default function DrillAction({
  destinations,
  rowLabel,
}: {
  destinations: readonly DrillDestination[];
  /** The group this row measures, for the link's accessible name. */
  rowLabel: string;
}) {
  if (destinations.length === 0) return null;
  return (
    <span className="inline-flex items-center justify-end gap-2">
      {destinations.map((destination) => (
        <Link
          key={destination.id}
          href={destination.href}
          aria-label={`${destination.label} for ${rowLabel}`}
          className="inline-flex items-center gap-1 whitespace-nowrap text-caption font-medium text-action hover:underline"
        >
          {CELL_LABELS[destination.id]}
          <ArrowUpRight size={12} aria-hidden />
        </Link>
      ))}
    </span>
  );
}
