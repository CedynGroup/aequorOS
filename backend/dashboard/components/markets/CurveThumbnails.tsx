'use client';

/**
 * Overview curve thumbnails: a compact card per published curve — a mini
 * sparkline of the curve shape (rate ×100), the short/long anchor rates, and
 * source freshness. Clicking a card jumps to the Curves tab for the full
 * workbench. Read-only headline board (spec §5 Overview).
 */

import type { YieldCurveViewRead } from '@aequoros/risk-service-api';
import Sparkline from '@/components/ui/Sparkline';
import { num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import AttributionChip from './AttributionChip';
import { CurveTypeBadge, MonoChip } from './chips';
import { tenorLabel } from './CurveBoard';

function CurveThumbnail({
  curve,
  onOpen,
}: {
  curve: YieldCurveViewRead;
  onOpen?: (curveName: string) => void;
}) {
  const sorted = [...curve.points].sort((a, b) => a.tenorMonths - b.tenorMonths);
  const series = sorted.map((point) => num(point.rate) * 100);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];

  return (
    <button
      type="button"
      onClick={onOpen ? () => onOpen(curve.curveName) : undefined}
      className={`grid w-full grid-cols-[minmax(11rem,1.25fr)_5rem_6rem_6rem_6rem_minmax(10rem,1fr)] items-center gap-3 px-4 py-3 text-left border-t border-border-light first:border-t-0 ${
        onOpen ? 'hover:bg-surface/60 transition-colors cursor-pointer' : ''
      }`}
    >
      <div className="min-w-0">
        <span className="block font-mono text-caption text-navy truncate">{curve.curveName}</span>
        <span className="mt-1 inline-flex"><CurveTypeBadge curveType={curve.curveType} /></span>
      </div>
      <MonoChip>{curve.currency}</MonoChip>
      <span className="font-mono text-caption text-right text-navy">
        {first ? `${tenorLabel(first.tenorMonths)} ${fmtPct(num(first.rate) * 100)}` : '—'}
      </span>
      <span className="font-mono text-caption text-right text-navy">
        {last ? `${tenorLabel(last.tenorMonths)} ${fmtPct(num(last.rate) * 100)}` : '—'}
      </span>
      <div className="justify-self-end w-20">
        {series.length > 1 && <Sparkline data={series} color="rgb(var(--accent))" />}
      </div>
      <AttributionChip attribution={curve.attribution} className="justify-end" />
    </button>
  );
}

export default function CurveThumbnails({
  curves,
  onOpen,
}: {
  curves: YieldCurveViewRead[];
  onOpen?: (curveName: string) => void;
}) {
  const sorted = [...curves].sort((a, b) => a.curveName.localeCompare(b.curveName));
  return (
    <div className="overflow-x-auto border border-border rounded-lg bg-surface-raised">
      <div className="min-w-[48rem]">
        <div className="grid grid-cols-[minmax(11rem,1.25fr)_5rem_6rem_6rem_6rem_minmax(10rem,1fr)] gap-3 px-4 py-2.5 bg-surface/60 text-micro font-medium uppercase tracking-wider text-slate">
          <span>Curve</span>
          <span>CCY</span>
          <span className="text-right">Short end</span>
          <span className="text-right">Long end</span>
          <span className="text-right">Shape</span>
          <span className="text-right">Source</span>
        </div>
        {sorted.map((curve) => (
          <CurveThumbnail key={curve.curveName} curve={curve} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}
