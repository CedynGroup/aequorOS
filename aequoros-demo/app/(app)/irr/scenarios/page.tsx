import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import ScenarioImpactChart from '@/components/charts/ScenarioImpactChart';
import { rateScenarios, type RateScenario } from '@/lib/data/irr';
import { bank } from '@/lib/data/bank';
import { fmtCurrencySigned, fmtPctSigned } from '@/lib/format';

const cols: Column<RateScenario>[] = [
  { key: 'name', header: 'Scenario', render: (r) => <span className="font-medium text-navy">{r.name}</span> },
  { key: 'desc', header: 'Description', render: (r) => <span className="text-slate">{r.description}</span> },
  {
    key: 'nii',
    header: 'NII impact',
    numeric: true,
    render: (r) => (
      <span className={r.niiImpactGHS >= 0 ? 'text-success' : 'text-critical'}>
        {fmtCurrencySigned(r.niiImpactGHS)}
      </span>
    ),
  },
  {
    key: 'niiPct',
    header: 'NII %',
    numeric: true,
    render: (r) => fmtPctSigned(r.niiImpactPct),
  },
  {
    key: 'eve',
    header: 'EVE impact',
    numeric: true,
    render: (r) => (
      <span className={r.eveImpactGHS >= 0 ? 'text-success' : 'text-critical'}>
        {fmtCurrencySigned(r.eveImpactGHS)}
      </span>
    ),
  },
  {
    key: 'evePct',
    header: 'EVE % Tier 1',
    numeric: true,
    render: (r) => fmtPctSigned(r.eveImpactPct),
  },
  {
    key: 'policy',
    header: 'Policy',
    align: 'right',
    render: (r) => (
      <StatusPill tone={r.withinPolicy ? 'compliant' : 'breach'}>
        {r.withinPolicy ? 'Within' : 'Breach'}
      </StatusPill>
    ),
  },
];

export default function IRRScenarios() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Interest Rate Risk', href: '/irr' },
          { label: 'Rate Scenarios' },
        ]}
        title="Rate Scenarios"
        subtitle="Standard BoG / Basel IRRBB shocks plus custom scenario builder"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader title="NII impact (12-month)" subtitle="Annualized change in net interest income" />
            <CardBody>
              <ScenarioImpactChart
                metric="NII"
                data={rateScenarios.map((s) => ({
                  name: s.name,
                  impact: s.niiImpactGHS / 1_000_000,
                }))}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="EVE impact" subtitle="Change in economic value of equity" />
            <CardBody>
              <ScenarioImpactChart
                metric="EVE"
                data={rateScenarios.map((s) => ({
                  name: s.name,
                  impact: s.eveImpactGHS / 1_000_000,
                }))}
              />
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader
            title="Scenario detail"
            subtitle="Per BoG IRRBB Pillar 2 framework · 6 standardized scenarios"
            action={
              <button
                type="button"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-action border border-action/30 bg-action-light rounded"
              >
                + Build custom scenario
              </button>
            }
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={rateScenarios} />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
