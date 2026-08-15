import { GitCommitHorizontal } from 'lucide-react';
import { fmtTimestamp, shortId } from '@/lib/format';

/** The audit fields shared by run / determination / digest payloads. */
export type RunBadgeRun = {
  /** Long id or input/package digest. */
  id: string;
  /** Engine / methodology version label (e.g. "v3", "AEQ-GHS-CURVES v2"). */
  version?: string;
  /** Content/input hash — truncated for display. */
  hash?: string;
  createdAt?: Date | string;
};

/** Audit chip for a stored run/determination: version, hash, timestamp. */
export function RunBadge({ run }: { run: RunBadgeRun }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded border border-border-light bg-surface px-2 py-1 font-mono text-[10px] tabular-nums text-slate"
      title={`Run ${run.id}${run.hash ? ` · hash ${run.hash}` : ''}`}
    >
      <GitCommitHorizontal size={11} aria-hidden />
      {run.version && <>{run.version}</>}
      {run.hash && (
        <>
          <span className="text-slate-light">·</span>
          {shortId(run.hash, 8)}
        </>
      )}
      {run.createdAt && (
        <>
          <span className="text-slate-light">·</span>
          {fmtTimestamp(run.createdAt)}
        </>
      )}
    </span>
  );
}
