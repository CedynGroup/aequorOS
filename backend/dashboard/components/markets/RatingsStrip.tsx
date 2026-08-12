import type { RatingViewRead } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { fmtDateUTC, labelize } from '@/lib/api/values';
import AttributionChip from './AttributionChip';

function watchTone(watchStatus: string | null | undefined): StatusTone {
  switch ((watchStatus ?? '').toLowerCase()) {
    case 'positive':
      return 'success';
    case 'negative':
    case 'watch_negative':
      return 'critical';
    case 'developing':
    case 'watch':
      return 'amber';
    default:
      return 'slate';
  }
}

/** Ratings strip: one card per issuer with agency, rating, and watch chip. */
export default function RatingsStrip({ ratings }: { ratings: RatingViewRead[] }) {
  return (
    <div className="overflow-x-auto border border-border rounded-lg bg-surface-raised">
      <table className="w-full min-w-[31rem] text-body">
        <thead className="bg-surface/60 text-micro font-medium uppercase tracking-wider text-slate">
          <tr>
            <th className="px-4 py-2.5 text-left">Issuer</th>
            <th className="px-3 py-2.5 text-left">Agency</th>
            <th className="px-3 py-2.5 text-right">Rating</th>
            <th className="px-3 py-2.5 text-right">Watch</th>
            <th className="px-4 py-2.5 text-right">Source</th>
          </tr>
        </thead>
        <tbody>
          {ratings.map((rating) => (
            <tr key={`${rating.issuer}-${rating.agency}`} className="border-t border-border-light hover:bg-surface/60">
              <td className="px-4 py-3 font-medium text-navy">{labelize(rating.issuer)}</td>
              <td className="px-3 py-3 font-mono text-caption text-slate uppercase">{rating.agency}</td>
              <td className="px-3 py-3 text-right font-mono text-kpi text-navy tnum">{rating.rating}</td>
              <td className="px-3 py-3 text-right">
                {rating.watchStatus ? (
                  <StatusPill tone={watchTone(rating.watchStatus)}>
                    {labelize(rating.watchStatus)}
                  </StatusPill>
                ) : (
                  <span className="text-caption text-slate">Stable</span>
                )}
              </td>
              <td className="px-4 py-3 text-right">
                <span className="block text-caption font-mono text-slate">{fmtDateUTC(rating.ratingDate)}</span>
                <AttributionChip attribution={rating.attribution} className="justify-end mt-1" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
