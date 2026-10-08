"use client";

/**
 * The reporting window and the filters a BI view is read under.
 *
 * Two rules the bar follows because the server does.
 *
 * A FILTER IS A READ. "Total exposure where the counterparty is X" discloses X
 * as surely as grouping by the name would, so a filter is authorized exactly
 * like a requested measure. The bar therefore offers only dimensions that came
 * back in THIS caller's catalogue, which the server has already filtered member
 * by member — a dimension a principal cannot read is never offered, so a filter
 * the bar can build is a filter the query path will serve.
 *
 * Only dimensions with a CLOSED vocabulary are offered. A free-text filter is a
 * probe: typing names into `contains` and watching which ones change a total is
 * how an aggregate view is turned into a lookup. Typed value entry belongs with
 * the self-service grid, under its own authority.
 */

import { useMemo, useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import type {
  BiCatalogueDimensionRead,
  BiCatalogueRead,
  BiFilter,
} from "@aequoros/risk-service-api";

import { narrowingFieldsFor } from "./query";
import type { ReactNode } from "react";

function valueLabel(
  dimension: BiCatalogueDimensionRead | undefined,
  code: unknown,
): string {
  const match = (dimension?.values ?? []).find(
    (value) => value.code === String(code),
  );
  return match ? match.label : String(code);
}

export default function FilterBar({
  catalogue,
  asOf,
  onAsOfChange,
  compareTo = null,
  onCompareToChange,
  filters,
  onFiltersChange,
  actions,
  widgetMeasures = [],
}: {
  catalogue: BiCatalogueRead | undefined;
  /**
   * The measures of each widget on the page, when the bar is narrowing a
   * PUBLISHED dashboard. Given them, the bar offers only fields at least one of
   * those widgets can be sliced by. Left empty — Explore, where the reader is
   * building the question — it offers the reader's whole catalogue.
   */
  widgetMeasures?: readonly (readonly string[])[];
  /** The reporting date, ISO `YYYY-MM-DD`. */
  asOf: string;
  onAsOfChange: (asOf: string) => void;
  /** The prior date every measure is compared with, when one is chosen. */
  compareTo?: string | null;
  onCompareToChange?: (compareTo: string | null) => void;
  filters: readonly BiFilter[];
  onFiltersChange: (filters: BiFilter[]) => void;
  actions?: ReactNode;
}) {
  const dimensions = useMemo(
    () => narrowingFieldsFor(catalogue, widgetMeasures),
    [catalogue, widgetMeasures],
  );
  const [pendingDimension, setPendingDimension] = useState("");

  const pending = dimensions.find(
    (dimension) => dimension.id === pendingDimension,
  );

  const addFilter = (member: string, code: string) => {
    onFiltersChange([
      ...filters.filter((filter) => filter.member !== member),
      { member, op: "eq", values: [code] },
    ]);
    setPendingDimension("");
  };

  const removeFilter = (member: string) => {
    onFiltersChange(filters.filter((filter) => filter.member !== member));
  };

  return (
    <div className="card flex flex-wrap items-end gap-4 px-5 py-4">
      <label className="flex flex-col gap-1">
        <span className="text-micro font-medium uppercase tracking-wider text-slate">
          Reporting date
        </span>
        <input
          type="date"
          value={asOf}
          onChange={(event) => onAsOfChange(event.target.value)}
          className="rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy"
        />
      </label>

      {onCompareToChange && (
        <label className="flex flex-col gap-1">
          <span className="text-micro font-medium uppercase tracking-wider text-slate">
            Compare with
          </span>
          <input
            type="date"
            value={compareTo ?? ""}
            onChange={(event) => onCompareToChange(event.target.value || null)}
            className="rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy"
          />
        </label>
      )}

      {dimensions.length > 0 && (
        <div className="flex items-end gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">
              Narrow by
            </span>
            <select
              value={pendingDimension}
              onChange={(event) => setPendingDimension(event.target.value)}
              className="rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy"
            >
              <option value="">Choose a field…</option>
              {dimensions.map((dimension) => (
                <option key={dimension.id} value={dimension.id}>
                  {dimension.label}
                </option>
              ))}
            </select>
          </label>

          {pending && (
            <label className="flex flex-col gap-1">
              <span className="text-micro font-medium uppercase tracking-wider text-slate">
                {pending.label}
              </span>
              <select
                value=""
                onChange={(event) => {
                  if (event.target.value) {
                    addFilter(pending.id, event.target.value);
                  }
                }}
                className="rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy"
              >
                <option value="">Choose a value…</option>
                {(pending.values ?? []).map((value) => (
                  <option key={value.code} value={value.code}>
                    {value.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      {filters.length > 0 && (
        <ul className="flex flex-wrap items-center gap-1.5">
          {filters.map((filter) => {
            const dimension = dimensions.find(
              (entry) => entry.id === filter.member,
            );
            return (
              <li key={filter.member}>
                <span className="inline-flex items-center gap-1.5 rounded-sm border border-action/20 bg-action-light px-2 py-1 text-caption text-action">
                  <SlidersHorizontal size={11} aria-hidden />
                  {dimension?.label ?? filter.member}:{" "}
                  {valueLabel(dimension, (filter.values ?? [])[0])}
                  <button
                    type="button"
                    aria-label={`Remove the ${
                      dimension?.label ?? filter.member
                    } filter`}
                    onClick={() => removeFilter(filter.member)}
                    className="rounded-sm hover:bg-action/10"
                  >
                    <X size={11} aria-hidden />
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {actions && (
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
