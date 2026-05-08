import Link from 'next/link';
import { Download, FileText, AlertCircle } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KPICard from '@/components/ui/KPICard';
import StatusPill from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import HQLAStackChart from '@/components/charts/HQLAStackChart';
import { lcr } from '@/lib/data/liquidity';
import { bank } from '@/lib/data/bank';
import { fmtCurrency, fmtPct } from '@/lib/format';

type OutflowRow = {
  item: string;
  balanceGHS: number;
  runoffPct: number;
  outflowGHS: number;
};

const outflowColumns: Column<OutflowRow>[] = [
  { key: 'item', header: 'Outflow category', render: (r) => r.item, width: '46%' },
  {
    key: 'balance',
    header: 'Balance (GHS)',
    numeric: true,
    render: (r) => fmtCurrency(r.balanceGHS, 'GHS'),
  },
  {
    key: 'runoff',
    header: 'Runoff %',
    numeric: true,
    render: (r) => `${r.runoffPct}%`,
  },
  {
    key: 'outflow',
    header: 'Stressed outflow',
    numeric: true,
    render: (r) => fmtCurrency(r.outflowGHS, 'GHS'),
  },
];

const inflowColumns: Column<{
  item: string;
  balanceGHS: number;
  inflowPct: number;
  inflowGHS: number;
}>[] = [
  { key: 'item', header: 'Inflow category', render: (r) => r.item, width: '46%' },
  {
    key: 'balance',
    header: 'Balance (GHS)',
    numeric: true,
    render: (r) => fmtCurrency(r.balanceGHS, 'GHS'),
  },
  {
    key: 'inflow',
    header: 'Inflow %',
    numeric: true,
    render: (r) => `${r.inflowPct}%`,
  },
  {
    key: 'gross',
    header: 'Gross inflow',
    numeric: true,
    render: (r) => fmtCurrency(r.inflowGHS, 'GHS'),
  },
];

export default function LCRDashboard() {
  const totalOutflows = lcr.outflows.reduce((s, o) => s + o.outflowGHS, 0);
  const totalGrossInflows = lcr.inflows.reduce((s, o) => s + o.inflowGHS, 0);
  const cappedInflows = Math.min(totalGrossInflows, totalOutflows * 0.75);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk' },
          { label: 'LCR Dashboard' },
        ]}
        title="Liquidity Coverage Ratio"
        subtitle="Basel III LCR per Bank of Ghana CRD framework · 30-day stressed horizon"
        asOf={bank.asOf}
        action={
          <div className="flex items-center gap-2">
            <Link
              href="/liquidity/submission"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
            >
              <FileText size={13} aria-hidden />
              Generate BoG return
            </Link>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700"
            >
              <Download size={13} aria-hidden />
              Export
            </button>
          </div>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {/* Top row: ratio gauge + 3 KPIs */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          <div className="lg:col-span-2">
            <RatioGauge
              label="Liquidity Coverage Ratio"
              value={lcr.current}
              threshold={lcr.threshold}
              internalBuffer={lcr.internalBuffer}
              status="compliant"
            />
          </div>
          <KPICard
            label="HQLA stock"
            value={lcr.hqlaTotalGHS / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={1}
            delta={+8.4}
            deltaSuffix="M"
            status="compliant"
            sparkline={[221, 230, 233, 245, 246, 250, 252, 256]}
          />
          <KPICard
            label="30-day net outflows"
            value={lcr.netOutflowsGHS / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={1}
            delta={-2.3}
            deltaSuffix="M"
            status="compliant"
            sparkline={[176, 178, 180, 175, 174, 178, 182, 180]}
          />
        </div>

        {/* HQLA composition + 12-month trend */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader
              title="LCR — 12-month trend"
              subtitle="Daily LCR run, month-end values"
              action={<StatusPill tone="success">Compliant 12 of 12</StatusPill>}
            />
            <CardBody>
              <RatioHistoryChart
                data={lcr.history}
                threshold={lcr.threshold}
                internalBuffer={lcr.internalBuffer}
                color="#0E8A4F"
                label="LCR"
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="HQLA composition"
              subtitle="Post-haircut by Basel III level"
            />
            <CardBody className="space-y-4">
              <HQLAStackChart data={lcr.hqlaBreakdown} />
              <ul className="space-y-2 text-caption pt-2 border-t border-border-light">
                {lcr.hqlaBreakdown.map((h) => (
                  <li key={h.level} className="flex items-center gap-3">
                    <span
                      className="w-2 h-2 rounded-sm shrink-0"
                      style={{ background: h.color }}
                      aria-hidden
                    />
                    <span className="font-medium text-navy w-16 shrink-0">
                      {h.level}
                    </span>
                    <span className="text-slate flex-1 truncate">{h.label}</span>
                    <span className="font-mono text-navy tabular-nums shrink-0">
                      {fmtCurrency(h.shareGHS, 'GHS')}
                    </span>
                    <span className="font-mono text-slate tabular-nums w-10 text-right shrink-0">
                      {h.pct}%
                    </span>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </div>

        {/* Outflow & inflow tables */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader
              title="Cash outflows"
              subtitle="30-day stressed runoff per BoG CRD weights"
            />
            <CardBody className="p-0">
              <DataTable
                columns={outflowColumns}
                rows={[
                  ...lcr.outflows,
                  {
                    item: 'TOTAL CASH OUTFLOWS',
                    balanceGHS: lcr.outflows.reduce((s, o) => s + o.balanceGHS, 0),
                    runoffPct: 0,
                    outflowGHS: totalOutflows,
                  } as OutflowRow,
                ]}
                totalsRowMatcher={(r) => r.item.startsWith('TOTAL')}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Cash inflows"
              subtitle="Capped at 75% of outflows per Basel III"
            />
            <CardBody className="p-0">
              <DataTable
                columns={inflowColumns}
                rows={[
                  ...lcr.inflows,
                  {
                    item: 'GROSS INFLOWS',
                    balanceGHS: lcr.inflows.reduce((s, o) => s + o.balanceGHS, 0),
                    inflowPct: 0,
                    inflowGHS: totalGrossInflows,
                  },
                  {
                    item: 'CAPPED INFLOWS (min of gross, 75% of outflows)',
                    balanceGHS: 0,
                    inflowPct: 0,
                    inflowGHS: cappedInflows,
                  },
                ]}
                totalsRowMatcher={(r) =>
                  r.item.startsWith('GROSS') || r.item.startsWith('CAPPED')
                }
              />
            </CardBody>
          </Card>
        </div>

        {/* Watchlist banner */}
        <div className="card border-l-4 border-l-warning bg-warning-light/40 p-5 flex items-start gap-3">
          <AlertCircle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <div>
            <p className="text-body font-medium text-navy">
              Watch item: Non-operational wholesale concentration
            </p>
            <p className="mt-1 text-body text-navy/80 leading-relaxed">
              GHS 140M (8.7% of deposit base) sits in non-operational wholesale —
              all assumed to run off in 30 days under BoG stress. Two
              counterparties account for 62% of that exposure. Recommend
              diversification or contractual term extension before next quarter.
            </p>
            <p className="mt-2 text-caption text-slate">
              Reviewed by Akua Mensah · Reference: ALCO Q1-2026 minutes, item 4.2
            </p>
          </div>
        </div>

        {/* Compliance summary line */}
        <p className="text-caption text-slate flex items-center gap-2">
          Net outflows = Outflows{' '}
          <span className="font-mono text-navy">
            {fmtCurrency(totalOutflows, 'GHS')}
          </span>{' '}
          − min(Gross inflows, 75% × Outflows){' '}
          <span className="font-mono text-navy">
            {fmtCurrency(cappedInflows, 'GHS')}
          </span>{' '}
          ={' '}
          <span className="font-mono font-medium text-navy">
            {fmtCurrency(lcr.netOutflowsGHS, 'GHS')}
          </span>
          . LCR = HQLA{' '}
          <span className="font-mono text-navy">
            {fmtCurrency(lcr.hqlaTotalGHS, 'GHS')}
          </span>{' '}
          / Net outflows ={' '}
          <span className="font-mono font-medium text-success">
            {fmtPct(lcr.current, 1)}
          </span>
          .
        </p>
      </div>
    </>
  );
}
