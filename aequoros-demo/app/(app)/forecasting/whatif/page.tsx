import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { whatIfScenarios, type WhatIfScenario } from '@/lib/data/forecasting';
import { bank } from '@/lib/data/bank';
import { fmtCurrency } from '@/lib/format';

const cols: Column<WhatIfScenario>[] = [
  {
    key: 'name',
    header: 'Scenario',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.name}</p>
        <p className="text-caption text-slate">{r.description}</p>
      </div>
    ),
    width: '38%',
  },
  {
    key: 'assets',
    header: 'Assets at +36M',
    numeric: true,
    render: (r) => fmtCurrency(r.assets36M * 1_000_000, 'GHS'),
  },
  {
    key: 'car',
    header: 'CAR at +36M',
    numeric: true,
    render: (r) => (
      <span
        className={
          r.car36M >= 13
            ? 'text-success font-medium'
            : r.car36M >= 10
            ? 'text-warning font-medium'
            : 'text-critical font-medium'
        }
      >
        {r.car36M.toFixed(1)}%
      </span>
    ),
  },
  {
    key: 'nim',
    header: 'NIM',
    numeric: true,
    render: (r) => `${r.nim36M.toFixed(2)}%`,
  },
  {
    key: 'npl',
    header: 'NPL ratio',
    numeric: true,
    render: (r) => (
      <span
        className={
          r.npl36M <= 5
            ? 'text-success'
            : r.npl36M <= 8
            ? 'text-warning'
            : 'text-critical'
        }
      >
        {r.npl36M.toFixed(1)}%
      </span>
    ),
  },
  {
    key: 'sev',
    header: 'Severity',
    align: 'right',
    render: (r) => <StatusPill tone={r.severity as StatusTone} />,
  },
];

export default function WhatIf() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'What-if Analysis' },
        ]}
        title="What-if Analysis"
        subtitle="Pre-built macro scenarios · Full balance sheet impact at 36-month horizon"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardHeader
            title="Scenario library"
            subtitle="Standard macro shocks · Calibrated to BoG ICAAP framework"
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={whatIfScenarios} />
          </CardBody>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {whatIfScenarios.slice(0, 3).map((s) => (
            <Card key={s.id} className={s.severity === 'critical' ? 'border-l-4 border-l-critical' : ''}>
              <CardHeader title={s.name} subtitle={s.description} />
              <CardBody className="grid grid-cols-2 gap-3">
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">Assets</p>
                  <p className="mt-1 font-mono text-h3 text-navy tabular-nums">
                    {fmtCurrency(s.assets36M * 1_000_000, 'GHS')}
                  </p>
                </div>
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">CAR</p>
                  <p className={`mt-1 font-mono text-h3 tabular-nums ${
                    s.car36M >= 10 ? 'text-success' : 'text-critical'
                  }`}>
                    {s.car36M.toFixed(1)}%
                  </p>
                </div>
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">NIM</p>
                  <p className="mt-1 font-mono text-h3 text-navy tabular-nums">
                    {s.nim36M.toFixed(2)}%
                  </p>
                </div>
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">NPL</p>
                  <p className="mt-1 font-mono text-h3 text-navy tabular-nums">
                    {s.npl36M.toFixed(1)}%
                  </p>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      </div>
    </>
  );
}
