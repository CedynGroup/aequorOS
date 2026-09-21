"use client";

/**
 * Table 5 in the regulator's own orientation: one row per risk type, one column
 * per basis the return asks for.
 *
 * Every cell is a string the server already formatted in the unit it declares
 * (`table5.unit`), so this component performs no conversion, no scaling and no
 * rounding of a filing figure.
 *
 * A null cell prints "Not modelled" (D-009). It is the single most important
 * rule on this grid: a `0` in a Pillar 2 column asserts that the bank measured
 * the risk and found it to need no capital, which is a different — and far more
 * dangerous — statement than "the bank has not modelled it".
 */

import SectionCard from "@/components/ui/SectionCard";
import type { IcaapTable5 } from "@/lib/api/icaapRiskCapital";
import { NOT_MODELLED } from "./labels";

export default function Table5Grid({
  table5,
}: {
  table5: IcaapTable5 | null | undefined;
}) {
  const columns = table5?.columns ?? [];
  const rows = table5?.rows ?? [];
  const notes = table5?.notes ?? [];
  const partialRows = table5?.partialRows ?? [];
  return (
    <SectionCard
      title="Table 5 — Pillar 2 capital by risk type"
      subtitle={
        table5?.unit ? `Figures in ${table5.unit}.` : "Figures as the return states them."
      }
      noPadding
    >
      <div className="overflow-x-auto">
        <table className="w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-caption text-slate">
              <th scope="col" className="px-4 py-2 text-left">
                Risk type
              </th>
              {columns.map((column) => (
                <th key={column.key} scope="col" className="px-4 py-2 text-right">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const partial = partialRows.includes(row.key);
              return (
                <tr
                  key={row.key}
                  className="border-b border-border-light/60 align-top"
                >
                  <th scope="row" className="px-4 py-2 text-left font-normal">
                    {row.label}
                    {partial && (
                      <span className="ml-2 text-caption text-warning">
                        incomplete
                      </span>
                    )}
                  </th>
                  {columns.map((column) => {
                    const cell = (row.cells ?? {})[column.key] ?? null;
                    return (
                      <td
                        key={column.key}
                        className={`px-4 py-2 text-right tnum ${
                          cell === null ? "text-slate" : "text-navy"
                        }`}
                      >
                        {cell === null ? NOT_MODELLED : cell}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {notes.length > 0 && (
        <ul className="space-y-1 px-4 py-3 text-caption text-slate">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      <p className="px-4 pb-3 text-caption text-slate">
        &ldquo;{NOT_MODELLED}&rdquo; means the bank has not quantified that risk
        under Pillar 2. It is not a zero requirement.
      </p>
    </SectionCard>
  );
}
