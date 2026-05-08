import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import RatioGauge from '@/components/ui/RatioGauge';
import StatusPill from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import DonutChart from '@/components/charts/DonutChart';
import { capital, rwaByCategory, carHistory } from '@/lib/data/basel';
import { bank } from '@/lib/data/bank';
import { fmtCurrency } from '@/lib/format';

export default function BaselDashboard() {
  const buffers = [
    { label: 'BoG Minimum CAR', value: capital.bogMinimum, met: true },
    { label: 'Capital Conservation Buffer', value: capital.conservationBuffer, met: true },
    { label: 'Countercyclical Buffer', value: capital.countercyclicalBuffer, met: true },
    { label: 'D-SIB Buffer', value: capital.dsibBuffer, met: true },
  ];
  const totalRequired = buffers.reduce((s, b) => s + b.value, 0);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital' },
          { label: 'Dashboard' },
        ]}
        title="Basel Capital"
        subtitle="Capital Adequacy Ratio · Tier 1 / Tier 2 · BoG CRD framework"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          <div className="lg:col-span-2">
            <RatioGauge
              label="Capital Adequacy Ratio"
              value={capital.car}
              threshold={capital.bogMinimum}
              internalBuffer={capital.internalBuffer}
              status="compliant"
              decimals={2}
              suffix="%"
            />
          </div>
          <KPICard
            label="Tier 1 ratio"
            value={capital.tier1}
            suffix="%"
            decimals={2}
            footer="CET1 + AT1 / RWA"
            status="compliant"
            sparkline={[10.4, 10.6, 10.9, 11.0, 11.2, 11.4, 11.6, 11.8]}
          />
          <KPICard
            label="Tier 2 ratio"
            value={capital.tier2}
            suffix="%"
            decimals={2}
            footer="Subordinated debt + reserves / RWA"
            status="compliant"
            sparkline={[2.6, 2.5, 2.5, 2.4, 2.4, 2.4, 2.4, 2.4]}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader
              title="CAR — 12-month trend"
              subtitle="Daily run, month-end values · Traffic light = compliant"
              action={<StatusPill tone="success">12 of 12 above buffer</StatusPill>}
            />
            <CardBody>
              <RatioHistoryChart
                data={carHistory}
                threshold={capital.bogMinimum}
                internalBuffer={capital.internalBuffer}
                yMin={9}
                yMax={16}
                color="#0E8A4F"
                label="CAR"
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="RWA composition"
              subtitle={`Total ${fmtCurrency(capital.totalRwaGHS, 'GHS')}`}
            />
            <CardBody className="space-y-4">
              <DonutChart
                data={rwaByCategory.map((r) => ({
                  name: r.category,
                  value: r.share,
                  color: r.color,
                }))}
                centerLabel="Total RWA"
                centerValue={fmtCurrency(capital.totalRwaGHS, 'GHS')}
                format="percent"
              />
              <ul className="space-y-2 text-caption pt-2 border-t border-border-light">
                {rwaByCategory.map((r) => (
                  <li key={r.category} className="flex items-center gap-3">
                    <span
                      className="w-2 h-2 rounded-sm shrink-0"
                      style={{ background: r.color }}
                      aria-hidden
                    />
                    <span className="text-navy/85 flex-1">{r.category}</span>
                    <span className="font-mono text-navy tabular-nums">{fmtCurrency(r.rwaGHS, 'GHS')}</span>
                    <span className="font-mono text-slate w-10 text-right tabular-nums">
                      {r.share}%
                    </span>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader
            title="Regulatory buffer status"
            subtitle="BoG CRD requirements · D-SIB designation: not designated"
          />
          <CardBody className="grid grid-cols-1 md:grid-cols-4 gap-5">
            {buffers.map((b) => (
              <div key={b.label} className="space-y-2">
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  {b.label}
                </p>
                <p className="font-mono text-h1 text-navy tabular-nums">
                  {b.value.toFixed(2)}%
                </p>
                <StatusPill tone={b.met ? 'success' : 'critical'}>
                  {b.met ? 'Met' : 'Below'}
                </StatusPill>
              </div>
            ))}
            <div className="md:col-span-4 border-t border-border-light pt-4 flex items-center justify-between">
              <p className="text-caption text-slate">
                Total required ratio:{' '}
                <span className="font-mono text-navy">{totalRequired.toFixed(2)}%</span>{' '}
                · Current CAR{' '}
                <span className="font-mono text-success font-medium">{capital.car.toFixed(2)}%</span>{' '}
                · Excess{' '}
                <span className="font-mono text-success font-medium">
                  +{(capital.car - totalRequired).toFixed(2)}%
                </span>
              </p>
            </div>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
