'use client';

/**
 * Analysis-window presets for series surfaces (trend charts, breach history).
 * Windows slice the period series client-side — ratios stay point-in-time
 * per book; the window selects how much of the path you see, per the
 * three-plane model. Not for tables of positions, which belong to one book.
 */

export type RangePreset = '3M' | '6M' | '1Y' | 'All';

export const RANGE_MONTHS: Record<RangePreset, number | null> = {
  '3M': 3,
  '6M': 6,
  '1Y': 12,
  All: null,
};

export default function RangeTabs({
  value,
  onChange,
}: {
  value: RangePreset;
  onChange: (preset: RangePreset) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Analysis window"
      className="inline-flex items-center rounded-md border border-border-light bg-surface p-0.5"
    >
      {(Object.keys(RANGE_MONTHS) as RangePreset[]).map((preset) => (
        <button
          key={preset}
          type="button"
          onClick={() => onChange(preset)}
          aria-pressed={value === preset}
          className={`px-2.5 py-1 rounded text-micro font-medium ${
            value === preset
              ? 'bg-surface-raised text-navy shadow-subtle'
              : 'text-slate hover:text-navy'
          }`}
        >
          {preset}
        </button>
      ))}
    </div>
  );
}
