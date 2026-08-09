'use client';

/**
 * Stressed survival ladder — renders the per-currency ladder blobs the
 * stored liquidity run already carries (`metrics.currency_gaps` +
 * `metrics.stressed_ladder`) and that no other screen shows. Read-only over
 * the stored run: no math beyond cumulating what the engine persisted.
 */

import { useState } from 'react';
import { Layers } from 'lucide-react';
import type { RegulatoryRunRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StressedLadderChart, {
  type LadderPoint,
} from '@/components/liquidity/charts/StressedLadderChart';
import {
  LADDER_HORIZONS,
  parseCurrencyGaps,
  parseStressedLadder,
} from '@/components/liquidity/ladderData';
import { runComputedAt } from '@/components/liquidity/runData';
import { currencyCode, fmtCurrency, fmtCurrencySigned } from '@/lib/format';

type HorizonRow = {
  horizon: string;
  contractualNet: number | null;
  contractualCumulative: number | null;
  stressedNet: number | null;
  stressedCumulative: number | null;
};

function signedCell(value: number | null): string {
  return value === null ? '—' : fmtCurrencySigned(value);
}

export default function StressedLadderPanel({
  runId,
  run,
  isLoading,
}: {
  /** latestRunId from the liquidity dashboard payload (null = none minted). */
  runId: string | null | undefined;
  run: RegulatoryRunRead | undefined;
  isLoading: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const gaps = parseCurrencyGaps(run);
  const ladder = parseStressedLadder(run);
  const currencies = [
    ...new Set([...gaps, ...ladder].map((entry) => entry.currency)),
  ].sort();
  const active =
    selected && currencies.includes(selected) ? selected : currencies[0];
  const activeGap = gaps.find((entry) => entry.currency === active);
  const activeStress = ladder.find((entry) => entry.currency === active);
  const hasStress = ladder.length > 0;

  const chartData: LadderPoint[] = LADDER_HORIZONS.map((horizon, index) => ({
    horizon: horizon.label,
    contractual: activeGap ? activeGap.cumulative[index] : null,
    stressed: activeStress ? activeStress.stressedCumulative[index] : null,
  }));

  const tableRows: HorizonRow[] = LADDER_HORIZONS.map((horizon, index) => ({
    horizon: horizon.label,
    contractualNet: activeGap ? activeGap.net[index] : null,
    contractualCumulative: activeGap ? activeGap.cumulative[index] : null,
    stressedNet: activeStress ? activeStress.stressedNet[index] : null,
    stressedCumulative: activeStress
      ? activeStress.stressedCumulative[index]
      : null,
  }));

  const eq = `${currencyCode()} eq.`;
  const columns: Column<HorizonRow>[] = [
    { key: 'horizon', header: 'Horizon', width: '24%', render: (r) => r.horizon },
    {
      key: 'contractual_net',
      header: `Contractual net (${eq})`,
      numeric: true,
      render: (r) => signedCell(r.contractualNet),
    },
    {
      key: 'contractual_cum',
      header: `Contractual cumulative (${eq})`,
      numeric: true,
      render: (r) => signedCell(r.contractualCumulative),
    },
    {
      key: 'stressed_net',
      header: `Stressed net (${eq})`,
      numeric: true,
      render: (r) => signedCell(r.stressedNet),
    },
    {
      key: 'stressed_cum',
      header: `Stressed cumulative (${eq})`,
      numeric: true,
      render: (r) => signedCell(r.stressedCumulative),
    },
  ];

  const hasData = currencies.length > 0;

  return (
    <SectionCard
      title="Stressed survival ladder"
      subtitle="Five contractual horizons per the stored results — the LMTD 14-bucket set generates with the LMT return"
      noPadding
      computedAt={hasData ? runComputedAt(run) : undefined}
      footer={
        hasData ? (
          activeStress ? (
            <span>
              {active}: demand-natured deposits{' '}
              <span className="font-mono text-navy">
                {fmtCurrency(activeStress.demandDeposits)}
              </span>{' '}
              of which stable core beyond 12 months{' '}
              <span className="font-mono text-navy">
                {fmtCurrency(activeStress.stableCore)}
              </span>{' '}
              under the reviewed run-off schedule (LRMD ¶50–54).
            </span>
          ) : (
            <span>
              Contractual only — the behavioural ladder rides runs whose
              scenario carries the reviewed NMD run-off schedule (LRMD
              ¶50–54).
            </span>
          )
        ) : undefined
      }
    >
      {isLoading ? (
        <p className="px-5 py-4 text-body text-slate">
          Loading the stored run…
        </p>
      ) : !runId ? (
        <div className="p-5">
          <EmptyState
            Icon={Layers}
            title="No stored liquidity run yet"
            description="The ladder reads from the latest stored baseline run. Once a liquidity run is stored for this period, its per-currency positions appear here."
          />
        </div>
      ) : !hasData ? (
        <div className="p-5">
          <EmptyState
            Icon={Layers}
            title="This run carries no per-currency ladder"
            description="Stored results from before the per-currency ladders don't carry them; the next stored liquidity run writes both the contractual gaps and, where the scenario includes the reviewed NMD run-off schedule, the behaviourally-stressed ladder."
          />
        </div>
      ) : (
        <div className="p-5 space-y-4">
          {currencies.length > 1 && (
            <div className="flex items-center gap-2 flex-wrap">
              {currencies.map((currency) => {
                const isActive = currency === active;
                return (
                  <button
                    key={currency}
                    type="button"
                    onClick={() => setSelected(currency)}
                    className={`px-3 py-1.5 rounded-full text-caption font-medium border transition-colors ${
                      isActive
                        ? 'bg-action-light text-action border-action/30'
                        : 'bg-surface text-slate border-border hover:text-navy'
                    }`}
                  >
                    {currency}
                  </button>
                );
              })}
            </div>
          )}

          <StressedLadderChart
            data={chartData}
            showStressed={hasStress}
            height={260}
          />

          <div className="-mx-5 -mb-5 border-t border-border-light">
            <DataTable columns={columns} rows={tableRows} density="compact" />
          </div>
        </div>
      )}
    </SectionCard>
  );
}
