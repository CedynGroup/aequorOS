import { TrendingUp, TrendingDown } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import { ftpRates, ftpRateHistory, type FtpRateEntry } from '@/lib/data/ftp';
import { bank } from '@/lib/data/bank';

const cols: Column<FtpRateEntry>[] = [
  {
    key: 'product',
    header: 'Product',
    render: (r) => <span className="font-medium text-navy">{r.product}</span>,
  },
  { key: 'tenor', header: 'Tenor', render: (r) => r.tenor },
  {
    key: 'rate',
    header: 'Active rate',
    numeric: true,
    render: (r) => (
      <span className="font-mono font-semibold text-navy">
        {r.rate.toFixed(2)}%
      </span>
    ),
  },
  {
    key: 'prev',
    header: 'Previous',
    numeric: true,
    render: (r) => (
      <span className="font-mono text-slate">{r.prevRate.toFixed(2)}%</span>
    ),
  },
  {
    key: 'change',
    header: 'Change',
    numeric: true,
    render: (r) => {
      const delta = r.rate - r.prevRate;
      return (
        <span
          className={`inline-flex items-center gap-1 ${
            delta > 0 ? 'text-success' : delta < 0 ? 'text-critical' : 'text-slate'
          }`}
        >
          {delta > 0 ? <TrendingUp size={11} /> : delta < 0 ? <TrendingDown size={11} /> : null}
          <span className="font-mono">
            {delta >= 0 ? '+' : ''}
            {(delta * 100).toFixed(0)} bps
          </span>
        </span>
      );
    },
  },
  { key: 'eff', header: 'Effective', render: (r) => r.effectiveFrom },
];

export default function FTPRates() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Funds Transfer Pricing', href: '/ftp' },
          { label: 'FTP Rates' },
        ]}
        title="FTP Rates"
        subtitle="Active FTP rates by product class · Auto-refreshed from BoG auction outcomes"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardHeader
            title="Active rate table"
            subtitle="All updated 01 Apr 2026 effective immediately for new contracts"
            action={
              <button
                type="button"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-action border border-action/30 bg-action-light rounded"
              >
                Propose new rate
              </button>
            }
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={ftpRates} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Rate history — 12 months"
            subtitle="3M and 1Y FTP rates · Tracks BoG auction trajectory"
          />
          <CardBody>
            <RatioHistoryChart
              data={ftpRateHistory.map((p) => ({ month: p.month, value: p['3M'] }))}
              threshold={20}
              yMin={22}
              yMax={28}
              color="#2D7FF9"
              label="3M FTP"
            />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
