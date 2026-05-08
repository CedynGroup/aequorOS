import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import BalanceSheetProjectionChart from '@/components/charts/BalanceSheetProjectionChart';
import { projection, strategicAssumptions } from '@/lib/data/forecasting';
import { bank } from '@/lib/data/bank';
import { fmtCurrency, fmtPctSigned } from '@/lib/format';

export default function ForecastDashboard() {
  const m12 = projection[12];
  const m24 = projection[24];
  const m36 = projection[36];
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting' },
          { label: 'Dashboard' },
        ]}
        title="Balance Sheet Forecast"
        subtitle="12 / 24 / 36-month projection · Strategic plan vs current trajectory"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <KPICard
            label="Today (Mar 26)"
            value={projection[0].assets}
            prefix="GHS"
            suffix="M"
            decimals={0}
            footer="Total assets"
          />
          <KPICard
            label="12-month projected"
            value={m12.assets}
            prefix="GHS"
            suffix="M"
            decimals={0}
            footer={`Y1 growth +${(((m12.assets - projection[0].assets) / projection[0].assets) * 100).toFixed(1)}%`}
            status="compliant"
          />
          <KPICard
            label="24-month projected"
            value={m24.assets}
            prefix="GHS"
            suffix="M"
            decimals={0}
            footer={`Y2 growth +${(((m24.assets - m12.assets) / m12.assets) * 100).toFixed(1)}%`}
            status="compliant"
          />
          <KPICard
            label="36-month projected"
            value={m36.assets}
            prefix="GHS"
            suffix="M"
            decimals={0}
            footer={`Y3 growth +${(((m36.assets - m24.assets) / m24.assets) * 100).toFixed(1)}%`}
            status="compliant"
          />
        </div>

        <Card>
          <CardHeader
            title="Asset projection — 36 months"
            subtitle="Composition of asset book over forecast horizon"
          />
          <CardBody>
            <BalanceSheetProjectionChart data={projection} horizonMonths={36} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Strategic assumptions"
            subtitle="Model inputs vs Board-approved strategic plan"
          />
          <CardBody className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {strategicAssumptions.map((a) => (
              <div key={a.label} className="border-l-4 border-l-action pl-4">
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  {a.label}
                </p>
                <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
                  {a.value}
                  {a.suffix}
                </p>
                <p className="mt-0.5 text-caption text-slate">
                  Plan{' '}
                  <span className="font-mono text-navy">
                    {a.planValue}
                    {a.suffix}
                  </span>{' '}
                  · Variance{' '}
                  <span
                    className={`font-mono ${
                      a.variance >= 0 ? 'text-success' : 'text-warning'
                    }`}
                  >
                    {fmtPctSigned(a.variance, 1)}
                  </span>
                </p>
              </div>
            ))}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Capital adequacy projection" subtitle="CAR over forecast horizon" />
          <CardBody>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              <div>
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  Current CAR
                </p>
                <p className="mt-1 font-mono text-h1 text-navy tabular-nums">14.20%</p>
              </div>
              <div>
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  Projected CAR (36M, baseline)
                </p>
                <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
                  {m36.car.toFixed(2)}%
                </p>
              </div>
              <div>
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  Capital injection needed by 36M
                </p>
                <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
                  {fmtCurrency(45_000_000, 'GHS')}
                </p>
              </div>
            </div>
            <p className="mt-4 text-caption text-slate">
              Projection driven by RWA growth (+24% over 36M) outpacing organic
              capital generation. Action plan in Capital Plan FY2026 includes a
              GHS 80M Tier 2 issuance window in H2-2027.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
