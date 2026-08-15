'use client';

/**
 * Side-by-side plane comparison (spec §4 `/planes`, §5 Sources tab). The same
 * scope resolved under each available plane, in parallel columns, so the bank
 * sees exactly what each source would feed the engines before committing. The
 * selected plane's column is highlighted; each column header carries its
 * attribution + freshness; when overlays are enabled the composed delta is
 * summarised beneath. `available:false` planes render as a greyed, empty column.
 */

import { AlertTriangle, Layers } from 'lucide-react';
import StatusPill from '@/components/ui/StatusPill';
import { num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import {
  MARKET_DATA_SOURCES,
  SOURCE_LABELS,
  type MarketDataCategory,
  type MarketDataPlane,
  type MarketDataPlanesResponse,
  type OverlayDeltaEntry,
  type PlaneItemWire,
} from '@/lib/api/marketDataSources';
import { fmtAge } from './AttributionChip';
import { MonoChip } from './chips';
import { tenorLabel } from './CurveBoard';

type NormalizedItem = { key: string; label: string; sublabel?: string; value: string };

/** Pick the curve point nearest 12 months as the row's headline value. */
function headlineCurvePoint(item: PlaneItemWire): string {
  const points = Array.isArray(item.points) ? item.points : [];
  if (points.length === 0) return '—';
  const target = 12;
  let best = points[0];
  for (const point of points) {
    const bestGap = Math.abs((best.tenor_months ?? 0) - target);
    const gap = Math.abs((point.tenor_months ?? 0) - target);
    if (gap < bestGap) best = point;
  }
  const tenor = best.tenor_months ?? 0;
  return `${tenorLabel(tenor)} · ${fmtPct(num(best.rate) * 100)}`;
}

/** Category-specific item → { key, label, value } for the comparison pivot. */
function normalizeItem(category: MarketDataCategory, item: PlaneItemWire): NormalizedItem | null {
  if (category === 'rates') {
    const code = item.index_code;
    if (!code) return null;
    const scenario = item.scenario && item.scenario !== 'base' ? item.scenario : undefined;
    return {
      key: `${code}::${item.scenario ?? 'base'}`,
      label: code,
      sublabel: scenario,
      // Reference-rate index values are percent-valued — render as-is (spec §4).
      value: fmtPct(num(item.value)),
    };
  }
  if (category === 'fx') {
    if (!item.base || !item.quote) return null;
    return {
      key: `${item.base}/${item.quote}::${item.rate_type ?? ''}`,
      label: `${item.base}/${item.quote}`,
      sublabel: item.rate_type ?? undefined,
      value: num(item.rate).toLocaleString('en-US', {
        minimumFractionDigits: 4,
        maximumFractionDigits: 4,
      }),
    };
  }
  // curves
  if (!item.curve_name) return null;
  const points = Array.isArray(item.points) ? item.points.length : 0;
  return {
    key: item.curve_name,
    label: item.curve_name,
    sublabel: item.currency ? `${item.currency} · ${points} pts` : `${points} pts`,
    value: headlineCurvePoint(item),
  };
}

/** Value label for the headline column (curves show a 12M anchor point). */
function valueColumnLabel(category: MarketDataCategory): string {
  if (category === 'rates') return 'Rate';
  if (category === 'fx') return 'Spot';
  return '~12M point';
}

function PlaneColumnHeader({ plane }: { plane: MarketDataPlane }) {
  const attribution = plane.attribution;
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className="text-body font-semibold text-navy">{SOURCE_LABELS[plane.source]}</span>
        {plane.isSelected && <StatusPill tone="compliant">Selected</StatusPill>}
        {!plane.available && <StatusPill tone="slate">No data</StatusPill>}
      </div>
      {attribution && plane.available && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <MonoChip>{attribution.sourceSystem || '—'}</MonoChip>
          {attribution.stale ? (
            <StatusPill tone="amber">Stale · {fmtAge(attribution.ageSeconds)}</StatusPill>
          ) : (
            <span className="text-micro text-slate whitespace-nowrap">
              {fmtAge(attribution.ageSeconds) === 'now'
                ? 'just pulled'
                : `${fmtAge(attribution.ageSeconds)} old`}
            </span>
          )}
          {attribution.fellBack && (
            <span
              className="inline-flex items-center gap-1 text-micro text-warning whitespace-nowrap"
              title={`Requested ${attribution.requestedSource ?? '—'}, served ${
                attribution.servedSource ?? '—'
              }`}
            >
              <AlertTriangle size={11} aria-hidden />
              fell back
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function deltaText(entry: OverlayDeltaEntry): string {
  if (entry.delta_bps !== undefined) return `${num(entry.delta_bps).toFixed(1)} bps`;
  if (entry.delta !== undefined) return `${num(entry.delta) >= 0 ? '+' : ''}${num(entry.delta)}`;
  if (entry.adjusted !== undefined && entry.base !== undefined) {
    return `${num(entry.base)} → ${num(entry.adjusted)}`;
  }
  return '';
}

function deltaLabel(entry: OverlayDeltaEntry): string {
  if (entry.label) return entry.label;
  if (entry.scope) return entry.scope;
  if (entry.tenor_months !== undefined) return tenorLabel(entry.tenor_months);
  return 'adjustment';
}

export default function PlaneComparison({
  data,
  category,
}: {
  data: MarketDataPlanesResponse;
  category: MarketDataCategory;
}) {
  // Preserve the AequorOS → Bank → Vendor order; keep only planes present.
  const bySource = new Map(data.planes.map((plane) => [plane.source, plane]));
  const planes = MARKET_DATA_SOURCES.map((source) => bySource.get(source)).filter(
    (plane): plane is MarketDataPlane => plane !== undefined
  );

  // Pivot: union of row keys across planes, seeded from the selected plane so
  // the most relevant rows lead.
  const rowOrder: NormalizedItem[] = [];
  const seen = new Set<string>();
  const orderedPlanes = [...planes].sort(
    (a, b) => Number(b.isSelected) - Number(a.isSelected)
  );
  const perPlane = new Map<string, Map<string, NormalizedItem>>();
  for (const plane of orderedPlanes) {
    const map = new Map<string, NormalizedItem>();
    for (const raw of plane.items) {
      const normalized = normalizeItem(category, raw);
      if (!normalized) continue;
      map.set(normalized.key, normalized);
      if (!seen.has(normalized.key)) {
        seen.add(normalized.key);
        rowOrder.push(normalized);
      }
    }
    perPlane.set(plane.source, map);
  }

  const overlay = data.overlay;
  const showOverlay = data.overlayEnabled && overlay.available;

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full text-body border-collapse tnum">
          <thead>
            <tr className="border-b border-border bg-surface">
              <th className="text-left px-4 py-3 text-micro font-medium text-slate uppercase tracking-wider">
                {valueColumnLabel(category)}
              </th>
              {planes.map((plane) => (
                <th
                  key={plane.source}
                  className={`text-left px-4 py-3 align-top ${
                    plane.isSelected ? 'bg-action-light/40' : ''
                  }`}
                >
                  <PlaneColumnHeader plane={plane} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowOrder.length === 0 && (
              <tr>
                <td
                  colSpan={planes.length + 1}
                  className="px-4 py-8 text-center text-body text-slate"
                >
                  No servable values for this category at the as-of date.
                </td>
              </tr>
            )}
            {rowOrder.map((row) => (
              <tr key={row.key} className="border-b border-border-light">
                <td className="px-4 py-2.5 align-top">
                  <span className="font-mono text-caption text-navy">{row.label}</span>
                  {row.sublabel && (
                    <span className="block text-micro text-slate normal-case">{row.sublabel}</span>
                  )}
                </td>
                {planes.map((plane) => {
                  const cell = perPlane.get(plane.source)?.get(row.key);
                  return (
                    <td
                      key={plane.source}
                      className={`px-4 py-2.5 font-mono text-caption ${
                        plane.isSelected ? 'bg-action-light/30 text-navy font-medium' : 'text-navy/90'
                      }`}
                    >
                      {cell ? cell.value : <span className="text-slate-light">—</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showOverlay && (
        <div className="rounded-lg border border-border-light bg-surface px-4 py-3 space-y-2">
          <div className="flex items-center gap-2">
            <Layers size={14} className="text-action shrink-0" aria-hidden />
            <span className="text-caption font-medium text-navy">
              Overlay composed on the {SOURCE_LABELS[data.selectedSource]} plane
            </span>
          </div>
          {overlay.deltaPreview.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {overlay.deltaPreview.slice(0, 12).map((entry, index) => {
                const text = deltaText(entry);
                return (
                  <span
                    key={`${deltaLabel(entry)}-${index}`}
                    className="inline-flex items-center gap-1.5 rounded border border-action/30 bg-action-light px-2 py-0.5 text-micro text-action"
                  >
                    <span className="font-mono">{deltaLabel(entry)}</span>
                    {text && <span className="tnum">{text}</span>}
                  </span>
                );
              })}
            </div>
          ) : (
            <p className="text-micro text-slate">
              Your private spread is applied at read time on top of the selected plane.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
