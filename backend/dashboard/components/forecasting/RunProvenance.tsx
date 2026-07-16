import { GitCommitHorizontal } from 'lucide-react';
import { fmtTimestamp, shortId } from '@/lib/api/values';

/**
 * Provenance meta row for computed results that carry run identity but not
 * the full RunBadge field set (what-if / optimizer result payloads):
 * run id, input hash, computed-at. Every value comes off the API payload.
 */
export default function RunProvenance({
  runId,
  inputHash,
  createdAt,
  note,
}: {
  runId: string;
  inputHash: string;
  createdAt: Date | null;
  note?: string;
}) {
  return (
    <div className="flex items-center gap-3 flex-wrap text-caption text-slate">
      <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-border-light bg-surface font-mono text-[10px] tnum">
        <GitCommitHorizontal size={11} aria-hidden />
        run {shortId(runId)}
        <span className="text-slate-light">·</span>
        input {shortId(inputHash)}
        {createdAt && (
          <>
            <span className="text-slate-light">·</span>
            {fmtTimestamp(createdAt)}
          </>
        )}
      </span>
      {note && <span>{note}</span>}
    </div>
  );
}
