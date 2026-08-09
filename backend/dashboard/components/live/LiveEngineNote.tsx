import { fmtRelative } from '@/lib/api/values';

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
        title={`Engines last ran ${fmtRelative(live.computedAt)} — recomputation fires automatically when new data lands.`}
      >
        Live engine
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
