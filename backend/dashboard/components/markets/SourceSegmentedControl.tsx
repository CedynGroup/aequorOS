'use client';

/**
 * Segmented base-source control for one market-data category: AequorOS | Bank |
 * Vendor (spec §5). A plane is disabled when it has no servable data for the
 * category at the as-of date (`available:false`) — the UI never offers an empty
 * pick. The currently-selected plane stays highlighted even if it fell back, so
 * the bank always sees what it chose.
 */

import {
  MARKET_DATA_SOURCES,
  SOURCE_LABELS,
  type MarketDataSource,
} from '@/lib/api/marketDataSources';

export default function SourceSegmentedControl({
  value,
  available,
  onChange,
  disabled = false,
}: {
  value: MarketDataSource;
  available: Record<MarketDataSource, boolean>;
  onChange: (source: MarketDataSource) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label="Base source"
      className="inline-flex items-center rounded-md border border-border bg-surface p-0.5"
    >
      {MARKET_DATA_SOURCES.map((source) => {
        const isActive = value === source;
        const isAvailable = available[source];
        const isDisabled = disabled || (!isAvailable && !isActive);
        return (
          <button
            key={source}
            type="button"
            disabled={isDisabled}
            aria-pressed={isActive}
            onClick={() => onChange(source)}
            title={isAvailable ? undefined : 'No data on this plane at the as-of date'}
            className={`px-3 py-1.5 rounded text-caption font-medium whitespace-nowrap transition-colors ${
              isActive
                ? 'bg-action-light text-action shadow-subtle'
                : isDisabled
                  ? 'text-slate-light cursor-not-allowed'
                  : 'text-slate hover:text-navy'
            }`}
          >
            {SOURCE_LABELS[source]}
          </button>
        );
      })}
    </div>
  );
}
