'use client';

/**
 * In-page tab controls (local state, not routes). `SubTabs` for switching
 * views inside a page; `RangeTabs` for analysis-window presets on series
 * surfaces. Both ported from the dashboard.
 */

export type SubTab = { key: string; label: string };

export function SubTabs({
  items,
  active,
  onChange,
}: {
  items: SubTab[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="border-b border-border-light">
      <nav className="-mb-px flex gap-1 overflow-x-auto" aria-label="Section views">
        {items.map((item) => {
          const isActive = item.key === active;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onChange(item.key)}
              className={`whitespace-nowrap border-b-2 px-4 py-2.5 text-body font-medium transition-colors ${
                isActive
                  ? 'border-action text-navy'
                  : 'border-transparent text-slate hover:border-border hover:text-navy'
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}

export type RangePreset = '3M' | '6M' | '1Y' | 'All';

export const RANGE_MONTHS: Record<RangePreset, number | null> = {
  '3M': 3,
  '6M': 6,
  '1Y': 12,
  All: null,
};

export function RangeTabs({
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
          className={`rounded px-2.5 py-1 text-micro font-medium ${
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
