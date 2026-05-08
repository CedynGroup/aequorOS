import { AlertTriangle } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import CapitalProjectionChart from '@/components/charts/CapitalProjectionChart';
import { capitalStressScenarios, capital } from '@/lib/data/basel';
import { bank } from '@/lib/data/bank';

export default function CapitalStress() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Stress Testing' },
        ]}
        title="Capital Stress Testing"
        subtitle="ICAAP-style stress · 12-month CAR projection · BoG severe scenario"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {capitalStressScenarios.map((s) => {
            const breach = s.endStateCar < capital.bogMinimum;
            return (
              <Card
                key={s.id}
                className={breach ? 'border-l-4 border-l-critical' : ''}
              >
                <CardHeader
                  title={s.name}
                  subtitle={s.description}
                  action={<StatusPill tone={s.severity as StatusTone} />}
                />
                <CardBody className="space-y-5">
                  <div className="grid grid-cols-2 gap-5">
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        End-state CAR
                      </p>
                      <p
                        className={`mt-1 font-mono text-h1 tabular-nums ${
                          s.endStateCar >= capital.bogMinimum ? 'text-success' : 'text-critical'
                        }`}
                      >
                        {s.endStateCar.toFixed(1)}%
                      </p>
                      <p className="text-caption text-slate">
                        from {capital.car.toFixed(1)}%
                      </p>
                    </div>
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        End-state Tier 1
                      </p>
                      <p
                        className={`mt-1 font-mono text-h1 tabular-nums ${
                          s.endStateTier1 >= 8 ? 'text-success' : 'text-critical'
                        }`}
                      >
                        {s.endStateTier1.toFixed(1)}%
                      </p>
                      <p className="text-caption text-slate">RWA growth +{s.rwaGrowthPct}%</p>
                    </div>
                  </div>

                  {breach && (
                    <div className="flex items-start gap-2 px-3 py-2.5 rounded bg-critical-light border border-critical/20">
                      <AlertTriangle
                        size={14}
                        className="text-critical shrink-0 mt-0.5"
                        aria-hidden
                      />
                      <p className="text-caption text-critical leading-relaxed">
                        Projected CAR falls below BoG 10% minimum. Capital action plan triggered.
                      </p>
                    </div>
                  )}

                  <CapitalProjectionChart
                    data={s.monthsAhead}
                    bogMin={capital.bogMinimum}
                    internalBuffer={capital.internalBuffer}
                  />
                </CardBody>
              </Card>
            );
          })}
        </div>

        <Card>
          <CardHeader title="Capital action plan" subtitle="Triggered actions under severe scenario" />
          <CardBody className="text-body text-navy/85 leading-relaxed space-y-3">
            <p>
              Under BoG ICAAP severe scenario, the following actions are pre-defined
              in the Recovery & Resolution Plan:
            </p>
            <ul className="space-y-2 ml-4">
              <li className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-action shrink-0 mt-2" />
                CAR &lt; 12%: Suspend variable compensation, halt non-essential capex
              </li>
              <li className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-warning shrink-0 mt-2" />
                CAR &lt; 11%: Halt dividend distributions, reduce RWA via portfolio sale
              </li>
              <li className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-critical shrink-0 mt-2" />
                CAR &lt; 10%: Activate Tier 2 issuance plan (GHS 50M sub-debt, pre-arranged with anchor investor)
              </li>
              <li className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-critical shrink-0 mt-2" />
                CAR &lt; 9%: Notify BoG, initiate emergency rights issue (GHS 80M underwritten facility)
              </li>
            </ul>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
