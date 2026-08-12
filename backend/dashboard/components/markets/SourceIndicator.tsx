'use client';

/**
 * Compact "which plane is feeding this" badge for the Curves / FX / Rates tabs.
 * The values on those tabs already reflect the bank's selected plane — the
 * backend arbitration honours the source preference server-side (spec §3) — so
 * this surfaces the active choice and links to the Sources tab to change it.
 */

import { SlidersHorizontal } from 'lucide-react';
import {
  SOURCE_LABELS,
  type CategorySourcePreference,
  type MarketDataCategory,
  CATEGORY_LABELS,
} from '@/lib/api/marketDataSources';

export default function SourceIndicator({
  category,
  preference,
  onManage,
}: {
  category: MarketDataCategory;
  preference: CategorySourcePreference | undefined;
  onManage: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onManage}
      title={`Change the ${CATEGORY_LABELS[category]} source plane`}
      className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1 text-caption text-slate hover:text-navy hover:border-action/40 transition-colors whitespace-nowrap"
    >
      <SlidersHorizontal size={13} aria-hidden />
      <span>
        Source:{' '}
        <span className="font-medium text-navy">
          {preference ? SOURCE_LABELS[preference.source] : '—'}
        </span>
      </span>
      {preference?.overlay && (
        <span className="inline-flex items-center rounded border border-action/30 bg-action-light px-1.5 py-0.5 text-micro font-medium text-action">
          Overlay on
        </span>
      )}
    </button>
  );
}
