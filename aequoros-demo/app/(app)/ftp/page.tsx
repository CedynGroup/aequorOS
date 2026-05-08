import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import KPICard from '@/components/ui/KPICard';
import DataTable, { type Column } from '@/components/ui/DataTable';
import YieldCurveChart from '@/components/charts/YieldCurveChart';
import { yieldCurve, type CurvePoint } from '@/lib/data/ftp';
import { bank } from '@/lib/data/bank';

const cols: Column<CurvePoint>[] = [
  { key: 'tenor', header: 'Tenor', render: (r) => r.tenor },
  { key: 'days', header: 'Days', numeric: true, render: (r) => r.days.toString() },
  { key: 'bog', header: 'BoG / Market', numeric: true, render: (r) => `${r.bog.toFixed(2)}%` },
  { key: 'dep', header: 'Deposit', numeric: true, render: (r) => `${r.deposit.toFixed(2)}%` },
  { key: 'lend', header: 'Lending', numeric: true, render: (r) => `${r.lending.toFixed(2)}%` },
  {
    key: 'ftp',
    header: 'FTP rate',
    numeric: true,
    render: (r) => (
      <span className="font-semibold text-navy">{r.ftp.toFixed(2)}%</span>
    ),
  },
];

export default function YieldCurvePage() {
  const lastUpdated = '01 Apr 2026 06:30 GMT';
  const point3m = yieldCurve.find((p) => p.tenor === '3M')!;
  const point1y = yieldCurve.find((p) => p.tenor === '1Y')!;
  const policyRate = 28.0;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Funds Transfer Pricing' },
          { label: 'Yield Curve' },
        ]}
        title="FTP Yield Curve"
        subtitle="Daily-bootstrapped funding curve · BoG T-bill auction + interbank market"
        asOf={bank.asOf}
        action={
          <span className="text-caption text-slate">
            Last refreshed{' '}
            <span className="font-mono text-navy">{lastUpdated}</span>
          </span>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <KPICard
            label="BoG policy rate"
            value={policyRate}
            suffix="%"
            decimals={2}
            footer="MPR · last reviewed 27 Mar 2026"
          />
          <KPICard
            label="3M T-bill (auction)"
            value={point3m.bog}
            suffix="%"
            decimals={2}
            footer="91-day · weekly auction"
          />
          <KPICard
            label="1Y FTP rate"
            value={point1y.ftp}
            suffix="%"
            decimals={2}
            footer="Internal funding cost"
          />
          <KPICard
            label="Curve steepness (10Y−3M)"
            value={3.5}
            suffix=" pts"
            decimals={2}
            footer="Long-end premium"
          />
        </div>

        <Card>
          <CardHeader
            title="Yield curve construction"
            subtitle="Bootstrap from BoG / market rates · Spread-adjusted FTP curve"
          />
          <CardBody>
            <YieldCurveChart data={yieldCurve} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Curve detail"
            subtitle="Active rates by tenor · FTP applied to all match-funded balances"
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={yieldCurve} />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
