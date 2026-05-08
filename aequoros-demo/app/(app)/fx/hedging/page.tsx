import { Calendar } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import KPICard from '@/components/ui/KPICard';
import { fxHedges, fxKpis, type FxHedge } from '@/lib/data/fx';
import { bank } from '@/lib/data/bank';
import { fmtCurrencySigned, fmtCurrency } from '@/lib/format';

const statusTone: Record<FxHedge['status'], StatusTone> = {
  Active: 'success',
  Expiring: 'amber',
  Drift: 'critical',
};

const cols: Column<FxHedge>[] = [
  {
    key: 'id',
    header: 'Contract',
    render: (r) => <span className="font-mono text-caption">{r.id}</span>,
  },
  { key: 'type', header: 'Type', render: (r) => r.type },
  { key: 'pair', header: 'Pair', render: (r) => <span className="font-mono">{r.pair}</span> },
  {
    key: 'notional',
    header: 'Notional',
    numeric: true,
    render: (r) => `${r.ccy === 'USD' ? '$' : r.ccy + ' '}${(r.notional / 1_000_000).toFixed(1)}M`,
  },
  { key: 'rate', header: 'Rate', numeric: true, render: (r) => r.rate.toFixed(2) },
  { key: 'eff', header: 'Effective', render: (r) => r.effectiveDate },
  { key: 'mat', header: 'Maturity', render: (r) => r.maturity },
  { key: 'days', header: 'Days', numeric: true, render: (r) => `${r.daysToMaturity}d` },
  {
    key: 'mtm',
    header: 'MTM',
    numeric: true,
    render: (r) => (
      <span className={r.mtmGHS >= 0 ? 'text-success' : 'text-critical'}>
        {fmtCurrencySigned(r.mtmGHS)}
      </span>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    align: 'right',
    render: (r) => <StatusPill tone={statusTone[r.status]}>{r.status}</StatusPill>,
  },
];

export default function FXHedging() {
  const expiring30d = fxHedges.filter((h) => h.daysToMaturity <= 30);
  const totalMtm = fxHedges.reduce((s, h) => s + h.mtmGHS, 0);
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk', href: '/fx' },
          { label: 'Hedging Dashboard' },
        ]}
        title="Hedging Dashboard"
        subtitle="Active hedges, expiring positions, and recommended restructure"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <KPICard
            label="Hedge ratio"
            value={fxKpis.hedgeRatio * 100}
            suffix="%"
            decimals={0}
            footer="Of FX-denominated exposure"
            status="compliant"
          />
          <KPICard
            label="Active contracts"
            value={fxHedges.length}
            decimals={0}
            footer={`${expiring30d.length} expiring in 30d`}
            status={expiring30d.length > 1 ? 'approaching' : 'compliant'}
          />
          <KPICard
            label="Net MTM"
            value={totalMtm / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={2}
            footer="Across all FX hedges"
            status={totalMtm >= 0 ? 'compliant' : 'critical'}
          />
          <KPICard
            label="Largest exposure"
            value={fxKpis.netOpenPositionPctCapital}
            suffix="%"
            decimals={2}
            footer="USD long, % of capital"
            status="approaching"
          />
        </div>

        <Card>
          <CardHeader
            title="Expiring within 30 days"
            subtitle="Roll, restructure, or unwind decisions"
            action={
              <span className="inline-flex items-center gap-2 text-caption text-warning font-medium">
                <Calendar size={13} aria-hidden /> 30-day window
              </span>
            }
          />
          <CardBody className="p-0">
            {expiring30d.length === 0 ? (
              <p className="px-5 py-6 text-body text-slate">No hedges expiring in next 30 days.</p>
            ) : (
              <DataTable columns={cols} rows={expiring30d} />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="All active hedges" subtitle="Forward, IRS, cross-currency, options" />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={fxHedges} />
          </CardBody>
        </Card>

        <p className="text-caption text-slate">
          Total notional GHS-equivalent:{' '}
          <span className="font-mono text-navy">
            {fmtCurrency(
              fxHedges.reduce(
                (s, h) =>
                  s + (h.ccy === 'USD' ? h.notional * bank.ghsUsd : h.notional),
                0
              )
            )}
          </span>
          . SA-CCR exposure recalculated daily; central clearing where eligible.
        </p>
      </div>
    </>
  );
}
