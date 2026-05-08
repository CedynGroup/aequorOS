import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import DonutChart from '@/components/charts/DonutChart';
import { rwaBreakdown, capital } from '@/lib/data/basel';
import { bank } from '@/lib/data/bank';
import { fmtCurrency } from '@/lib/format';

type Row = {
  category: string;
  subcategory: string;
  share: number;
  rwaGHS: number;
};

const cols: Column<Row>[] = [
  { key: 'cat', header: 'Category', render: (r) => r.category, width: '22%' },
  { key: 'sub', header: 'Sub-category', render: (r) => r.subcategory, width: '32%' },
  {
    key: 'rwa',
    header: 'RWA (GHS)',
    numeric: true,
    render: (r) => fmtCurrency(r.rwaGHS, 'GHS'),
  },
  {
    key: 'share',
    header: '% total',
    numeric: true,
    render: (r) => `${r.share.toFixed(1)}%`,
  },
  {
    key: 'cap',
    header: 'Capital req.',
    numeric: true,
    render: (r) => fmtCurrency(r.rwaGHS * 0.13, 'GHS'),
  },
];

export default function RWABreakdown() {
  const totalRwa = rwaBreakdown.reduce((s, r) => s + r.rwaGHS, 0);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'RWA Breakdown' },
        ]}
        title="RWA Breakdown"
        subtitle="Risk-weighted assets by category and portfolio · Standardized approach"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card>
            <CardHeader title="By portfolio" subtitle="Sub-category contribution" />
            <CardBody>
              <DonutChart
                data={rwaBreakdown.map((r) => ({
                  name: r.subcategory,
                  value: r.share,
                  color: r.color,
                }))}
                centerLabel="Total RWA"
                centerValue={fmtCurrency(totalRwa, 'GHS')}
                format="percent"
                height={280}
              />
            </CardBody>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader
              title="Detail by sub-category"
              subtitle="Capital requirement at 13.0% target ratio"
            />
            <CardBody className="p-0">
              <DataTable
                columns={cols}
                rows={[
                  ...rwaBreakdown,
                  {
                    category: 'TOTAL',
                    subcategory: '',
                    share: 100,
                    rwaGHS: totalRwa,
                  } as Row,
                ]}
                totalsRowMatcher={(r) => r.category === 'TOTAL'}
              />
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="Methodology" subtitle="Standardized vs internal models" />
          <CardBody className="text-body text-navy/85 leading-relaxed space-y-3">
            <p>
              Credit risk uses BoG CRD standardized approach with Bank of Ghana
              risk weights for sovereign, bank, and corporate exposures. Retail
              mortgage portfolio uses 35% risk weight (LTV ≤ 80%). SME exposures
              receive standardized risk weight subject to external rating where
              available, otherwise 100%.
            </p>
            <p>
              Operational risk calculated under standardized approach (12% of
              gross income, three-year average). Market risk uses standardized
              measurement method per BoG Pillar 1. Internal Models Approach
              transition planned for FY2027 subject to BoG approval.
            </p>
            <p>
              Total RWA{' '}
              <span className="font-mono font-medium text-navy">{fmtCurrency(totalRwa, 'GHS')}</span> ·
              Total capital{' '}
              <span className="font-mono font-medium text-navy">{fmtCurrency(capital.totalCapitalGHS, 'GHS')}</span> ·
              CAR <span className="font-mono font-medium text-success">{capital.car.toFixed(2)}%</span>.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
