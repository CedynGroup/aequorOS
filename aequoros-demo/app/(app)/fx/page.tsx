import { TrendingUp, TrendingDown } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioGauge from '@/components/ui/RatioGauge';
import { fxPositions, fxKpis, type CurrencyPosition } from '@/lib/data/fx';
import { bank } from '@/lib/data/bank';
import { fmtCurrency, fmtCurrencySigned, fmtPctSigned } from '@/lib/format';

const cols: Column<CurrencyPosition>[] = [
  {
    key: 'ccy',
    header: 'Currency',
    render: (r) => (
      <div>
        <p className="font-mono font-medium text-navy">{r.ccy}</p>
        <p className="text-caption text-slate">{r.ccyName}</p>
      </div>
    ),
    width: '14%',
  },
  { key: 'a', header: 'Assets', numeric: true, render: (r) => fmtCurrency(r.assetsGHS) },
  { key: 'l', header: 'Liabilities', numeric: true, render: (r) => fmtCurrency(r.liabilitiesGHS) },
  { key: 'd', header: 'Derivatives', numeric: true, render: (r) => fmtCurrency(r.derivativesGHS) },
  {
    key: 'n',
    header: 'Net (GHS-eq)',
    numeric: true,
    render: (r) => (
      <span className={r.netGHS >= 0 ? 'text-success' : 'text-warning'}>
        {fmtCurrencySigned(r.netGHS)}
      </span>
    ),
  },
  {
    key: 'pct',
    header: '% capital',
    numeric: true,
    render: (r) => fmtPctSigned(r.netPctOfCapital, 2),
  },
  {
    key: 'spot',
    header: 'Spot',
    numeric: true,
    render: (r) => (
      <div>
        <p className="font-mono">{r.spot < 1 ? r.spot.toFixed(4) : r.spot.toFixed(2)}</p>
        <p
          className={`font-mono text-caption ${
            r.spotChange1d >= 0 ? 'text-success' : 'text-critical'
          } inline-flex items-center gap-0.5 justify-end`}
        >
          {r.spotChange1d >= 0 ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
          {r.spotChange1d >= 0 ? '+' : ''}
          {r.spotChange1d.toFixed(r.spot < 1 ? 4 : 2)}
        </p>
      </div>
    ),
  },
];

export default function FXDashboard() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk' },
          { label: 'Exposure Dashboard' },
        ]}
        title="FX Exposure"
        subtitle="Net open position by currency · BoG NOP framework"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          <div className="lg:col-span-2">
            <RatioGauge
              label="Net Open Position (% of capital)"
              value={fxKpis.netOpenPositionPctCapital}
              threshold={fxKpis.bogLimitPct}
              status="approaching"
              suffix="%"
              decimals={2}
            />
          </div>
          <KPICard
            label="VaR (1d, 99%)"
            value={fxKpis.varDaily99 / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={2}
            footer="Historical simulation, 250d window"
            status="compliant"
          />
          <KPICard
            label="Expected Shortfall"
            value={fxKpis.expectedShortfall / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={2}
            footer="Tail loss beyond 99% VaR"
            status="compliant"
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
          {fxPositions.map((p) => (
            <Card key={p.ccy} className="border-l-2 border-l-action">
              <CardBody className="space-y-2">
                <div className="flex items-baseline justify-between">
                  <span className="font-mono font-semibold text-navy text-h2">
                    {p.ccy}
                  </span>
                  <span className="text-caption text-slate">{p.ccyName}</span>
                </div>
                <p
                  className={`font-mono text-h3 tabular-nums ${
                    p.netGHS >= 0 ? 'text-success' : 'text-warning'
                  }`}
                >
                  {fmtCurrencySigned(p.netGHS)}
                </p>
                <p className="text-caption text-slate">
                  {p.netGHS >= 0 ? 'Long' : 'Short'} · {fmtPctSigned(p.netPctOfCapital, 2)} of capital
                </p>
              </CardBody>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader
            title="Position breakdown"
            subtitle="Assets, liabilities, derivatives, net by currency · GHS equivalent"
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={fxPositions} />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
