'use client';

/**
 * Overlay editor drawer (spec §9, §11b): per-tenor basis-point spreads by
 * component tag on one published curve, with a live stacked base+premium
 * preview (TransferCurveChart idiom) and the arithmetic spelled out
 * transparently ("base 4.20% + 25 bps TLP + 25 bps liquidity = 4.70%").
 *
 * Composition is server-side at read time; this drawer only writes overlay
 * records. The preview mirrors the server formula:
 *   adjusted = base × Π(multiplicative) + Σ(additive bps)/10000 + Σ(fixed).
 * Only this bank's overlays are ever shown — other banks' overlays are
 * invisible by construction (RLS-forced tenant isolation).
 */

import { useMemo, useState } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { X } from 'lucide-react';
import type {
  MarketDataOverlayRead,
  YieldCurveViewRead,
} from '@aequoros/risk-service-api';
import {
  useCreateMarketDataOverlay,
  useEndMarketDataOverlay,
  useMarketDataOverlays,
} from '@/lib/api/hooks';
import {
  CHART_ACCENT,
  CHART_GRID,
  axisProps,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import { tenorLabel } from './CurveBoard';
import { CurveTypeBadge, MonoChip, SyntheticProxyBadge } from './chips';

const COMPONENT_TAGS = [
  'term_liquidity_premium',
  'liquidity_premium',
  'funding_spread',
  'credit_spread',
  'other',
] as const;

type ComponentTag = (typeof COMPONENT_TAGS)[number];

const TAG_LABELS: Record<ComponentTag, string> = {
  term_liquidity_premium: 'Term liquidity premium',
  liquidity_premium: 'Liquidity premium',
  funding_spread: 'Funding spread',
  credit_spread: 'Credit spread',
  other: 'Other',
};

/** Short names for the transparent-arithmetic line. */
const TAG_SHORT: Record<ComponentTag, string> = {
  term_liquidity_premium: 'TLP',
  liquidity_premium: 'liquidity',
  funding_spread: 'funding',
  credit_spread: 'credit',
  other: 'other',
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

type PendingSpread = {
  componentTag: ComponentTag;
  tenorKey: string; // 'flat' or the tenor months as a string
  bps: string;
  effectiveFrom: string;
  note: string;
};

type OverlayLike = {
  componentTag: string;
  adjustmentType: string;
  value: number;
  tenorMonths: number | null;
};

function appliesTo(overlay: OverlayLike, tenor: number): boolean {
  return overlay.tenorMonths === null || overlay.tenorMonths === tenor;
}

/** Server composition formula, mirrored for the live preview. */
function composeRate(base: number, overlays: OverlayLike[], tenor: number): number {
  let rate = base;
  for (const o of overlays) {
    if (appliesTo(o, tenor) && o.adjustmentType === 'multiplicative') rate *= o.value;
  }
  for (const o of overlays) {
    if (!appliesTo(o, tenor)) continue;
    if (o.adjustmentType === 'additive_bps') rate += o.value / 10_000;
    else if (o.adjustmentType === 'fixed') rate += o.value;
  }
  return rate;
}

export default function OverlayDrawer({
  bankId,
  bankName,
  curve,
  onClose,
}: {
  bankId: string;
  bankName: string;
  curve: YieldCurveViewRead;
  onClose: () => void;
}) {
  const overlaysQuery = useMarketDataOverlays(bankId, {
    baseCurveName: curve.curveName,
  });
  const createOverlay = useCreateMarketDataOverlay(bankId);
  const endOverlay = useEndMarketDataOverlay(bankId);

  const [pending, setPending] = useState<PendingSpread>({
    componentTag: 'liquidity_premium',
    tenorKey: 'flat',
    bps: '',
    effectiveFrom: todayIso(),
    note: '',
  });

  const overlaysData = overlaysQuery.data;
  const activeOverlays: MarketDataOverlayRead[] = useMemo(
    () => overlaysData?.overlays ?? [],
    [overlaysData]
  );

  const pendingBps = Number(pending.bps);
  const hasPending = pending.bps.trim() !== '' && Number.isFinite(pendingBps) && pendingBps !== 0;

  const effectiveOverlays: OverlayLike[] = useMemo(() => {
    const existing = activeOverlays.map((overlay) => ({
      componentTag: overlay.componentTag,
      adjustmentType: overlay.adjustmentType,
      value: num(overlay.value),
      tenorMonths: overlay.tenorMonths ?? null,
    }));
    if (!hasPending) return existing;
    return [
      ...existing,
      {
        componentTag: pending.componentTag,
        adjustmentType: 'additive_bps',
        value: pendingBps,
        tenorMonths: pending.tenorKey === 'flat' ? null : Number(pending.tenorKey),
      },
    ];
  }, [activeOverlays, hasPending, pending.componentTag, pending.tenorKey, pendingBps]);

  // Stacked preview: base band + one additive band per component tag, with
  // the fully-composed adjusted rate traced as a line along the top.
  const previewData = curve.points.map((point) => {
    const base = num(point.rate) * 100;
    const row: Record<string, number | string> = {
      tenorLabel: tenorLabel(point.tenorMonths),
      basePct: base,
    };
    for (const tag of COMPONENT_TAGS) {
      const bps = effectiveOverlays
        .filter(
          (o) =>
            o.componentTag === tag &&
            o.adjustmentType === 'additive_bps' &&
            appliesTo(o, point.tenorMonths)
        )
        .reduce((sum, o) => sum + o.value, 0);
      if (bps !== 0) row[tag] = bps / 100;
    }
    row.adjustedPct =
      composeRate(num(point.rate), effectiveOverlays, point.tenorMonths) * 100;
    return row;
  });
  const usedTags = COMPONENT_TAGS.filter((tag) =>
    previewData.some((row) => row[tag] !== undefined)
  );

  // Transparent arithmetic for the tenor the pending spread targets (or the
  // first tenor when flat): base + each component = adjusted.
  const arithmeticTenor =
    pending.tenorKey === 'flat'
      ? curve.points[0]?.tenorMonths
      : Number(pending.tenorKey);
  const arithmetic = useMemo(() => {
    const point = curve.points.find((p) => p.tenorMonths === arithmeticTenor);
    if (!point) return null;
    const base = num(point.rate);
    const terms: string[] = [`base ${fmtPct(base * 100, 2)}`];
    for (const o of effectiveOverlays) {
      if (!appliesTo(o, point.tenorMonths)) continue;
      const short = TAG_SHORT[o.componentTag as ComponentTag] ?? o.componentTag;
      if (o.adjustmentType === 'additive_bps') {
        terms.push(`${o.value >= 0 ? '+' : '−'} ${Math.abs(o.value)} bps ${short}`);
      } else if (o.adjustmentType === 'fixed') {
        terms.push(
          `${o.value >= 0 ? '+' : '−'} ${fmtPct(Math.abs(o.value) * 100, 2)} ${short}`
        );
      } else {
        terms.push(`× ${o.value} ${short}`);
      }
    }
    if (terms.length === 1) return null;
    const adjusted = composeRate(base, effectiveOverlays, point.tenorMonths);
    return `${terms.join(' ')} = ${fmtPct(adjusted * 100, 2)} at ${tenorLabel(
      point.tenorMonths
    )}`;
  }, [arithmeticTenor, curve.points, effectiveOverlays]);

  const saveDisabled = !hasPending || createOverlay.isPending;

  const save = async () => {
    if (saveDisabled) return;
    await createOverlay.mutateAsync({
      baseRefKind: 'curve',
      baseCurveName: curve.curveName,
      tenorMonths: pending.tenorKey === 'flat' ? null : Number(pending.tenorKey),
      adjustmentType: 'additive_bps',
      value: pending.bps,
      componentTag: pending.componentTag,
      effectiveFrom: new Date(`${pending.effectiveFrom}T00:00:00Z`),
      note: pending.note.trim() === '' ? null : pending.note.trim(),
    });
    setPending((prior) => ({ ...prior, bps: '', note: '' }));
  };

  return (
    <>
      <div className="fixed inset-0 bg-navy/30 z-40" onClick={onClose} aria-hidden />
      <aside
        className="fixed inset-y-0 right-0 z-50 w-full max-w-xl bg-surface-raised border-l border-border shadow-xl overflow-y-auto"
        role="dialog"
        aria-label={`Overlay spreads for ${curve.curveName}`}
      >
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border-light sticky top-0 bg-surface-raised z-10">
          <div className="min-w-0 space-y-1">
            <h2 className="text-h2 text-navy">Your spreads on this curve</h2>
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="font-medium text-navy">{curve.currency}</span>
              <MonoChip>{curve.curveName}</MonoChip>
              <CurveTypeBadge curveType={curve.curveType} />
              {curve.curveType === 'discount' && <SyntheticProxyBadge />}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 rounded text-slate hover:text-navy hover:bg-surface"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="px-5 py-4 space-y-6">
          {/* Live stacked preview: base + premium bands + adjusted line. */}
          <div className="space-y-2">
            <h3 className="text-body font-semibold text-navy">
              Base vs adjusted preview
            </h3>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart
                data={previewData}
                margin={{ top: 8, right: 16, bottom: 4, left: 4 }}
              >
                <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="tenorLabel" {...axisProps} />
                <YAxis
                  {...axisProps}
                  tickFormatter={(v: number) => `${v.toFixed(1)}%`}
                  width={48}
                  domain={['auto', 'auto']}
                />
                <Tooltip
                  {...chartTooltipProps}
                  formatter={(value: number | string, name) => {
                    const v = typeof value === 'number' ? value : Number(value);
                    if (
                      typeof name === 'string' &&
                      name !== 'Official base' &&
                      name !== 'Your adjusted'
                    ) {
                      return [`${(v * 100).toFixed(0)} bp`, name];
                    }
                    return [fmtPct(v, 2), name];
                  }}
                />
                <Legend {...chartLegendProps} />
                <Area
                  type="monotone"
                  dataKey="basePct"
                  name="Official base"
                  stackId="curve"
                  stroke={seriesColor(0)}
                  fill={seriesColor(0)}
                  fillOpacity={0.25}
                  isAnimationActive={false}
                />
                {usedTags.map((tag, index) => (
                  <Area
                    key={tag}
                    type="monotone"
                    dataKey={tag}
                    name={TAG_LABELS[tag]}
                    stackId="curve"
                    stroke={seriesColor(index + 1)}
                    fill={seriesColor(index + 1)}
                    fillOpacity={0.35}
                    isAnimationActive={false}
                  />
                ))}
                <Line
                  type="monotone"
                  dataKey="adjustedPct"
                  name="Your adjusted"
                  stroke={CHART_ACCENT}
                  strokeWidth={2}
                  strokeDasharray="6 4"
                  dot={{ r: 3 }}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
            {arithmetic && (
              <p className="text-caption text-navy font-mono bg-surface border border-border-light rounded px-3 py-2">
                {arithmetic}
              </p>
            )}
          </div>

          {/* Active spreads with attribution + end action. */}
          <div className="space-y-2">
            <h3 className="text-body font-semibold text-navy">Active spreads</h3>
            {overlaysQuery.isLoading ? (
              <p className="text-caption text-slate">Loading…</p>
            ) : activeOverlays.length === 0 ? (
              <p className="text-caption text-slate">
                No active spreads on this curve. The published base is used as-is.
              </p>
            ) : (
              <ul className="space-y-2">
                {activeOverlays.map((overlay) => (
                  <li
                    key={overlay.id}
                    className="card px-3.5 py-2.5 flex items-start justify-between gap-3"
                  >
                    <div className="min-w-0 space-y-0.5">
                      <p className="text-body text-navy">
                        <span className="font-medium">
                          {TAG_LABELS[overlay.componentTag as ComponentTag] ??
                            overlay.componentTag}
                        </span>{' '}
                        ·{' '}
                        {overlay.adjustmentType === 'additive_bps'
                          ? `${num(overlay.value) >= 0 ? '+' : ''}${num(overlay.value)} bps`
                          : overlay.adjustmentType === 'fixed'
                            ? `${num(overlay.value) >= 0 ? '+' : ''}${fmtPct(num(overlay.value) * 100, 2)}`
                            : `× ${num(overlay.value)}`}{' '}
                        ·{' '}
                        {overlay.tenorMonths !== null &&
                        overlay.tenorMonths !== undefined
                          ? tenorLabel(overlay.tenorMonths)
                          : 'all tenors'}
                      </p>
                      <p className="text-caption text-slate">
                        Set by {bankName} · effective{' '}
                        {fmtDateUTC(overlay.effectiveFrom)}
                        {overlay.createdByEmail ? ` · by ${overlay.createdByEmail}` : ''}
                      </p>
                      {overlay.note && (
                        <p className="text-caption text-slate italic truncate">
                          {overlay.note}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      disabled={endOverlay.isPending}
                      onClick={() =>
                        endOverlay.mutate({
                          overlayId: overlay.id,
                          effectiveTo: todayIso(),
                        })
                      }
                      className="text-caption font-medium text-critical hover:underline whitespace-nowrap disabled:opacity-50"
                    >
                      End today
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Add a spread. */}
          <div className="space-y-3">
            <h3 className="text-body font-semibold text-navy">Add a spread</h3>
            <div className="grid grid-cols-2 gap-3">
              <label className="block space-y-1">
                <span className="text-micro font-medium text-slate uppercase tracking-wider">
                  Component
                </span>
                <select
                  value={pending.componentTag}
                  onChange={(event) =>
                    setPending((prior) => ({
                      ...prior,
                      componentTag: event.target.value as ComponentTag,
                    }))
                  }
                  className="w-full px-2.5 py-1.5 text-body bg-surface border border-border rounded text-navy"
                >
                  {COMPONENT_TAGS.map((tag) => (
                    <option key={tag} value={tag}>
                      {TAG_LABELS[tag]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-1">
                <span className="text-micro font-medium text-slate uppercase tracking-wider">
                  Tenor
                </span>
                <select
                  value={pending.tenorKey}
                  onChange={(event) =>
                    setPending((prior) => ({ ...prior, tenorKey: event.target.value }))
                  }
                  className="w-full px-2.5 py-1.5 text-body bg-surface border border-border rounded text-navy"
                >
                  <option value="flat">All tenors (flat)</option>
                  {curve.points.map((point) => (
                    <option key={point.tenorMonths} value={String(point.tenorMonths)}>
                      {tenorLabel(point.tenorMonths)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-1">
                <span className="text-micro font-medium text-slate uppercase tracking-wider">
                  Spread (bps)
                </span>
                <input
                  type="number"
                  step="1"
                  value={pending.bps}
                  onChange={(event) =>
                    setPending((prior) => ({ ...prior, bps: event.target.value }))
                  }
                  placeholder="25"
                  className="w-full px-2.5 py-1.5 text-body font-mono bg-surface border border-border rounded text-navy"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-micro font-medium text-slate uppercase tracking-wider">
                  Effective from
                </span>
                <input
                  type="date"
                  value={pending.effectiveFrom}
                  onChange={(event) =>
                    setPending((prior) => ({
                      ...prior,
                      effectiveFrom: event.target.value,
                    }))
                  }
                  className="w-full px-2.5 py-1.5 text-body font-mono bg-surface border border-border rounded text-navy"
                />
              </label>
              <label className="block space-y-1 col-span-2">
                <span className="text-micro font-medium text-slate uppercase tracking-wider">
                  Note (optional)
                </span>
                <input
                  type="text"
                  value={pending.note}
                  onChange={(event) =>
                    setPending((prior) => ({ ...prior, note: event.target.value }))
                  }
                  placeholder="Why this spread exists"
                  className="w-full px-2.5 py-1.5 text-body bg-surface border border-border rounded text-navy"
                />
              </label>
            </div>
            {createOverlay.isError && (
              <p className="text-caption text-critical">
                {createOverlay.error instanceof Error
                  ? createOverlay.error.message
                  : 'Saving the spread failed.'}
              </p>
            )}
            <button
              type="button"
              onClick={() => void save()}
              disabled={saveDisabled}
              className="px-3.5 py-2 text-caption font-medium btn-primary disabled:opacity-50"
            >
              {createOverlay.isPending ? 'Saving…' : 'Save spread'}
            </button>
          </div>

          <p className="text-caption text-slate border-t border-border-light pt-3">
            Spreads are private to {bankName}: no other institution can see or
            compose them, and other banks&rsquo; overlays are never shown here.
            The published golden copy is never modified — your adjusted curve is
            composed at read time.
          </p>
        </div>
      </aside>
    </>
  );
}
