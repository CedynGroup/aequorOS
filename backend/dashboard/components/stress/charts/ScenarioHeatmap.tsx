'use client';

/**
 * Scenario × metric heatmap / severity ladder (docs/stress.md §4 item 3 — "new
 * component; there is none today"). A CSS-grid matrix: one row per scenario (or
 * run), one column per headline metric, each cell shaded by its
 * distance-to-threshold so the board reads the whole scenario set at a glance and
 * the rows sort into a severity ladder (worst CAR erosion first).
 *
 * Token-native: cells shade the risk-semantic success/warning/critical tokens by
 * opacity; no raw hex, both themes.
 */

import type { ReactNode } from 'react';

export type HeatCell = {
  /** Formatted display value (e.g. "12.4%"). */
  display: string;
  /** 0 = comfortably safe … 1 = breach. Drives the shading. */
  intensity: number;
  /** breach flips the cell to the critical ramp regardless of intensity. */
  breach?: boolean;
  title?: string;
};

export type HeatColumn = { key: string; label: string; sub?: string };

export type HeatRow = {
  key: string;
  label: string;
  sublabel?: string;
  badge?: ReactNode;
  cells: Record<string, HeatCell | undefined>;
  onClick?: () => void;
  active?: boolean;
};

function cellStyle(cell: HeatCell | undefined): { background: string; color: string } {
  if (!cell) return { background: 'transparent', color: 'rgb(var(--text-faint))' };
  const t = Math.max(0, Math.min(1, cell.intensity));
  if (cell.breach || t >= 0.66) {
    return { background: `rgb(var(--crit) / ${(0.14 + t * 0.34).toFixed(3)})`, color: 'rgb(var(--heading))' };
  }
  if (t >= 0.33) {
    return { background: `rgb(var(--warn) / ${(0.12 + t * 0.3).toFixed(3)})`, color: 'rgb(var(--heading))' };
  }
  return { background: `rgb(var(--ok) / ${(0.1 + (0.33 - t) * 0.4).toFixed(3)})`, color: 'rgb(var(--heading))' };
}

export default function ScenarioHeatmap({
  columns,
  rows,
}: {
  columns: HeatColumn[];
  rows: HeatRow[];
}) {
  const template = `minmax(180px, 1.4fr) repeat(${columns.length}, minmax(96px, 1fr))`;
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[640px]">
        {/* Header */}
        <div className="grid gap-px" style={{ gridTemplateColumns: template }}>
          <div className="px-3 py-2 text-micro font-medium uppercase tracking-wider text-slate">
            Scenario
          </div>
          {columns.map((c) => (
            <div key={c.key} className="px-3 py-2 text-right">
              <div className="text-micro font-medium uppercase tracking-wider text-slate">{c.label}</div>
              {c.sub && <div className="text-micro text-slate-light">{c.sub}</div>}
            </div>
          ))}
        </div>
        {/* Rows */}
        <div className="space-y-px">
          {rows.map((row) => (
            <div
              key={row.key}
              className={`grid gap-px rounded-md ${row.onClick ? 'cursor-pointer' : ''} ${
                row.active ? 'ring-1 ring-action' : ''
              }`}
              style={{ gridTemplateColumns: template }}
              onClick={row.onClick}
              role={row.onClick ? 'button' : undefined}
              tabIndex={row.onClick ? 0 : undefined}
              onKeyDown={(e) => {
                if (row.onClick && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault();
                  row.onClick();
                }
              }}
            >
              <div className="px-3 py-2.5 flex flex-col justify-center bg-surface rounded-l-md min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-body font-medium text-navy truncate">{row.label}</span>
                  {row.badge}
                </div>
                {row.sublabel && <span className="text-micro text-slate truncate">{row.sublabel}</span>}
              </div>
              {columns.map((c) => {
                const cell = row.cells[c.key];
                const style = cellStyle(cell);
                return (
                  <div
                    key={c.key}
                    className="px-3 py-2.5 text-right tnum text-body flex items-center justify-end"
                    style={style}
                    title={cell?.title}
                  >
                    {cell?.display ?? '—'}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
