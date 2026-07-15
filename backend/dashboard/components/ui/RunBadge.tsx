import { GitCommitHorizontal } from 'lucide-react';
import { fmtTimestamp, shortId } from '@/lib/api/values';

/** The audit fields shared by regulatory and forecast run payloads. */
export type RunBadgeRun = {
  id: string;
  engineVersion: string;
  inputHash: string;
  createdAt: Date;
};

/** Audit chip for a stored calculation run: engine, input hash, timestamp. */
export default function RunBadge({ run }: { run: RunBadgeRun }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-border-light bg-surface text-[10px] font-mono text-slate tabular-nums"
      title={`Run ${run.id} · input hash ${run.inputHash}`}
    >
      <GitCommitHorizontal size={11} aria-hidden />
      {run.engineVersion}
      <span className="text-slate-light">·</span>
      {shortId(run.inputHash, 8)}
      <span className="text-slate-light">·</span>
      {fmtTimestamp(run.createdAt)}
    </span>
  );
}
