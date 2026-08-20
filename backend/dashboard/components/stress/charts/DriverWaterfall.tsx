'use client';

/**
 * Driver attribution (docs/stress.md §4 items 3 & 6): the stressed capital
 * position decomposed into credit-loss, earnings/FX and RWA-migration drivers,
 * with a drill-down to the loss-by-exposure-class allocation.
 *
 * The bridge is the bank's capital HEADROOM over the 13% CAR requirement
 * (S = Total capital − CAR_target% × RWA, GHS'000) at the final horizon year —
 * the standard ICAAP stress presentation, and the only decomposition that keeps
 * a capital reduction (credit losses, earnings) and an RWA increase (rating
 * migration) on one comparable, exactly-reconciling axis:
 *
 *   S_stress − S_base = (Cs − Cb) − r·(Ds − Db)
 *
 * so the four driver bars sum precisely from the base to the stressed headroom.
 * Reuses the existing `WaterfallChart` (invisible-offset technique). Every input
 * is read from Appendix II Tables 1 & 3 — a labelled client-side decomposition,
 * per the design system's "derivations are labelled" rule.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import WaterfallChart, { type WaterfallStep } from '@/components/forecasting/charts/WaterfallChart';
import SectionCard from '@/components/ui/SectionCard';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrency } from '@/lib/format';
import type { EnterpriseStressRead } from '../types';

function n(value: string | null | undefined): number {
  return value == null ? 0 : num(value);
}

export default function DriverWaterfall({ run }: { run: EnterpriseStressRead }) {
  const [open, setOpen] = useState(false);
  const t1 = run.appendix_ii.table1_summary;
  const t3 = run.appendix_ii.table3_profit_and_loss;
  const base = t1.pre_adverse;
  const stress = t1.post_adverse;

  if (base.length === 0 || stress.length === 0) {
    return null;
  }
  const carTarget = n(t1.car_target_pct) / 100 || 0.13;
  const lastBase = base[base.length - 1];
  const lastStress = stress[stress.length - 1];

  const Cb = n(lastBase.total_regulatory_capital);
  const Cs = n(lastStress.total_regulatory_capital);
  const Db = n(lastBase.total_rwa);
  const Ds = n(lastStress.total_rwa);

  const sBase = Cb - carTarget * Db;
  const sStress = Cs - carTarget * Ds;

  // Cumulative incremental impairment (stress − base) across the horizon (GHS'000).
  const baseImp = t3.filter((r) => r.label.startsWith('base_')).reduce((a, r) => a + n(r.impairment_losses), 0);
  const stressImp = t3.filter((r) => r.label.startsWith('stress_')).reduce((a, r) => a + n(r.impairment_losses), 0);
  const cumIncrImpairment = stressImp - baseImp;

  const creditStep = -cumIncrImpairment;
  const capitalDrop = Cs - Cb;
  const earningsFxStep = capitalDrop - creditStep;
  const rwaStep = -carTarget * (Ds - Db);

  const steps: WaterfallStep[] = [
    { kind: 'total', label: 'Base headroom', value: sBase },
    { kind: 'delta', label: 'Credit losses', value: creditStep },
    { kind: 'delta', label: 'Earnings / FX / tax', value: earningsFxStep },
    { kind: 'delta', label: 'RWA migration', value: rwaStep },
    { kind: 'total', label: 'Stress headroom', value: sStress },
  ];

  const impact = t1.impact_of_adverse[t1.impact_of_adverse.length - 1];
  const losses = (impact?.losses ?? [])
    .map((l) => ({ cls: l.exposure_class, loss: n(l.loss) }))
    .filter((l) => l.loss > 0)
    .sort((a, b) => b.loss - a.loss);
  const maxLoss = Math.max(...losses.map((l) => l.loss), 1);

  return (
    <SectionCard
      title="Driver attribution"
      subtitle="Capital headroom over the 13% CAR requirement, decomposed — final horizon year, GHS'000 (client-derived)"
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
                    {fmtCurrency(l.loss, undefined, { decimals: 0 })}
                  </span>
                </div>
              ))
            )}
            <p className="text-micro text-slate-light pt-1">
              Impact of adverse allocated across the CRD exposure classes (Appendix II Table 1), GHS&apos;000.
            </p>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
