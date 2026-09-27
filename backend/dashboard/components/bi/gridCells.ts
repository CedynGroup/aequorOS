/**
 * The copy the self-service grid adds on top of a formatted cell.
 *
 * VALUE TYPES ARE NOT DECIDED HERE. `./result` is the one module that turns a
 * catalogue value type into text, and the grid calls it — `formatCell` and
 * `columnUnit` are re-exported below so a reader of this file can see that the
 * grid has no formatter of its own. A grid that formatted independently would
 * eventually disagree with the chart beside it and with the export taken from
 * it about the same member, which is exactly the failure the catalogue's split
 * of `ratio` into `fraction` / `index` / `duration_years` was meant to end.
 *
 * What IS here is the wording a paged, rolled-up answer needs and a chart does
 * not: what a truncated page says about itself, and how a subtotal row names
 * the group it totals.
 */

import { fmtInt } from "../../lib/format";
import { columnUnit, formatCell, isNumericFormat } from "./result";

export { columnUnit, formatCell, isNumericFormat };

/**
 * The message a reader sees when an answer hit the server's page cap.
 *
 * A silently truncated grid is a wrong answer: the reader believes they are
 * looking at the whole book. So the notice names how many rows are shown and
 * every honest way to see the rest.
 */
export function truncationNotice(rowsShown: number): string {
  return (
    `Showing the first ${fmtInt(rowsShown)} rows of a larger answer. ` +
    "Narrow the question with a filter, keep only the largest groups, " +
    "or take it out through Export to see all of it."
  );
}

/**
 * How a rolled-up row reads. Level 0 is the whole institution; a deeper level is
 * the subtotal of the field at that depth, named by its own value.
 */
export function subtotalLabel(level: number, deepestValue: string): string {
  if (level <= 0) return "Total for the institution";
  if (deepestValue === "") return "Subtotal";
  return `${deepestValue} — subtotal`;
}
