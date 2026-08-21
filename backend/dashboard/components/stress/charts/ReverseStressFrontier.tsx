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

function AxisRow({ axis }: { axis: FrontierAxis }) {
  const kMax = Math.max(axis.kMax, 1.01);
  const pct = (k: number) => `${Math.max(0, Math.min(1, (k - 1) / (kMax - 1))) * 100}%`;
  const markerK = axis.breached && axis.breachMultiplier ? axis.breachMultiplier : null;

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
      <div className="relative h-9">
        {/* Track: safe → stressed gradient */}
        <div
          className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2.5 rounded-full overflow-hidden"
          style={{
            background:
              'linear-gradient(to right, rgb(var(--ok) / 0.35), rgb(var(--warn) / 0.35), rgb(var(--crit) / 0.4))',
          }}
        />
        {/* 1x anchor */}
        <div className="absolute left-0 top-0 h-full flex flex-col items-start">
          <div className="w-px h-full bg-border" />
        </div>
        {/* breach marker */}
        {markerK !== null && (
          <div
            className="absolute top-0 h-full flex flex-col items-center"
            style={{ left: pct(markerK) }}
          >
            <div className="w-0.5 h-full" style={{ background: 'rgb(var(--crit))' }} />
            <span
              className="absolute -top-0.5 translate-x-[-50%] left-1/2 text-micro font-semibold tnum text-critical whitespace-nowrap"
              style={{ marginTop: '-14px' }}
            >
              {markerK.toFixed(2)}×
            </span>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between text-micro text-slate tnum">
        <span>1× (as reported)</span>
        {axis.ratioAtBreach !== null ? (
          <span>
            {axis.ratioLabel} {axis.ratioAtBreach.toFixed(1)}% at breach vs floor {axis.floor.toFixed(1)}%
          </span>
        ) : (
          <span>
            {axis.ratioLabel} floor {axis.floor.toFixed(1)}% holds across the search range
          </span>
        )}
        <span>{kMax.toFixed(2)}×</span>
      </div>
    </div>
  );
}

export default function ReverseStressFrontier({ axes }: { axes: FrontierAxis[] }) {
  return (
    <div className="space-y-6">
      {axes.map((axis) => (
        <AxisRow key={axis.label} axis={axis} />
      ))}
    </div>
  );
}
