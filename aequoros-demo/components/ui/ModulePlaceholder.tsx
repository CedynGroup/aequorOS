import { CalendarClock } from 'lucide-react';
import PageHeader from './PageHeader';
import { Card, CardBody } from './Card';

/**
 * Landing page for modules that are specified in the product architecture
 * but scheduled after the MVP. Honest framing only — no fake data.
 */
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
      />

      <div className="px-8 py-6 space-y-6">
        <div className="card border-l-4 border-l-action bg-action-light/40 p-5 flex items-start gap-3">
          <CalendarClock size={18} className="text-action shrink-0 mt-0.5" aria-hidden />
          <div>
            <p className="text-body font-medium text-navy">
              Module {moduleNumber} · Post-MVP module
            </p>
            <p className="mt-1 text-body text-navy/80 leading-relaxed max-w-3xl">
              This module is specified in the full product architecture and
              scheduled post-seed. The MVP implements Liquidity Risk, Basel
              Capital, and Balance Sheet Forecasting end-to-end.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <div className="px-5 py-4 border-b border-border-light">
              <h3 className="text-h3 text-navy">Planned screens</h3>
              <p className="text-caption text-slate mt-0.5">
                Per the AequorOS product architecture
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
