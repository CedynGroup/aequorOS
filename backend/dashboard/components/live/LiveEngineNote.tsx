import { fmtRelative, fmtTimestamp } from '@/lib/api/values';

/**
 * Desk-grade provenance line: which engine tier produced the figures on
 * screen and how fresh they are. Stored/live status without any governance
 * vocabulary — full run provenance lives on the Reports registry.
 */
export default function LiveEngineNote({
  live,
  stored,
}: {
  live?: { status: string; computedAt: Date } | null;
  stored: boolean;
}) {
  if (live) {
    const healthy = live.status.toLowerCase() === 'green';
    return (
      <span
        className="text-caption text-slate whitespace-nowrap"
        title={`Position date — when the engines last consumed the book. Recomputation fires automatically when new data lands (last ran ${fmtRelative(live.computedAt)}).`}
      >
        Positions as of {fmtTimestamp(live.computedAt)}
        {!healthy && (
          <>
            {' '}
            <span className="font-mono text-micro uppercase">{live.status}</span>
          </>
        )}
      </span>
    );
  }
  return (
    <span className="text-caption text-slate whitespace-nowrap">
      {stored
        ? 'Computed from stored engine results'
        : 'Computed live from current positions'}
    </span>
  );
}
