import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StatusPill from '@/components/ui/StatusPill';
import { irsPortfolio, type IRSContract } from '@/lib/data/irr';
import { bank } from '@/lib/data/bank';
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

const cols: Column<IRSContract>[] = [
  {
    key: 'id',
    header: 'Contract',
    render: (r) => <span className="font-mono text-caption">{r.id}</span>,
  },
  { key: 'type', header: 'Type', render: () => 'IRS' },
  { key: 'tenor', header: 'Tenor', render: (r) => r.tenor },
  {
    key: 'notional',
    header: 'Notional',
    numeric: true,
    render: (r) => `${r.ccy === 'USD' ? '$' : 'GHS '}${(r.notional / 1_000_000).toFixed(1)}M`,
  },
  { key: 'pay', header: 'Pay leg', render: (r) => <span className="font-mono text-caption">{r.payRate}</span> },
  { key: 'rcv', header: 'Receive leg', render: (r) => <span className="font-mono text-caption">{r.receiveRate}</span> },
  { key: 'eff', header: 'Effective', render: (r) => r.effectiveDate },
  { key: 'mat', header: 'Maturity', render: (r) => r.maturity },
  {
    key: 'mtm',
    header: 'MTM',
    numeric: true,
    render: (r) => (
      <span className={r.mtm >= 0 ? 'text-success' : 'text-critical'}>
        {fmtCurrencySigned(r.mtm, r.ccy === 'USD' ? 'USD' : 'GHS')}
      </span>
    ),
  },
];

export default function PositionViewer() {
  const totalNotionalGHS =
    irsPortfolio.reduce(
      (s, c) => s + (c.ccy === 'USD' ? c.notional * bank.ghsUsd : c.notional),
      0
    );
  const totalMTM = irsPortfolio.reduce(
    (s, c) => s + (c.ccy === 'USD' ? c.mtm * bank.ghsUsd : c.mtm),
    0
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Interest Rate Risk', href: '/irr' },
          { label: 'Position Viewer' },
        ]}
        title="Position Viewer"
        subtitle="IRS portfolio · Hedge accounting under IFRS 9"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="card p-5 grid grid-cols-2 md:grid-cols-4 gap-6">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Active contracts
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {irsPortfolio.length}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Total notional (GHS-eq)
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {fmtCurrency(totalNotionalGHS)}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Net MTM
            </p>
            <p className={`mt-1 font-mono text-h1 tabular-nums ${totalMTM >= 0 ? 'text-success' : 'text-critical'}`}>
              {fmtCurrencySigned(totalMTM)}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Hedge effectiveness
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">94.6%</p>
          </div>
        </div>

        <Card>
          <CardHeader
            title="IRS portfolio"
            subtitle="Open derivative positions · Synthetic CCP and bilateral mix"
            action={<StatusPill tone="compliant">All within bucket limits</StatusPill>}
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={irsPortfolio} />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
