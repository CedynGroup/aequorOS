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
    return (
      <span className="text-caption text-slate whitespace-nowrap">
        Live engine{' '}
        <span className="font-mono text-[10px] uppercase">{live.status}</span>
        {' · '}recomputed {fmtRelative(live.computedAt)}
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
