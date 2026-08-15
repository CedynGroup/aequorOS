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
    <div className="overflow-x-auto border border-border rounded-lg bg-surface-raised">
      <table className="w-full min-w-[34rem] text-body">
        <thead className="bg-surface/60 text-micro font-medium uppercase tracking-wider text-slate">
          <tr>
            <th className="px-4 py-2.5 text-left">Indicator</th>
            <th className="px-3 py-2.5 text-right">Scenario</th>
            <th className="px-3 py-2.5 text-right">Value</th>
            <th className="px-3 py-2.5 text-right">Horizon</th>
            <th className="px-4 py-2.5 text-right">Source</th>
          </tr>
        </thead>
        <tbody>
          {indices.map((index) => (
            <tr
              key={`${index.indexCode}-${index.scenario}`}
              className="border-t border-border-light hover:bg-surface/60"
            >
              <td className="px-4 py-3 font-medium text-navy">{labelize(index.indexCode)}</td>
              <td className="px-3 py-3 text-right">
                {index.scenario !== 'base' ? (
                  <StatusPill tone="amber">{labelize(index.scenario)}</StatusPill>
                ) : (
                  <span className="text-caption text-slate">Base</span>
                )}
              </td>
              <td className="px-3 py-3 text-right font-mono font-semibold text-navy tnum">
                {fmtIndexValue(index.value)}
              </td>
              <td className="px-3 py-3 text-right text-caption text-slate">
                {index.horizonMonths !== null && index.horizonMonths !== undefined
                  ? `${index.horizonMonths}m`
                  : '—'}
              </td>
              <td className="px-4 py-3 text-right">
                <span className="block text-caption font-mono text-slate">{fmtDateUTC(index.asOfDate)}</span>
                <AttributionChip attribution={index.attribution} className="justify-end mt-1" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
