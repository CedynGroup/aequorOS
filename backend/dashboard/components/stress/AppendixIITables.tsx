'use client';

/**
 * Appendix II Tables 1–6 (docs/stress.md §1.8) — the BoG stress-test regulatory
 * deliverable, rendered from an immutable enterprise-stress run's `appendix_ii`
 * payload. Amounts are GHS'000 (the directive unit); ratios are percentages.
 *
 * The serialised tables are snapshot-oriented (a list of period columns, each
 * with named line fields); this transposes them into line-item rows × period
 * columns for reading and for the board pack.
 */

import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import SubTabs from '@/components/ui/SubTabs';
import { type ReactNode, useState } from 'react';
import { num, labelize } from '@/lib/api/values';
import type {
  AppendixIITables as Tables,
  CapitalSnapshot,
  Table2Row,
  Table3Row,
  Table4Row,
  Table5Row,
} from './types';
import { MACRO_VAR_BY_KEY, fmtMacro } from './macro';

function colLabel(label: string): string {
  if (label === 'current') return 'Current';
  const m = /^(base|stress)_y(\d+)$/.exec(label);
  if (m) return `${m[1] === 'base' ? 'Base' : 'Stress'} Y${m[2]}`;
  return labelize(label);
}

function ghs(v: string | null | undefined): string {
  if (v == null) return '—';
  return num(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function pct(v: string | null | undefined): string {
  return v == null ? '—' : `${num(v).toFixed(2)}%`;
}

type LineDef<T> = {
  label: string;
  get: (row: T) => string | null | undefined;
  kind?: 'ghs' | 'pct';
  bold?: boolean;
  indent?: boolean;
};

/** Transpose column-oriented snapshots into a line-item × period matrix. */
function MatrixTable<T extends { label: string }>({
  cols,
  lines,
  bounded = true,
}: {
  cols: T[];
  lines: LineDef<T>[];
  /** Constrain height + sticky header. Off for print (board pack) so no rows clip. */
  bounded?: boolean;
}) {
  type RowT = { line: string; bold?: boolean; indent?: boolean; kind?: 'ghs' | 'pct'; cells: string[] };
  const rows: RowT[] = lines.map((ln) => ({
    line: ln.label,
    bold: ln.bold,
    indent: ln.indent,
    kind: ln.kind,
    cells: cols.map((c) => (ln.kind === 'pct' ? pct(ln.get(c)) : ghs(ln.get(c)))),
  }));
  const columns: Column<RowT>[] = [
    {
      key: 'line',
      header: 'Line item',
      render: (r) => (
        <span className={`${r.bold ? 'font-semibold text-navy' : 'text-navy'} ${r.indent ? 'pl-4' : ''}`}>
          {r.line}
        </span>
      ),
    },
    ...cols.map((c, i) => ({
      key: c.label,
      header: colLabel(c.label),
      numeric: true,
      render: (r: RowT) => <span className={r.bold ? 'font-semibold' : ''}>{r.cells[i]}</span>,
    })),
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      density="compact"
      emphasizeTotals={false}
      stickyHeader={bounded}
      maxHeight={bounded ? 520 : undefined}
    />
  );
}

function Table1({ tables, bounded = true }: { tables: Tables; bounded?: boolean }) {
  const t1 = tables.table1_summary;
  const snaps: CapitalSnapshot[] = [t1.current, ...t1.pre_adverse, ...t1.post_adverse];
  const lines: LineDef<CapitalSnapshot>[] = [
    { label: 'CET1 capital', get: (s) => s.cet1 },
    { label: 'Tier 1 capital', get: (s) => s.tier1 },
    { label: 'Tier 2 capital', get: (s) => s.tier2 },
    { label: 'Total regulatory capital', get: (s) => s.total_regulatory_capital, bold: true },
    { label: 'Total RWA', get: (s) => s.total_rwa },
    { label: 'CET1 ratio', get: (s) => s.cet1_ratio_pct, kind: 'pct' },
    { label: 'Tier 1 ratio', get: (s) => s.tier1_ratio_pct, kind: 'pct' },
    { label: 'CAR', get: (s) => s.car_pct, kind: 'pct', bold: true },
    { label: 'Paid-up capital', get: (s) => s.paid_up },
  ];
  const lastImpact = t1.impact_of_adverse[t1.impact_of_adverse.length - 1];
  const ma = t1.management_actions;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Capital gap (pre-action)" value={`GHS'000 ${ghs(t1.capital_gap)}`} tone="critical" />
        <Metric label="CAR target" value={pct(t1.car_target_pct)} />
        <Metric label="Paid-up minimum" value={`GHS'000 ${ghs(t1.paid_up_min)}`} />
        <Metric
          label="Residual after actions"
          value={
            t1.residual_capital_required_after_actions
              ? `GHS'000 ${ghs(t1.residual_capital_required_after_actions.worst)}`
              : 'No plan modelled'
          }
          tone={ma ? (ma.stays_above_all_minima ? 'success' : 'amber') : 'slate'}
        />
      </div>
      <MatrixTable cols={snaps} lines={lines} bounded={bounded} />
      {lastImpact && (
        <div>
          <p className="text-caption font-medium uppercase tracking-wider text-slate mb-2">
            Impact of adverse — losses by CRD exposure class (stress Y{lastImpact.year}, GHS&apos;000)
          </p>
          <div className="flex flex-wrap gap-2">
            {lastImpact.losses
              .map((l) => ({ cls: l.exposure_class, loss: num(l.loss ?? '0') }))
              .filter((l) => l.loss > 0)
              .sort((a, b) => b.loss - a.loss)
              .map((l) => (
                <span
                  key={l.cls}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border-light px-2.5 py-1 text-caption"
                >
                  <span className="text-slate">{labelize(l.cls)}</span>
                  <span className="tnum text-navy font-medium">{l.loss.toLocaleString()}</span>
                </span>
              ))}
          </div>
        </div>
      )}
      {ma && t1.post_capitalisation && (
        <div>
          <p className="text-caption font-medium uppercase tracking-wider text-slate mb-2">
            Post-capitalisation (with management actions, GHS&apos;000)
          </p>
          <MatrixTable
            bounded={bounded}
            cols={t1.post_capitalisation}
            lines={[
              { label: 'Total regulatory capital', get: (s) => s.total_regulatory_capital, bold: true },
              { label: 'Total RWA', get: (s) => s.total_rwa },
              { label: 'CAR', get: (s) => s.car_pct, kind: 'pct', bold: true },
              { label: 'CET1 ratio', get: (s) => s.cet1_ratio_pct, kind: 'pct' },
            ]}
          />
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'amber' | 'critical' | 'slate' }) {
  return (
    <div className="card px-3 py-2.5">
      <p className="text-micro uppercase tracking-wider text-slate">{label}</p>
      <p className={`mt-0.5 text-body font-semibold tnum ${tone === 'critical' ? 'text-critical' : tone === 'amber' ? 'text-warning' : tone === 'success' ? 'text-success' : 'text-navy'}`}>
        {value}
      </p>
    </div>
  );
}

function Table2({ rows, bounded = true }: { rows: Table2Row[]; bounded?: boolean }) {
  const lines: LineDef<Table2Row>[] = [
    { label: 'Paid-up capital', get: (r) => r.cet1.paid_up, indent: true },
    { label: 'Retained earnings', get: (r) => r.cet1.retained_earnings, indent: true },
    { label: 'Statutory reserves', get: (r) => r.cet1.statutory_reserves, indent: true },
    { label: 'Other reserves', get: (r) => r.cet1.other_reserves, indent: true },
    { label: 'Minority interest', get: (r) => r.cet1.minority_interest, indent: true },
    { label: 'Other CET1', get: (r) => r.cet1.other_cet1, indent: true },
    { label: 'Gross CET1', get: (r) => r.cet1.gross_cet1, bold: true },
    { label: 'Deduction: intangibles', get: (r) => r.cet1.deduction_intangibles, indent: true },
    { label: 'Deduction: FI investments', get: (r) => r.cet1.deduction_fi_investments, indent: true },
    { label: 'Deduction: OCI', get: (r) => r.cet1.deduction_oci, indent: true },
    { label: 'Deduction: DTA', get: (r) => r.cet1.deduction_dta, indent: true },
    { label: 'Deduction: commercial entity', get: (r) => r.cet1.deduction_commercial_entity, indent: true },
    { label: 'Deduction: other', get: (r) => r.cet1.deduction_other, indent: true },
    { label: 'Total deductions', get: (r) => r.cet1.total_deductions },
    { label: 'CET1 after deductions', get: (r) => r.cet1.cet1_after_deductions, bold: true },
    { label: 'AT1 (eligible)', get: (r) => r.at1_eligible },
    { label: 'Tier 2 (eligible)', get: (r) => r.tier2_eligible },
    { label: 'Total regulatory capital', get: (r) => r.total_regulatory_capital, bold: true },
    { label: 'Credit risk reserve', get: (r) => r.credit_risk_reserve },
    { label: 'Total RWA', get: (r) => r.total_rwa },
  ];
  return <MatrixTable cols={rows} lines={lines} bounded={bounded} />;
}

function Table3({ rows, bounded = true }: { rows: Table3Row[]; bounded?: boolean }) {
  const lines: LineDef<Table3Row>[] = [
    { label: 'Opening retained earnings', get: (r) => r.opening_retained_earnings },
    { label: 'Net interest income', get: (r) => r.net_interest_income },
    { label: 'Fees & commissions', get: (r) => r.fees_and_commissions },
    { label: 'Operating expenses', get: (r) => r.operating_expenses },
    { label: 'Impairment losses', get: (r) => r.impairment_losses },
    { label: 'Profit before tax', get: (r) => r.profit_before_tax, bold: true },
    { label: 'Tax', get: (r) => r.tax },
    { label: 'Profit after tax', get: (r) => r.profit_after_tax, bold: true },
    { label: 'Distributions', get: (r) => r.distributions },
    { label: 'Adjusted retained earnings (for CAR)', get: (r) => r.adjusted_retained_earnings_for_car, bold: true },
  ];
  return <MatrixTable cols={rows} lines={lines} bounded={bounded} />;
}

function Table4({ rows, bounded = true }: { rows: Table4Row[]; bounded?: boolean }) {
  const lines: LineDef<Table4Row>[] = [
    { label: 'Cash & balances', get: (r) => r.cash_and_balances, indent: true },
    { label: 'Short-term investments', get: (r) => r.short_term_investments, indent: true },
    { label: 'Loans', get: (r) => r.loans, indent: true },
    { label: 'Other assets', get: (r) => r.other_assets, indent: true },
    { label: 'Total assets', get: (r) => r.total_assets, bold: true },
    { label: 'Demand deposits', get: (r) => r.demand_deposits, indent: true },
    { label: 'Savings deposits', get: (r) => r.savings_deposits, indent: true },
    { label: 'Time deposits', get: (r) => r.time_deposits, indent: true },
    { label: 'Other deposits', get: (r) => r.other_deposits, indent: true },
    { label: 'Borrowings', get: (r) => r.borrowings, indent: true },
    { label: 'Total liabilities', get: (r) => r.total_liabilities, bold: true },
    { label: 'Capital', get: (r) => r.capital, bold: true },
  ];
  return <MatrixTable cols={rows} lines={lines} bounded={bounded} />;
}

function Table5({ rows, bounded = true }: { rows: Table5Row[]; bounded?: boolean }) {
  const lines: LineDef<Table5Row>[] = [
    { label: 'Credit RWA', get: (r) => r.credit_rwa, indent: true },
    { label: 'Operational RWA', get: (r) => r.operational_rwa, indent: true },
    { label: 'Market RWA', get: (r) => r.market_rwa, indent: true },
    { label: 'Total Pillar-1 RWA', get: (r) => r.total_pillar1_rwa, bold: true },
    { label: 'Pillar-1 requirement', get: (r) => r.pillar1_requirement },
    { label: 'Pillar-2: credit concentration', get: (r) => r.pillar2.credit_concentration, indent: true },
    { label: 'Pillar-2: IRRBB', get: (r) => r.pillar2.irrbb, indent: true },
    { label: 'Pillar-2: country & FX', get: (r) => r.pillar2.country_and_fx, indent: true },
    { label: 'Pillar-2: other', get: (r) => r.pillar2.other, indent: true },
    { label: 'Pillar-2 total', get: (r) => r.pillar2.total },
    { label: 'Total capital requirement', get: (r) => r.total_capital_requirement, bold: true },
  ];
  return <MatrixTable cols={rows} lines={lines} bounded={bounded} />;
}

function Table6({ tables }: { tables: Tables }) {
  const rows = tables.table6_risk_drivers.rows;
  // Group by variable → year columns.
  const years = Array.from(new Set(rows.map((r) => r.year_index))).sort((a, b) => a - b);
  const byVar = new Map<string, Map<number, { base: string | null; stress: string | null }>>();
  for (const r of rows) {
    if (!byVar.has(r.variable)) byVar.set(r.variable, new Map());
    byVar.get(r.variable)!.set(r.year_index, { base: r.base_value, stress: r.stress_value });
  }
  type RowT = { variable: string };
  const dataRows: RowT[] = Array.from(byVar.keys()).map((variable) => ({ variable }));
  const columns: Column<RowT>[] = [
    {
      key: 'variable',
      header: 'Macro driver',
      render: (r) => <span className="text-navy">{MACRO_VAR_BY_KEY[r.variable]?.label ?? labelize(r.variable)}</span>,
    },
    ...years.flatMap((y) => [
      {
        key: `b${y}`,
        header: `Base Y${y}`,
        numeric: true,
        render: (r: RowT) => {
          const meta = MACRO_VAR_BY_KEY[r.variable];
          const cell = byVar.get(r.variable)?.get(y);
          return <span>{cell?.base != null ? fmtMacro(meta, num(cell.base)) : '—'}</span>;
        },
      },
      {
        key: `s${y}`,
        header: `Stress Y${y}`,
        numeric: true,
        render: (r: RowT) => {
          const meta = MACRO_VAR_BY_KEY[r.variable];
          const cell = byVar.get(r.variable)?.get(y);
          return <span className="text-critical">{cell?.stress != null ? fmtMacro(meta, num(cell.stress)) : '—'}</span>;
        },
      },
    ]),
  ];
  return (
    <div className="space-y-2">
      <DataTable columns={columns} rows={dataRows} density="compact" emphasizeTotals={false} />
      {tables.table6_risk_drivers.source && (
        <p className="text-micro text-slate px-1">Sources: {tables.table6_risk_drivers.source}</p>
      )}
    </div>
  );
}

const TAB_ITEMS = [
  { key: 't1', label: 'T1 · Summary' },
  { key: 't2', label: 'T2 · Capital' },
  { key: 't3', label: 'T3 · P&L' },
  { key: 't4', label: 'T4 · Balance sheet' },
  { key: 't5', label: 'T5 · RWA' },
  { key: 't6', label: 'T6 · Drivers' },
];

export default function AppendixIITables({
  tables,
  flat = false,
}: {
  tables: Tables;
  /** Board-pack mode: render every table stacked (for print) instead of tabbed. */
  flat?: boolean;
}) {
  const [tab, setTab] = useState('t1');
  if (flat) {
    return (
      <div className="space-y-6">
        <Section title="Table 1 — Summary Results"><Table1 tables={tables} bounded={false} /></Section>
        <Section title="Table 2 — Regulatory Capital Projection"><Table2 rows={tables.table2_capital} bounded={false} /></Section>
        <Section title="Table 3 — Movement in P&L"><Table3 rows={tables.table3_profit_and_loss} bounded={false} /></Section>
        <Section title="Table 4 — Statement of Financial Position"><Table4 rows={tables.table4_financial_position} bounded={false} /></Section>
        <Section title="Table 5 — Evolution of RWA & Capital Requirements"><Table5 rows={tables.table5_rwa.rows} bounded={false} /></Section>
        <Section title="Table 6 — Key Risk Drivers & Assumptions"><Table6 tables={tables} /></Section>
      </div>
    );
  }
  return (
    <SectionCard
      title="Appendix II — regulatory deliverable"
      subtitle={`Tables 1–6 · ${tables.unit} · the BoG ICAAP stress submission format`}
    >
      <div className="space-y-4">
        <SubTabs items={TAB_ITEMS} active={tab} onChange={setTab} />
        {tab === 't1' && <Table1 tables={tables} />}
        {tab === 't2' && <Table2 rows={tables.table2_capital} />}
        {tab === 't3' && <Table3 rows={tables.table3_profit_and_loss} />}
        {tab === 't4' && <Table4 rows={tables.table4_financial_position} />}
        {tab === 't5' && <Table5 rows={tables.table5_rwa.rows} />}
        {tab === 't6' && <Table6 tables={tables} />}
      </div>
    </SectionCard>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <SectionCard title={title}>
      {children}
    </SectionCard>
  );
}
