import type { IndexViewRead } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { fmtDateUTC, labelize, num } from '@/lib/api/values';
import AttributionChip from './AttributionChip';

/** Raw index value, trimmed — units vary per index so none is assumed. */
function fmtIndexValue(value: string): string {
  const parsed = num(value);
  return parsed.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

/** Macro indices / forecasts strip: value, scenario, horizon, attribution. */
export default function IndicesStrip({ indices }: { indices: IndexViewRead[] }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      {indices.map((index) => (
        <div
          key={`${index.indexCode}-${index.scenario}`}
          className="card px-4 py-3.5 flex flex-col gap-2 min-w-0"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-micro font-medium text-slate uppercase tracking-wider truncate">
              {labelize(index.indexCode)}
            </p>
            {index.scenario !== 'base' && (
              <StatusPill tone="amber">{labelize(index.scenario)}</StatusPill>
            )}
          </div>
          <div className="flex items-end justify-between gap-3">
            <span className="font-mono text-kpi text-navy tnum">
              {fmtIndexValue(index.value)}
            </span>
            {index.horizonMonths !== null && index.horizonMonths !== undefined && (
              <span className="text-caption text-slate whitespace-nowrap">
                {index.horizonMonths}m horizon
              </span>
            )}
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-caption text-slate font-mono">
              {fmtDateUTC(index.asOfDate)}
            </span>
            <AttributionChip attribution={index.attribution} />
          </div>
        </div>
      ))}
    </div>
  );
}
