'use client';

/**
 * Management-actions with/without panel (docs/stress.md §4 item 7; ¶67(f),
 * ¶78–81). Reads the run's Appendix II Table 1 management-action blocks — the
 * "results WITH and WITHOUT management actions" the directive requires — and
 * the per-year action rows (capital raised by tier, dividend preserved, RWA
 * relief). No plan modelled ⇒ an explicit prompt, never a fabricated zero.
 */

import { ShieldCheck } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import KpiStat from '@/components/ui/KpiStat';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { num } from '@/lib/api/values';
import type { EnterpriseStressRead, ManagementActionsRow } from './types';

function ghs(v: string | null | undefined): string {
  return v == null ? '—' : num(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export default function ManagementActionsPanel({ run }: { run: EnterpriseStressRead }) {
  const summary = run.summary;
  const t1 = run.appendix_ii.table1_summary;
  const ma = t1.management_actions;

  if (!ma) {
    return (
      <SectionCard title="Management actions" subtitle="Results with and without (¶67(f), ¶78–81)">
        <EmptyState
          Icon={ShieldCheck}
          title="No management-action plan modelled"
          description="Select an approved management-action plan when running the scenario to see the with/without comparison and the Table 1 action blocks (capital raise, dividend policy, RWA relief) — the directive's required distinct output."
        />
      </SectionCard>
    );
  }

  const withoutOk = summary.stress_stays_above_all_minima;
  const withOk = summary.with_actions_stays_above_all_minima ?? ma.stays_above_all_minima;

  const rows = ma.rows;
  const columns: Column<ManagementActionsRow>[] = [
    { key: 'year', header: 'Stress year', render: (r) => <span className="text-navy">Y{r.year}</span> },
    { key: 'cet1', header: 'Capital raise CET1', numeric: true, render: (r) => ghs(r.capital_raised_cet1) },
    { key: 'at1', header: 'AT1', numeric: true, render: (r) => ghs(r.capital_raised_at1) },
    { key: 't2', header: 'Tier 2', numeric: true, render: (r) => ghs(r.capital_raised_tier2) },
    { key: 'div', header: 'Dividend preserved', numeric: true, render: (r) => ghs(r.revision_of_dividend_policy) },
    { key: 'rwa', header: 'RWA relief', numeric: true, render: (r) => ghs(r.rwa_relief_total) },
    { key: 'total', header: 'Total actions', numeric: true, render: (r) => <span className="font-semibold">{ghs(r.total_management_actions)}</span> },
  ];

  return (
    <SectionCard
      title="Management actions — with vs without"
      subtitle={`Plan ${ma.plan_id} · Table 1 action blocks (GHS'000)`}
      actions={
        <StatusPill tone={withOk ? 'success' : 'critical'}>
          {withOk ? 'Restored above all minima' : 'Still below minima'}
        </StatusPill>
      }
    >
      <div className="space-y-5">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KpiStat
            label="Without actions"
            value={withoutOk ? 'Above all minima' : `Breach Y${summary.first_breach_year ?? '—'}`}
            status={withoutOk ? 'ok' : 'crit'}
            hint="Pre-management-action projection"
          />
          <KpiStat
            label="With actions"
            value={withOk ? 'Above all minima' : `Breach Y${summary.with_actions_first_breach_year ?? ma.first_breach_year ?? '—'}`}
            status={withOk ? 'ok' : 'crit'}
            hint="Post-management-action projection"
          />
          <KpiStat
            label="Capital gap (pre-action)"
            value={`GHS'000 ${ghs(t1.capital_gap)}`}
            status={num(t1.capital_gap ?? '0') > 0 ? 'warn' : 'ok'}
            hint="Worst-year residual before actions"
          />
          <KpiStat
            label="Residual after actions"
            value={`GHS'000 ${ghs(summary.residual_capital_required_after_actions)}`}
            status={num(summary.residual_capital_required_after_actions ?? '0') > 0 ? 'warn' : 'ok'}
            hint="Capital still required post-plan"
          />
        </div>
        <DataTable columns={columns} rows={rows} density="compact" emphasizeTotals={false} />
      </div>
    </SectionCard>
  );
}
