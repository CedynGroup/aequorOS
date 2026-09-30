'use client';

/**
 * Driver attribution (docs/stress.md §4 items 3 & 6): the stressed capital
 * position decomposed into credit-loss, earnings/FX and RWA-migration drivers,
 * with a drill-down to the loss-by-exposure-class allocation.
 *
 * The bridge is the bank's capital HEADROOM over the run's own CAR requirement
 * (S = Total capital − CAR_target% × RWA) at the final horizon year — the
 * standard ICAAP stress presentation, and the only decomposition that keeps a
 * capital reduction (credit losses, earnings) and an RWA increase (rating
 * migration) on one comparable, exactly-reconciling axis:
 *
 *   S_stress − S_base = (Cs − Cb) − r·(Ds − Db)
 *
 * so the four driver bars sum precisely from the base to the stressed headroom.
 * Reuses the existing `WaterfallChart` (invisible-offset technique). Every input
 * is read from Appendix II Tables 1 & 3 — a labelled client-side decomposition,
 * per the design system's "derivations are labelled" rule.
 *
 * A MISSING INPUT IS NOT A ZERO. Every figure the bridge reads is nullable on
 * the wire, and this component used to map each null to 0 before subtracting.
 * A null final-year post-adverse capital then drew the stressed headroom at
 * −(CAR target × RWA): a fabricated wipe-out, on the stress board, in the
 * colour of a real one. The fail-open guard's rule P0-23 lists ten field names
 * and knows none of these, so nothing caught it. Now every input is read with
 * `numOrNull`, and a run with any of them absent is told which figure is
 * missing and gets no bridge — the same treatment the CAR requirement already
 * had.
 *
 * UNITS. Appendix II amounts are in THOUSANDS of the reporting currency
 * (`appendix_ii.unit`), but `WaterfallChart` and `fmtCurrency` apply their own
 * K/M/B compaction to a value in CURRENCY UNITS. Passing thousands straight
 * through understated the whole bridge by 1000× — a base headroom of 450,000
 * (GHS 450m) rendered as "GHS 450.0K". Everything handed to a currency
 * formatter here is therefore rescaled to units first, via `toUnits()`.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import WaterfallChart, { type WaterfallStep } from '@/components/forecasting/charts/WaterfallChart';
import SectionCard from '@/components/ui/SectionCard';
import { fmtFloorPct, labelize, numOrNull } from '@/lib/api/values';
import { fmtCurrency } from '@/lib/format';
import type { EnterpriseStressRead } from '../types';

/** Appendix II thousands → currency units, for the currency formatters. */
const THOUSANDS_TO_UNITS = 1_000;
function toUnits(thousands: number): number {
  return thousands * THOUSANDS_TO_UNITS;
}

/**
 * The impairment losses of one projection, summed across its horizon — or null
 * when any year is absent, or when there are no years at all. An empty set of
 * rows is not "no losses": it is a P&L table that was not computed, and summing
 * it to 0 would attribute the whole capital movement to earnings.
 */
function impairmentTotal(
  rows: readonly { impairment_losses: string | null }[],
): number | null {
  if (rows.length === 0) return null;
  let total = 0;
  for (const row of rows) {
    const value = numOrNull(row.impairment_losses);
    if (value === null) return null;
    total += value;
  }
  return total;
}

function listMissing(names: readonly string[]): string {
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

const SUBTITLE = "Capital headroom over the run's CAR requirement, decomposed — final horizon year";

export default function DriverWaterfall({ run }: { run: EnterpriseStressRead }) {
  const [open, setOpen] = useState(false);
  const t1 = run.appendix_ii.table1_summary;
  const t3 = run.appendix_ii.table3_profit_and_loss;
  const base = t1.pre_adverse;
  const stress = t1.post_adverse;

  if (base.length === 0 || stress.length === 0) {
    return null;
  }
  // The whole bridge is defined RELATIVE to the run's capital requirement. With
  // no requirement on the run there is no headroom to decompose — say so
  // instead of falling back to an invented 13%, which silently rebased every
  // bar against a threshold the institution may not be held to.
  const carTargetPct = numOrNull(t1.car_target_pct);
  if (carTargetPct === null || carTargetPct <= 0) {
    return (
      <SectionCard title="Driver attribution" subtitle={SUBTITLE}>
        <p className="text-body text-slate">
          This run carries no capital-adequacy requirement, so the headroom bridge cannot be
          derived. No requirement is assumed on the institution&apos;s behalf.
        </p>
      </SectionCard>
    );
  }
  const carTarget = carTargetPct / 100;
  const lastBase = base[base.length - 1];
  const lastStress = stress[stress.length - 1];

  const Cb = numOrNull(lastBase.total_regulatory_capital);
  const Cs = numOrNull(lastStress.total_regulatory_capital);
  const Db = numOrNull(lastBase.total_rwa);
  const Ds = numOrNull(lastStress.total_rwa);

  // Cumulative impairment across the horizon, in the Appendix II unit
  // (thousands) like every figure above.
  const baseImp = impairmentTotal(t3.filter((r) => r.label.startsWith('base_')));
  const stressImp = impairmentTotal(t3.filter((r) => r.label.startsWith('stress_')));

  const missing: string[] = [];
  if (Cb === null) missing.push('total regulatory capital before the adverse scenario');
  if (Cs === null) missing.push('total regulatory capital after the adverse scenario');
  if (Db === null) missing.push('risk-weighted assets before the adverse scenario');
  if (Ds === null) missing.push('risk-weighted assets after the adverse scenario');
  if (baseImp === null) missing.push('impairment losses in the base projection');
  if (stressImp === null) missing.push('impairment losses in the adverse projection');
  if (
    Cb === null ||
    Cs === null ||
    Db === null ||
    Ds === null ||
    baseImp === null ||
    stressImp === null
  ) {
    return (
      <SectionCard title="Driver attribution" subtitle={SUBTITLE}>
        <p className="text-body text-slate">
          The headroom bridge cannot be derived for this run: it carries no final-year figure
          for {listMissing(missing)}. Nothing is drawn in its place — a missing figure is not a
          zero.
        </p>
      </SectionCard>
    );
  }

  const sBase = Cb - carTarget * Db;
  const sStress = Cs - carTarget * Ds;
  const cumIncrImpairment = stressImp - baseImp;

  const creditStep = -cumIncrImpairment;
  const capitalDrop = Cs - Cb;
  const earningsFxStep = capitalDrop - creditStep;
  const rwaStep = -carTarget * (Ds - Db);

  // Rescaled to currency units: WaterfallChart's axis and tooltip run the values
  // through fmtCurrency, which compacts to K/M/B on its own.
  const steps: WaterfallStep[] = [
    { kind: 'total', label: 'Base headroom', value: toUnits(sBase) },
    { kind: 'delta', label: 'Credit losses', value: toUnits(creditStep) },
    { kind: 'delta', label: 'Earnings / FX / tax', value: toUnits(earningsFxStep) },
    { kind: 'delta', label: 'RWA migration', value: toUnits(rwaStep) },
    { kind: 'total', label: 'Stress headroom', value: toUnits(sStress) },
  ];

  const impact = t1.impact_of_adverse[t1.impact_of_adverse.length - 1];
  // An exposure class with no allocated loss is left out, never drawn as 0.
  const losses = (impact?.losses ?? [])
    .flatMap((l) => {
      const loss = numOrNull(l.loss);
      return loss === null || loss <= 0 ? [] : [{ cls: l.exposure_class, loss }];
    })
    .sort((a, b) => b.loss - a.loss);
  const maxLoss = Math.max(...losses.map((l) => l.loss), 1);

  return (
    <SectionCard
      title="Driver attribution"
      subtitle={`Capital headroom over the run's ${fmtFloorPct(carTargetPct)} CAR requirement, decomposed — final horizon year (client-derived)`}
    >
      <WaterfallChart steps={steps} height={300} />
      <div className="mt-3 border-t border-border-light pt-3">
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-caption font-medium text-action hover:underline"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Loss attribution by exposure class {impact ? `(stress Y${impact.year})` : ''}
        </button>
        {open && (
          <div className="mt-3 space-y-1.5">
            {losses.length === 0 ? (
              <p className="text-caption text-slate">No incremental credit loss allocated in this scenario.</p>
            ) : (
              losses.map((l) => (
                <div key={l.cls} className="flex items-center gap-3">
                  <span className="w-40 shrink-0 text-caption text-navy truncate">{labelize(l.cls)}</span>
                  <div className="flex-1 h-2 rounded-full bg-surface overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${(l.loss / maxLoss) * 100}%`, background: 'rgb(var(--crit) / 0.7)' }}
                    />
                  </div>
                  <span className="w-24 shrink-0 text-right text-caption tnum text-navy">
                    {fmtCurrency(toUnits(l.loss), undefined, { decimals: 1 })}
                  </span>
                </div>
              ))
            )}
            <p className="text-micro text-slate-light pt-1">
              Impact of adverse allocated across the CRD exposure classes (Appendix II Table 1).
            </p>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
