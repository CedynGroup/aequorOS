import { Clock } from 'lucide-react';
import { fmtTimestamp } from '@/lib/api/values';

/**
 * Computed-at meta row for what-if / optimizer results. Full run provenance
 * (engine version, input hash) is governance furniture and lives on the
 * Reports registry, not on desk surfaces.
 */
export default function RunProvenance({
  createdAt,
  note,
}: {
  createdAt: Date | null;
  note?: string;
}) {
  return (
    <div className="flex items-center gap-3 flex-wrap text-caption text-slate">
      {createdAt && (
        <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-border-light bg-surface font-mono text-[10px] tnum">
          <Clock size={11} aria-hidden />
          computed {fmtTimestamp(createdAt)}
        </span>
      )}
      {note && <span>{note}</span>}
    </div>
  );
}
