import { AlertTriangle, ChevronRight } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { stressScenarios, lcr, nsfr } from '@/lib/data/liquidity';
import { bank } from '@/lib/data/bank';

export default function StressScenarios() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Stress Scenarios' },
        ]}
        title="Stress Scenarios"
        subtitle="Basel III-aligned liquidity stress per BoG ILAAP framework"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        {/* Baseline reference */}
        <div className="card px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-6">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Baseline LCR
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {lcr.current.toFixed(1)}%
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Baseline NSFR
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {nsfr.current.toFixed(1)}%
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Internal LCR buffer
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {lcr.internalBuffer}%
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Survival horizon
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">30 d</p>
          </div>
        </div>

        {/* Scenario cards */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {stressScenarios.map((s) => {
            const breach = s.severity === 'critical';
            return (
              <Card key={s.id} className={breach ? 'border-l-4 border-l-critical' : ''}>
                <CardHeader
                  title={s.name}
                  subtitle={s.description}
                  action={<StatusPill tone={s.severity as StatusTone} />}
                />
                <CardBody className="space-y-5">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        LCR after stress
                      </p>
                      <p className="mt-1 font-mono text-h1 tabular-nums">
                        <span
                          className={
                            s.lcrAfter >= 100 ? 'text-success' : 'text-critical'
                          }
                        >
                          {s.lcrAfter.toFixed(1)}%
                        </span>
                      </p>
                      <p className="mt-1 font-mono text-caption text-critical tabular-nums">
                        {s.lcrChange.toFixed(1)} pts
                      </p>
                    </div>
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        NSFR after stress
                      </p>
                      <p className="mt-1 font-mono text-h1 tabular-nums">
                        <span
                          className={
                            s.nsfrAfter >= 100 ? 'text-success' : 'text-critical'
                          }
                        >
                          {s.nsfrAfter.toFixed(1)}%
                        </span>
                      </p>
                      <p className="mt-1 font-mono text-caption text-critical tabular-nums">
                        {s.nsfrChange.toFixed(1)} pts
                      </p>
                    </div>
                  </div>

                  {s.breachDay && (
                    <div className="flex items-start gap-2 px-3 py-2.5 rounded bg-critical-light border border-critical/20">
                      <AlertTriangle
                        size={14}
                        className="text-critical shrink-0 mt-0.5"
                        aria-hidden
                      />
                      <p className="text-caption text-critical leading-relaxed">
                        First LCR breach projected at <span className="font-mono font-semibold">Day +{s.breachDay}</span>.
                        Activate ILAAP contingency plan.
                      </p>
                    </div>
                  )}

                  <div>
                    <p className="text-micro font-medium uppercase tracking-wider text-slate">
                      Treasury notes
                    </p>
                    <p className="mt-2 text-body text-navy/85 leading-relaxed">
                      {s.notes}
                    </p>
                  </div>

                  <button
                    type="button"
                    className="w-full inline-flex items-center justify-center gap-1 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded hover:bg-action/10"
                  >
                    Open detailed scenario
                    <ChevronRight size={13} aria-hidden />
                  </button>
                </CardBody>
              </Card>
            );
          })}
        </div>

        {/* Methodology footer */}
        <Card>
          <CardHeader title="Methodology" subtitle="ILAAP and Basel III stress alignment" />
          <CardBody className="text-body text-navy/85 leading-relaxed space-y-3">
            <p>
              Stress factors applied to baseline LCR and NSFR per BoG&apos;s ILAAP
              framework and Basel III §35-36 (LCR) and §50-52 (NSFR). HQLA
              haircuts revalued under each scenario; behavioral runoff
              assumptions adjusted by scenario severity multiplier.
            </p>
            <p>
              Idiosyncratic and market-wide are calibrated to BoG severe
              tolerance levels documented in the 2025 Industry Stress Test
              Review. Combined scenario assumes simultaneous shock with no
              central bank backstop. Recalculated daily; ILAAP submission
              quarterly.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
