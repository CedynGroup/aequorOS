import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import StatusPill from '@/components/ui/StatusPill';
import { fxScenarios } from '@/lib/data/fx';
import { bank } from '@/lib/data/bank';
import { fmtCurrencySigned, fmtPctSigned } from '@/lib/format';

export default function FXScenarios() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk', href: '/fx' },
          { label: 'Currency Scenarios' },
        ]}
        title="Currency Scenarios"
        subtitle="Cedi depreciation P&L impact · Calibrated to BoG ICAAP framework"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {fxScenarios.map((s) => {
            const tone =
              s.id === 'severe' ? 'critical' : s.id === 'moderate' ? 'amber' : 'success';
            return (
              <Card
                key={s.id}
                className={s.id === 'severe' ? 'border-l-4 border-l-critical' : ''}
              >
                <CardHeader title={s.name} subtitle={s.description} action={<StatusPill tone={tone} />} />
                <CardBody className="space-y-5">
                  <div className="grid grid-cols-2 gap-5">
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        P&amp;L impact
                      </p>
                      <p className={`mt-1 font-mono text-h1 tabular-nums ${
                        s.pnlImpactGHS >= 0 ? 'text-success' : 'text-critical'
                      }`}>
                        {fmtCurrencySigned(s.pnlImpactGHS)}
                      </p>
                    </div>
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        Capital impact
                      </p>
                      <p className="mt-1 font-mono text-h1 text-slate tabular-nums">
                        {fmtPctSigned(s.capitalImpactPct, 2)}
                      </p>
                    </div>
                  </div>
                  <div className="border-t border-border-light pt-3 text-caption text-slate">
                    GHS/USD: 12.50 →{' '}
                    <span className="font-mono text-navy font-medium">
                      {(12.5 * (1 + s.ghsUsdShock / 100)).toFixed(2)}
                    </span>{' '}
                    (+{s.ghsUsdShock}%)
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>

        <Card>
          <CardHeader title="Methodology" subtitle="Currency shock translation to P&L and capital" />
          <CardBody className="text-body text-navy/85 leading-relaxed space-y-3">
            <p>
              Net open position revalued at stressed FX rates per BoG ICAAP
              guidance. Pass-through to P&amp;L assumes immediate mark-to-market;
              capital impact reflects regulatory translation reserve treatment under
              IFRS 9 and BoG NOP rules.
            </p>
            <p>
              Cross-currency hedge effectiveness retained at current ratio (74%).
              Idiosyncratic stress assumes simultaneous shock across all paired
              currencies; diversification benefit not credited per BoG conservative
              framework.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
