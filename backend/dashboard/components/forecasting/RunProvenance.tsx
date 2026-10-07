import { Clock, ShieldCheck } from 'lucide-react';
import type { ForecastAssumptionProvenanceRead } from '@aequoros/risk-service-api';
import { fmtTimestamp } from '@/lib/api/values';
import { provenanceLabel } from './AssumptionRegister';

/**
 * Computed-at meta row for what-if / optimizer results, with the approved
 * assumption version the run resolved. Full run provenance (engine version,
 * input hash) is governance furniture and lives on the Reports registry, not
 * on desk surfaces.
 */
export default function RunProvenance({
  createdAt,
  assumptionVersion,
  note,
}: {
  createdAt: Date | null;
  assumptionVersion?: ForecastAssumptionProvenanceRead | null;
  note?: string;
}) {
  return (
    <div className="flex items-center gap-3 flex-wrap text-caption text-slate">
      {createdAt && (
        <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded border border-border-light bg-surface font-mono text-micro tnum">
          <Clock size={11} aria-hidden />
          computed {fmtTimestamp(createdAt)}
        </span>
      )}
      {assumptionVersion && (
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck size={11} aria-hidden />
          Assumptions: {provenanceLabel(assumptionVersion)}
        </span>
      )}
      {note && <span>{note}</span>}
    </div>
  );
}
