"use client";

/**
 * How the rows of a self-service answer are shaped: subtotals, one field across
 * the columns, and how many groups to keep.
 *
 * EVERY CONTROL HERE CAN ONLY BUILD A LEGAL QUESTION. The compiler refuses a
 * malformed one with a 422, so a control that could express a refused
 * combination would be a control that produces an error message instead of an
 * answer. So the offer itself is narrowed:
 *
 * * the column field is a SINGLE choice, drawn from `pivotableDimensions` — the
 *   fields these measures can be sliced by whose published vocabulary fits
 *   inside the server's column cap. The compiler REFUSES a pivot whose field
 *   has more values in the reporting window than the cap rather than dropping
 *   columns, so a wider field could only ever fail; when one is excluded the
 *   reason is stated rather than the field simply going missing;
 * * the column field is never also a row field, and never the Top-N field;
 * * Top-N is chosen from the ROW fields only, and its count is bounded by the
 *   server's own maximum;
 * * subtotals need at least one row field to roll up, so the toggle is
 *   unavailable without one, with the reason on screen.
 *
 * The number of columns a pivot may emit is deliberately not a control. It is
 * fixed at the server's cap, because asking for fewer columns than the field has
 * values is the one thing that turns a legal pivot into a refusal.
 */

import type { BiCatalogueDimensionRead } from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import {
  BI_PIVOT_COLUMN_CAP,
  BI_TOP_N_CAP,
  hasConcentrationMeasure,
  pivotableDimensions,
  sliceableDimensions,
  type ExploreCatalogue,
  type ExploreShape,
} from "./exploreQuery";

const CONTROL_CLASS =
  "rounded-md border border-border bg-surface px-2 py-1 text-caption text-navy";

export default function GridShapeControls({
  catalogue,
  shape,
  onShapeChange,
}: {
  catalogue: ExploreCatalogue;
  shape: ExploreShape;
  onShapeChange: (shape: ExploreShape) => void;
}) {
  const rowFields = shape.dimensions
    .map((id) => catalogue.dimensions.find((entry) => entry.id === id))
    .filter((entry): entry is BiCatalogueDimensionRead => Boolean(entry));

  const concentration = hasConcentrationMeasure(catalogue, shape.measures);
  const pivotable = pivotableDimensions(catalogue, shape.measures).filter(
    (dimension) => !shape.dimensions.includes(dimension.id),
  );
  const excludedFromPivot = sliceableDimensions(
    catalogue,
    shape.measures,
  ).filter((dimension) => {
    const size = (dimension.values ?? []).length;
    return size === 0 || size > BI_PIVOT_COLUMN_CAP;
  });

  const topNCandidates = rowFields.filter(
    (dimension) => dimension.id !== shape.pivot,
  );

  const update = (patch: Partial<ExploreShape>) => {
    onShapeChange({ ...shape, ...patch });
  };

  return (
    <SectionCard
      title="Shape the grid"
      subtitle="Roll the rows up, spread one field across the columns, or keep only the largest groups."
    >
      <div className="space-y-4">
        <div className="space-y-1">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={shape.subtotals && rowFields.length > 0}
              disabled={rowFields.length === 0}
              onChange={(event) =>
                update({ subtotals: event.currentTarget.checked })
              }
            />
            <span className="min-w-0">
              <span className="block text-body text-navy">
                Show subtotals for each group
              </span>
              <span className="block text-caption text-slate">
                {rowFields.length === 0
                  ? "Choose at least one field to break the answer down by first — there is nothing to total."
                  : "The server computes each subtotal from the same book as the rows, so a total always agrees with the rows above it."}
              </span>
            </span>
          </label>
        </div>

        <div className="space-y-1">
          <label
            className="block text-micro font-medium uppercase tracking-wider text-slate"
            htmlFor="bi-grid-pivot"
          >
            Spread across the columns
          </label>
          <select
            id="bi-grid-pivot"
            className={CONTROL_CLASS}
            value={shape.pivot ?? ""}
            disabled={concentration || pivotable.length === 0}
            onChange={(event) => {
              const chosen = event.currentTarget.value;
              update({
                pivot: chosen === "" ? null : chosen,
                topN:
                  shape.topN && shape.topN.dimension === chosen
                    ? null
                    : shape.topN,
              });
            }}
          >
            <option value="">Nothing — one column per measure</option>
            {pivotable.map((dimension) => (
              <option key={dimension.id} value={dimension.id}>
                {dimension.label}
              </option>
            ))}
          </select>
          <p className="text-caption text-slate">
            {concentration
              ? "A concentration measure is one figure for the whole slice, so it cannot be spread across columns."
              : pivotable.length === 0
                ? "No field these measures can be split by has a short enough list of values to become columns."
                : `Up to ${BI_PIVOT_COLUMN_CAP} columns are shown. A field already used for the rows is not offered here.`}
          </p>
          {excludedFromPivot.length > 0 && !concentration && (
            <p className="text-caption text-slate">
              {excludedFromPivot.map((entry) => entry.label).join(", ")}{" "}
              {excludedFromPivot.length === 1 ? "has" : "have"} too many values
              to become columns. Filter first, or break the answer down by{" "}
              {excludedFromPivot.length === 1 ? "it" : "them"} in the rows.
            </p>
          )}
        </div>

        <div className="space-y-1">
          <label
            className="block text-micro font-medium uppercase tracking-wider text-slate"
            htmlFor="bi-grid-top-n-field"
          >
            Keep only the largest groups
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <select
              id="bi-grid-top-n-field"
              className={CONTROL_CLASS}
              value={shape.topN?.dimension ?? ""}
              disabled={topNCandidates.length === 0}
              onChange={(event) => {
                const chosen = event.currentTarget.value;
                update({
                  topN:
                    chosen === ""
                      ? null
                      : {
                          dimension: chosen,
                          n: shape.topN?.n ?? 10,
                          other: shape.topN?.other ?? true,
                        },
                });
              }}
            >
              <option value="">Every group</option>
              {topNCandidates.map((dimension) => (
                <option key={dimension.id} value={dimension.id}>
                  {dimension.label}
                </option>
              ))}
            </select>
            {shape.topN && (
              <>
                <input
                  type="number"
                  aria-label="How many groups to keep"
                  className={`${CONTROL_CLASS} w-20`}
                  min={1}
                  max={BI_TOP_N_CAP}
                  value={shape.topN.n}
                  onChange={(event) => {
                    const asked = Number(event.currentTarget.value);
                    const kept = Number.isFinite(asked)
                      ? Math.min(BI_TOP_N_CAP, Math.max(1, Math.trunc(asked)))
                      : 1;
                    update({
                      topN: shape.topN
                        ? { ...shape.topN, n: kept }
                        : shape.topN,
                    });
                  }}
                />
                <label className="flex items-center gap-1.5 text-caption text-slate">
                  <input
                    type="checkbox"
                    checked={shape.topN.other}
                    onChange={(event) =>
                      update({
                        topN: shape.topN
                          ? { ...shape.topN, other: event.currentTarget.checked }
                          : shape.topN,
                      })
                    }
                  />
                  Collect the rest into one row
                </label>
              </>
            )}
          </div>
          <p className="text-caption text-slate">
            {topNCandidates.length === 0
              ? "Choose a field to break the answer down by first — the largest groups are groups of something."
              : `Between 1 and ${BI_TOP_N_CAP} groups, ranked by the first measure.`}
          </p>
        </div>
      </div>
    </SectionCard>
  );
}
