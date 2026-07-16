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
    <div
      className={`grid grid-cols-1 gap-4 ${ratings.length > 1 ? 'sm:grid-cols-2' : ''}`}
    >
      {ratings.map((rating) => (
        <div
          key={`${rating.issuer}-${rating.agency}`}
          className="card px-4 py-3.5 flex flex-col gap-2 min-w-0"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-micro font-medium text-slate uppercase tracking-wider truncate">
              {labelize(rating.issuer)}
            </p>
            <span className="text-caption text-slate font-mono uppercase">
              {rating.agency}
            </span>
          </div>
          <div className="flex items-end justify-between gap-3">
            <span className="font-mono text-kpi text-navy tnum">{rating.rating}</span>
            {rating.watchStatus && (
              <StatusPill tone={watchTone(rating.watchStatus)}>
                {labelize(rating.watchStatus)}
              </StatusPill>
            )}
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-caption text-slate">
              Rated <span className="font-mono">{fmtDateUTC(rating.ratingDate)}</span>
            </span>
            <AttributionChip attribution={rating.attribution} />
          </div>
        </div>
      ))}
    </div>
  );
}
