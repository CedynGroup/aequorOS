'use client';

/**
 * Sources tab — the three-plane control room (spec §5). Per-category base-source
 * selector (AequorOS | Bank | Vendor) + overlay toggle, wired to
 * `GET/PUT /source-preferences`; below it the side-by-side plane comparison
 * from `GET /planes` so the bank sees exactly what each choice would feed the
 * engines before committing. The selection is live — no approval gate (founder
 * call) — so a prominent banner states where it flows.
 */

import { useState } from 'react';
import { Info, Radio, Waypoints } from 'lucide-react';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import StatusPill from '@/components/ui/StatusPill';
import { fmtTimestamp } from '@/lib/api/values';
import {
  useMarketDataPlanes,
  useMarketDataSourcePreferences,
  useUpdateMarketDataSourcePreferences,
} from '@/lib/api/hooks';
import {
  CATEGORY_LABELS,
  MARKET_DATA_CATEGORIES,
  MARKET_DATA_SOURCES,
  SOURCE_LABELS,
  type CategorySourcePreference,
  type MarketDataCategory,
  type MarketDataPlanesResponse,
  type MarketDataSource,
  type MarketDataSourcePreferencesPatch,
} from '@/lib/api/marketDataSources';
import PlaneComparison from './PlaneComparison';
import SourceSegmentedControl from './SourceSegmentedControl';

const CATEGORY_HINTS: Record<MarketDataCategory, string> = {
  curves: 'Yield & discount curves feeding IRRBB duration and FTP discounting',
  fx: 'Spot & tenor FX for revaluation and the FX net-open-position return',
  rates: 'Policy, money-market and lending reference rates',
};

function availableMap(
  planes: MarketDataPlanesResponse | undefined
): Record<MarketDataSource, boolean> {
  const map: Record<MarketDataSource, boolean> = { aequor: true, bank: true, vendor: true };
  if (!planes) return map;
  for (const source of MARKET_DATA_SOURCES) {
    const plane = planes.planes.find((candidate) => candidate.source === source);
    map[source] = plane ? plane.available : false;
  }
  return map;
}

function buildPatch(
  category: MarketDataCategory,
  partial: Partial<CategorySourcePreference>
): MarketDataSourcePreferencesPatch {
  switch (category) {
    case 'curves':
      return { curves: partial };
    case 'fx':
      return { fx: partial };
    case 'rates':
      return { rates: partial };
  }
}

function OverlayToggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors ${
        checked ? 'bg-action border-action' : 'bg-surface border-border'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
    >
      <span
        aria-hidden
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-subtle transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

function CategoryRow({
  category,
  preference,
  planes,
  onSource,
  onOverlay,
  busy,
  focused,
  onFocus,
}: {
  category: MarketDataCategory;
  preference: CategorySourcePreference;
  planes: MarketDataPlanesResponse | undefined;
  onSource: (source: MarketDataSource) => void;
  onOverlay: (overlay: boolean) => void;
  busy: boolean;
  focused: boolean;
  onFocus: () => void;
}) {
  const available = availableMap(planes);
  const selectedPlane = planes?.planes.find((plane) => plane.isSelected);
  const fellBack = selectedPlane?.attribution?.fellBack ?? false;

  return (
    <tr className={`border-t border-border-light transition-colors ${focused ? 'bg-action-light/20' : 'hover:bg-surface/60'}`}>
      <td className="px-4 py-3 align-top">
        <button
          type="button"
          onClick={onFocus}
          className="text-left min-w-0"
          aria-pressed={focused}
        >
          <span className="inline-flex items-center gap-2">
            <span className="text-body font-semibold text-navy">{CATEGORY_LABELS[category]}</span>
            {focused && <StatusPill tone="action">Comparing</StatusPill>}
            {fellBack && <StatusPill tone="amber">Fell back</StatusPill>}
          </span>
          <span className="block mt-0.5 text-caption text-slate">{CATEGORY_HINTS[category]}</span>
        </button>
      </td>
      <td className="px-4 py-3 align-middle">
        <SourceSegmentedControl
          value={preference.source}
          available={available}
          onChange={onSource}
          disabled={busy}
        />
      </td>
      <td className="px-4 py-3 text-right align-middle">
        <label className="inline-flex items-center gap-2 whitespace-nowrap">
          <span className="text-caption font-medium text-slate">Overlay</span>
          <OverlayToggle checked={preference.overlay} onChange={onOverlay} disabled={busy} />
        </label>
      </td>
    </tr>
  );
}

export default function SourcesControlRoom({
  bankId,
  asOf,
}: {
  bankId: string;
  asOf: string | null;
}) {
  const prefs = useMarketDataSourcePreferences(bankId);
  const update = useUpdateMarketDataSourcePreferences(bankId);
  const asOfParam = asOf ?? undefined;

  // One planes query per category so every row reflects availability and the
  // comparison can switch categories without a refetch storm.
  const curvesPlanes = useMarketDataPlanes(bankId, 'curves', asOfParam);
  const fxPlanes = useMarketDataPlanes(bankId, 'fx', asOfParam);
  const ratesPlanes = useMarketDataPlanes(bankId, 'rates', asOfParam);
  const planesByCategory: Record<
    MarketDataCategory,
    MarketDataPlanesResponse | undefined
  > = {
    curves: curvesPlanes.data,
    fx: fxPlanes.data,
    rates: ratesPlanes.data,
  };

  const [focused, setFocused] = useState<MarketDataCategory>('curves');

  if (prefs.isError) {
    return <ErrorPanel error={prefs.error} onRetry={() => prefs.refetch()} />;
  }

  const data = prefs.data;
  const busy = update.isPending;

  return (
    <div className="space-y-6">
      {/* The live-toggle banner (spec §5): jurisdiction-neutral wording. */}
      <div className="rounded-lg border border-action/30 bg-action-light px-4 py-3 flex items-start gap-2.5">
        <Radio size={16} className="text-action shrink-0 mt-0.5" aria-hidden />
        <div className="min-w-0">
          <p className="text-body font-medium text-navy">
            Your selection flows live into IRRBB / FTP and official runs.
          </p>
          <p className="text-caption text-slate mt-0.5">
            Switching a plane changes which values the risk engines consume immediately — there is
            no approval gate. If a chosen plane has no data at a date, arbitration falls back and
            flags it, so a calculation never breaks.
          </p>
        </div>
      </div>

      <section className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Waypoints size={15} className="text-slate" aria-hidden />
          <div>
            <h3 className="text-h3 text-navy">Source routing</h3>
            <p className="mt-0.5 text-caption text-slate">Choose the plane each market category supplies to the engines.</p>
          </div>
        </div>
        {!data ? (
          <div className="space-y-px" aria-busy>
            {MARKET_DATA_CATEGORIES.map((category) => (
              <div
                key={category}
                className="h-[70px] border-t border-border-light bg-surface animate-pulse"
              />
            ))}
          </div>
        ) : (
          <table className="w-full min-w-[46rem] text-body">
            <thead className="bg-surface/60 text-micro font-medium uppercase tracking-wider text-slate">
              <tr>
                <th className="px-4 py-2.5 text-left">Market category</th>
                <th className="px-4 py-2.5 text-left">Base plane</th>
                <th className="px-4 py-2.5 text-right">Private overlay</th>
              </tr>
            </thead>
            <tbody>
              {MARKET_DATA_CATEGORIES.map((category) => (
                <CategoryRow
                  key={category}
                  category={category}
                  preference={data[category]}
                  planes={planesByCategory[category]}
                  busy={busy}
                  focused={focused === category}
                  onFocus={() => setFocused(category)}
                  onSource={(source) => update.mutate(buildPatch(category, { source }))}
                  onOverlay={(overlay) => update.mutate(buildPatch(category, { overlay }))}
                />
              ))}
            </tbody>
          </table>
        )}

        {data && (data.updatedBy || data.updatedAt) && (
          <p className="text-micro text-slate">
            {busy ? (
              'Saving…'
            ) : (
              <>
                Last changed
                {data.updatedBy ? ` by ${data.updatedBy}` : ''}
                {data.updatedAt ? ` · ${fmtTimestamp(data.updatedAt)}` : ''}
              </>
            )}
          </p>
        )}
        {update.isError && (
          <p className="text-caption text-critical">
            Could not save the source preference. Try again.
          </p>
        )}
      </section>

      {/* Side-by-side plane comparison for the focused category. */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-h3 text-navy">Plane comparison</h3>
            <p className="text-caption text-slate">
              The same {CATEGORY_LABELS[focused]} scope resolved under every plane — the selected
              one is highlighted.
            </p>
          </div>
          <div
            role="group"
            aria-label="Comparison category"
            className="inline-flex items-center rounded-md border border-border bg-surface p-0.5"
          >
            {MARKET_DATA_CATEGORIES.map((category) => (
              <button
                key={category}
                type="button"
                onClick={() => setFocused(category)}
                aria-pressed={focused === category}
                className={`px-3 py-1.5 rounded text-caption font-medium whitespace-nowrap transition-colors ${
                  focused === category
                    ? 'bg-action-light text-action shadow-subtle'
                    : 'text-slate hover:text-navy'
                }`}
              >
                {CATEGORY_LABELS[category]}
              </button>
            ))}
          </div>
        </div>

        <div className="card overflow-hidden">
          {(() => {
            const query =
              focused === 'curves'
                ? curvesPlanes
                : focused === 'fx'
                  ? fxPlanes
                  : ratesPlanes;
            if (query.isError) {
              return (
                <div className="p-5">
                  <ErrorPanel error={query.error} onRetry={() => query.refetch()} />
                </div>
              );
            }
            if (!query.data) {
              return (
                <div className="p-10 flex items-center justify-center text-caption text-slate">
                  <span className="inline-flex items-center gap-2">
                    <Info size={13} aria-hidden />
                    Loading plane comparison…
                  </span>
                </div>
              );
            }
            return <PlaneComparison data={query.data} category={focused} />;
          })()}
        </div>
      </section>

      <p className="inline-flex items-start gap-1.5 text-caption text-slate">
        <Info size={12} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          Planes are read from what is already ingested — a plane greys out when it has no data for
          a category at the as-of date. The {SOURCE_LABELS.aequor} plane is the desk&rsquo;s golden
          copy; the {SOURCE_LABELS.bank} plane is your own uploads/pushes; the {SOURCE_LABELS.vendor}{' '}
          plane is your licensed feed.
        </span>
      </p>
    </div>
  );
}
