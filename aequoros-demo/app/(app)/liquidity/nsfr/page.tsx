import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KPICard from '@/components/ui/KPICard';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { nsfr } from '@/lib/data/liquidity';
import { bank } from '@/lib/data/bank';
import { fmtCurrency } from '@/lib/format';

type AsfRow = {
  item: string;
  balanceGHS: number;
  factor: number;
  asfGHS: number;
};

type RsfRow = {
  item: string;
  balanceGHS: number;
  factor: number;
  rsfGHS: number;
};

const asfColumns: Column<AsfRow>[] = [
  { key: 'item', header: 'Liability category', render: (r) => r.item, width: '50%' },
  { key: 'bal', header: 'Balance', numeric: true, render: (r) => fmtCurrency(r.balanceGHS) },
  { key: 'fct', header: 'ASF factor', numeric: true, render: (r) => `${r.factor}%` },
  { key: 'asf', header: 'ASF amount', numeric: true, render: (r) => fmtCurrency(r.asfGHS) },
];

const rsfColumns: Column<RsfRow>[] = [
  { key: 'item', header: 'Asset category', render: (r) => r.item, width: '50%' },
  { key: 'bal', header: 'Balance', numeric: true, render: (r) => fmtCurrency(r.balanceGHS) },
  { key: 'fct', header: 'RSF factor', numeric: true, render: (r) => `${r.factor}%` },
  { key: 'rsf', header: 'RSF amount', numeric: true, render: (r) => fmtCurrency(r.rsfGHS) },
];

export default function NSFRDashboard() {
  const totalASF = nsfr.asfBreakdown.reduce((s, r) => s + r.asfGHS, 0);
  const totalRSF = nsfr.rsfBreakdown.reduce((s, r) => s + r.rsfGHS, 0);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'NSFR Dashboard' },
        ]}
        title="Net Stable Funding Ratio"
        subtitle="Basel III NSFR per BoG CRD · 1-year stable funding horizon"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          <div className="lg:col-span-2">
            <RatioGauge
              label="Net Stable Funding Ratio"
              value={nsfr.current}
              threshold={nsfr.threshold}
              status="compliant"
            />
          </div>
          <KPICard
            label="Available stable funding"
            value={nsfr.asfGHS / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={1}
            status="compliant"
            sparkline={[1490, 1500, 1505, 1515, 1520, 1525, 1528, 1530]}
          />
          <KPICard
            label="Required stable funding"
            value={nsfr.rsfGHS / 1_000_000}
            prefix="GHS"
            suffix="M"
            decimals={1}
            status="compliant"
            sparkline={[1280, 1284, 1288, 1290, 1292, 1294, 1296, 1297]}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader
              title="Available Stable Funding (ASF)"
              subtitle="Liability-side weighting per Basel III §50"
            />
            <CardBody className="p-0">
              <DataTable
                columns={asfColumns}
                rows={[
                  ...nsfr.asfBreakdown,
                  {
                    item: 'TOTAL ASF',
                    balanceGHS: nsfr.asfBreakdown.reduce((s, r) => s + r.balanceGHS, 0),
                    factor: 0,
                    asfGHS: totalASF,
                  } as AsfRow,
                ]}
                totalsRowMatcher={(r) => r.item.startsWith('TOTAL')}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Required Stable Funding (RSF)"
              subtitle="Asset-side weighting per Basel III §52"
            />
            <CardBody className="p-0">
              <DataTable
                columns={rsfColumns}
                rows={[
                  ...nsfr.rsfBreakdown,
                  {
                    item: 'TOTAL RSF',
                    balanceGHS: nsfr.rsfBreakdown.reduce((s, r) => s + r.balanceGHS, 0),
                    factor: 0,
                    rsfGHS: totalRSF,
                  } as RsfRow,
                ]}
                totalsRowMatcher={(r) => r.item.startsWith('TOTAL')}
              />
            </CardBody>
          </Card>
        </div>

        <p className="text-caption text-slate">
          NSFR = Total ASF{' '}
          <span className="font-mono text-navy">{fmtCurrency(totalASF, 'GHS')}</span>{' '}
          / Total RSF{' '}
          <span className="font-mono text-navy">{fmtCurrency(totalRSF, 'GHS')}</span>{' '}
          = <span className="font-mono font-medium text-success">{nsfr.current.toFixed(1)}%</span>.
          BoG minimum 100%. Sample Bank Limited remains compliant with{' '}
          <span className="font-mono text-navy">+18.0 pts</span> headroom.
        </p>
      </div>
    </>
  );
}
