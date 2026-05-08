import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StatusPill from '@/components/ui/StatusPill';
import { productLines, type ProductLine } from '@/lib/data/ftp';
import { bank } from '@/lib/data/bank';
import { fmtCurrency, fmtPctSigned } from '@/lib/format';

const cols: Column<ProductLine>[] = [
  {
    key: 'product',
    header: 'Product',
    render: (r) => <span className="font-medium text-navy">{r.product}</span>,
    width: '28%',
  },
  {
    key: 'cat',
    header: 'Type',
    render: (r) => (
      <StatusPill tone={r.category === 'Asset' ? 'action' : 'success'}>
        {r.category}
      </StatusPill>
    ),
  },
  { key: 'bal', header: 'Balance', numeric: true, render: (r) => fmtCurrency(r.balanceGHS, 'GHS') },
  {
    key: 'yield',
    header: 'Yield / Cost',
    numeric: true,
    render: (r) => `${r.yieldPct.toFixed(2)}%`,
  },
  {
    key: 'ftp',
    header: 'FTP rate',
    numeric: true,
    render: (r) => `${r.ftpRatePct.toFixed(2)}%`,
  },
  {
    key: 'spread',
    header: 'Spread',
    numeric: true,
    render: (r) => (
      <span
        className={
          r.spreadPct >= 0
            ? 'text-success font-medium'
            : 'text-critical font-medium'
        }
      >
        {fmtPctSigned(r.spreadPct, 1)}
      </span>
    ),
  },
];

export default function ProductPL() {
  const assetTotal = productLines
    .filter((p) => p.category === 'Asset')
    .reduce((s, p) => s + p.balanceGHS, 0);
  const liabilityTotal = productLines
    .filter((p) => p.category === 'Liability')
    .reduce((s, p) => s + p.balanceGHS, 0);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Funds Transfer Pricing', href: '/ftp' },
          { label: 'Product P&L' },
        ]}
        title="Product Profitability"
        subtitle="FTP-adjusted spread by product line · Match-funded basis"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Asset book</p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {fmtCurrency(assetTotal, 'GHS')}
            </p>
            <p className="mt-1 text-caption text-slate">6 product categories</p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Liability book</p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {fmtCurrency(liabilityTotal, 'GHS')}
            </p>
            <p className="mt-1 text-caption text-slate">5 product categories</p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Net spread (NIM proxy)</p>
            <p className="mt-1 font-mono text-h1 text-success tabular-nums">4.82%</p>
            <p className="mt-1 text-caption text-slate">Weighted by funding mix</p>
          </div>
        </div>

        <Card>
          <CardHeader
            title="Asset products"
            subtitle="FTP rate represents internal funding cost; spread is gross of credit losses"
          />
          <CardBody className="p-0">
            <DataTable
              columns={cols}
              rows={productLines.filter((p) => p.category === 'Asset')}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Liability products"
            subtitle="FTP rate represents earned credit on funding provided to asset book"
          />
          <CardBody className="p-0">
            <DataTable
              columns={cols}
              rows={productLines.filter((p) => p.category === 'Liability')}
            />
          </CardBody>
        </Card>

        <p className="text-caption text-slate">
          Negative spread on GoG securities reflects current curve inversion at the
          short end vs FTP curve. Behavioral modeling for non-maturity deposits
          (NMD) calibrated using 60 months historical balance/repricing data;
          recalibrated quarterly.
        </p>
      </div>
    </>
  );
}
