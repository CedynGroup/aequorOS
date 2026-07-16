import type { MarketDataAttributionRead } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';

/** "3m", "2h", "5d" — compact age from the attribution's ageSeconds. */
export function fmtAge(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  if (seconds < 60) return 'now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/**
 * Source attribution + freshness chip carried by every Markets card: the
 * vendor/source system the value was arbitrated from, plus a warning chip
 * with the pull age when the backend flagged the view stale.
 */
export default function AttributionChip({
  attribution,
  className = '',
}: {
  attribution: MarketDataAttributionRead;
  className?: string;
}) {
  const age = fmtAge(attribution.ageSeconds);
  return (
    <span className={`inline-flex items-center gap-1.5 min-w-0 ${className}`}>
      <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-border-light bg-surface text-[10px] font-mono uppercase tracking-wider text-slate whitespace-nowrap">
        {attribution.sourceSystem}
      </span>
      {attribution.stale ? (
        <StatusPill tone="amber">Stale · {age}</StatusPill>
      ) : (
        <span
          className="text-caption text-slate whitespace-nowrap"
          title={`Pulled ${attribution.ingestedAt.toISOString()}`}
        >
          {age === 'now' ? 'just pulled' : `${age} old`}
        </span>
      )}
    </span>
  );
}
