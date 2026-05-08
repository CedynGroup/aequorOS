import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { branches, type Branch } from '@/lib/data/ftp';
import { bank } from '@/lib/data/bank';
import { fmtCurrency } from '@/lib/format';

const trendIcon = {
  up: <TrendingUp size={12} className="text-success" />,
  down: <TrendingDown size={12} className="text-critical" />,
  flat: <Minus size={12} className="text-slate" />,
};

const cols: Column<Branch>[] = [
  {
    key: 'rank',
    header: '#',
    numeric: true,
    render: (r) => <span className="font-mono text-slate">{r.rank}</span>,
    width: '5%',
  },
  {
    key: 'name',
    header: 'Branch',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.name}</p>
        <p className="text-caption text-slate">
          {r.id} · {r.region}
        </p>
      </div>
    ),
    width: '24%',
  },
  {
    key: 'dep',
    header: 'Deposits',
    numeric: true,
    render: (r) => fmtCurrency(r.depositsGHS, 'GHS'),
  },
  {
    key: 'lns',
    header: 'Loans',
    numeric: true,
    render: (r) => fmtCurrency(r.loansGHS, 'GHS'),
  },
  {
    key: 'nii',
    header: 'NII (annual)',
    numeric: true,
    render: (r) => fmtCurrency(r.niiGHS, 'GHS'),
  },
  {
    key: 'nim',
    header: 'FTP-adj NIM',
    numeric: true,
    render: (r) => (
      <span
        className={
          r.ftpAdjustedNimPct >= 4.5
            ? 'text-success font-medium'
            : r.ftpAdjustedNimPct >= 4.0
            ? 'text-navy'
            : r.ftpAdjustedNimPct >= 3.5
            ? 'text-warning'
            : 'text-critical'
        }
      >
        {r.ftpAdjustedNimPct.toFixed(2)}%
      </span>
    ),
  },
  {
    key: 'trend',
    header: '',
    align: 'right',
    render: (r) => trendIcon[r.trend],
    width: '5%',
  },
];

export default function BranchPL() {
  const totalDep = branches.reduce((s, b) => s + b.depositsGHS, 0);
  const totalLns = branches.reduce((s, b) => s + b.loansGHS, 0);
  const totalNii = branches.reduce((s, b) => s + b.niiGHS, 0);
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Funds Transfer Pricing', href: '/ftp' },
          { label: 'Branch P&L' },
        ]}
        title="Branch Profitability"
        subtitle="FTP-adjusted NIM by branch · 18 branches across 9 regions"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Total branches</p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">{branches.length}</p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Total deposits</p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">{fmtCurrency(totalDep, 'GHS')}</p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Total loans</p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">{fmtCurrency(totalLns, 'GHS')}</p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Annualized NII</p>
            <p className="mt-1 font-mono text-h1 text-success tabular-nums">{fmtCurrency(totalNii, 'GHS')}</p>
          </div>
        </div>

        <Card>
          <CardHeader title="Branch ranking" subtitle="Sorted by FTP-adjusted NIM" />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={branches} />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
