import { Wrench } from 'lucide-react';
import PageHeader from './PageHeader';
import { Card, CardBody } from './Card';
import { bank } from '@/lib/data/bank';

export default function ModulePlaceholder({
  moduleNumber,
  title,
  subtitle,
  screens,
  capabilities,
}: {
  moduleNumber: string;
  title: string;
  subtitle: string;
  screens: string[];
  capabilities: string[];
}) {
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Modules', href: '/' }, { label: title }]}
        title={title}
        subtitle={subtitle}
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="card border-l-4 border-l-action bg-action-light/40 p-5 flex items-start gap-3">
          <Wrench size={18} className="text-action shrink-0 mt-0.5" aria-hidden />
          <div>
            <p className="text-body font-medium text-navy">
              Module {moduleNumber} · In active development
            </p>
            <p className="mt-1 text-body text-navy/80 leading-relaxed max-w-3xl">
              The full {title} module is being built out as part of the
              prototype roadmap. The Liquidity Risk module is fully interactive
              today; this module is wired into navigation and will receive its
              dashboards, scenarios, AI components, and reports in the next
              build phase.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <div className="px-5 py-4 border-b border-border-light">
              <h3 className="text-h3 text-navy">Screens to be built</h3>
              <p className="text-caption text-slate mt-0.5">
                Per AequorOS Figma Design Brief
              </p>
            </div>
            <CardBody>
              <ul className="space-y-2.5 text-body text-navy/85">
                {screens.map((s) => (
                  <li key={s} className="flex items-start gap-3">
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-action shrink-0 mt-2"
                      aria-hidden
                    />
                    <span>{s}</span>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>

          <Card>
            <div className="px-5 py-4 border-b border-border-light">
              <h3 className="text-h3 text-navy">Capabilities</h3>
              <p className="text-caption text-slate mt-0.5">
                What this module will deliver
              </p>
            </div>
            <CardBody>
              <ul className="space-y-2.5 text-body text-navy/85">
                {capabilities.map((c) => (
                  <li key={c} className="flex items-start gap-3">
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-success shrink-0 mt-2"
                      aria-hidden
                    />
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
