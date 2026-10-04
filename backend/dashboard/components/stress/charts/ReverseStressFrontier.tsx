'use client';

/**
 * Reverse-stress frontier plot (docs/stress.md §4 item 3 — "new component").
 * The reverse-stress engine bisects each axis (liquidity LCR, capital CET1) for
 * the severity multiplier k at which the hard floor breaks. This renders that
 * frontier as a severity ladder: a track from 1× to k_max per axis, the breach
 * multiplier marked, and the ratio-at-breach against the floor — the "how far
 * from the edge" read a board pack needs.
 *
 * Token-native SVG/CSS; both themes.
 */

import { fmtPct } from '@/lib/format';

export type FrontierAxis = {
  label: string;
  breached: boolean;
  /** Severity multiplier at breach (only meaningful when `breached`). */
  breachMultiplier: number | null;
  /** Upper bound of the bisection search. */
  kMax: number;
  ratioAtBreach: number | null;
  floor: number;
  /** e.g. "LCR" / "CET1". */
  ratioLabel: string;
};

/**
 * Where severity `k` sits on the 1×…k_max track, as a fraction of its width.
 * Values outside the track (a breach below the 1× origin, or beyond k_max)
 * pin to the nearer end.
 */
function trackFraction(k: number, kMax: number): number {
  return Math.max(0, Math.min(1, (k - 1) / (kMax - 1)));
}

/**
 * Places an element at `fraction` along the track while keeping its whole box
 * inside the track: shifting it back by the same fraction of its own width
 * left-aligns it at the 1× origin, centres it mid-track and right-aligns it at
 * k_max, so the breach value never hangs past either end.
 */
function pinnedWithinTrack(fraction: number) {
  const pct = `${fraction * 100}%`;
  return { left: pct, transform: `translateX(-${pct})` };
}

function AxisRow({ axis }: { axis: FrontierAxis }) {
  const kMax = Math.max(axis.kMax, 1.01);
  const markerK = axis.breached && axis.breachMultiplier ? axis.breachMultiplier : null;
  const marker =
    markerK !== null
      ? { value: `${markerK.toFixed(2)}×`, style: pinnedWithinTrack(trackFraction(markerK, kMax)) }
      : null;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-body font-medium text-navy">{axis.label}</span>
        <span className={`text-caption tnum ${axis.breached ? 'text-critical' : 'text-success'}`}>
          {axis.breached && markerK
            ? `Breaks at ${markerK.toFixed(2)}× severity`
            : `Holds to ${kMax.toFixed(2)}×`}
        </span>
      </div>
      <div>
        {/* Breach value lane: its own band between the title row and the track */}
        <div className="relative h-4">
          {marker && (
            <span
              className="absolute bottom-0 text-micro font-semibold tnum text-critical whitespace-nowrap"
              style={marker.style}
              data-testid="reverse-stress-breach-value"
            >
              {marker.value}
            </span>
          )}
        </div>
        <div className="relative h-7">
          {/* Track: safe → stressed gradient */}
          <div
            className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2.5 rounded-full overflow-hidden"
            style={{
              background:
                'linear-gradient(to right, rgb(var(--ok) / 0.35), rgb(var(--warn) / 0.35), rgb(var(--crit) / 0.4))',
            }}
          />
          {/* 1x anchor */}
          <div className="absolute left-0 top-0 h-full w-px bg-border" />
          {marker && (
            <div
              className="absolute top-0 h-full w-0.5"
              style={{ ...marker.style, background: 'rgb(var(--crit))' }}
            />
          )}
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 text-micro text-slate tnum">
        <span className="shrink-0">1× (as reported)</span>
        {axis.ratioAtBreach !== null ? (
          <span className="text-center">
            {axis.ratioLabel} {fmtPct(axis.ratioAtBreach, 1)} at breach vs floor {fmtPct(axis.floor, 1)}
          </span>
        ) : (
          <span className="text-center">
            {axis.ratioLabel} floor {fmtPct(axis.floor, 1)} holds across the search range
          </span>
        )}
        <span className="shrink-0">{kMax.toFixed(2)}×</span>
      </div>
    </div>
  );
}

export default function ReverseStressFrontier({ axes }: { axes: FrontierAxis[] }) {
  return (
    <div className="space-y-6" data-testid="reverse-stress-frontier">
      {axes.map((axis) => (
        <AxisRow key={axis.label} axis={axis} />
      ))}
    </div>
  );
}
