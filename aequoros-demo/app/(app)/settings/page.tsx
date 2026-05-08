import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import StatusPill from '@/components/ui/StatusPill';
import { bank } from '@/lib/data/bank';

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" subtitle="Bank profile, integrations, governance" />

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader title="Bank profile" subtitle="Reporting entity configuration" />
          <CardBody>
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-body">
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Legal name
                </dt>
                <dd className="mt-1 text-navy">{bank.name}</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Jurisdiction
                </dt>
                <dd className="mt-1 text-navy">{bank.jurisdiction}</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Regulator
                </dt>
                <dd className="mt-1 text-navy">{bank.regulator}</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  License class
                </dt>
                <dd className="mt-1 text-navy">{bank.licenseClass}</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Reporting currency
                </dt>
                <dd className="mt-1 font-mono text-navy">GHS</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Founded
                </dt>
                <dd className="mt-1 text-navy">{bank.founded}</dd>
              </div>
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Core banking integrations" subtitle="Source systems and connectors" />
          <CardBody className="space-y-3">
            {[
              { name: 'Temenos T24', status: 'Connected · API', tone: 'success' as const },
              { name: 'BoG submission portal', status: 'Connected · v2.4', tone: 'success' as const },
              { name: 'Treasury Markets feed (BoG, Bloomberg)', status: 'Connected', tone: 'success' as const },
              { name: 'Snowflake analytical warehouse', status: 'Active', tone: 'success' as const },
              { name: 'Identity provider (Okta)', status: 'Connected · SAML', tone: 'success' as const },
            ].map((i) => (
              <div
                key={i.name}
                className="flex items-center justify-between gap-3 py-2 border-b border-border-light last:border-b-0"
              >
                <span className="text-body text-navy">{i.name}</span>
                <span className="flex items-center gap-2">
                  <span className="text-caption text-slate">{i.status}</span>
                  <StatusPill tone={i.tone}>OK</StatusPill>
                </span>
              </div>
            ))}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Governance" subtitle="Model risk, audit, security" />
          <CardBody>
            <ul className="space-y-3 text-body text-navy/85">
              {[
                'Model Risk Management aligned with SR 11-7',
                'End-to-end encryption (AES-256 at rest, TLS 1.3 in transit)',
                'Role-based access control with quarterly access review',
                'Full audit trail and data lineage across all calculations',
                'SOC 2 Type II roadmap in progress',
                'Data residency options per jurisdiction (GHS data resident in West Africa region)',
              ].map((g) => (
                <li key={g} className="flex items-start gap-3">
                  <span className="w-1.5 h-1.5 rounded-full bg-success shrink-0 mt-2" aria-hidden />
                  {g}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Users & roles" subtitle="Active platform users" />
          <CardBody className="p-0">
            <ul className="divide-y divide-border-light">
              {[
                { name: 'Akua Mensah', role: 'Head of Treasury & ALM', access: 'Treasury · Approver' },
                { name: 'Yaa Adjei', role: 'Chief Risk Officer', access: 'All modules · CRO sign-off' },
                { name: 'Kojo Aboagye', role: 'FX Trader', access: 'FX · Read/Write' },
                { name: 'Ama Owusu', role: 'Compliance Lead', access: 'Submissions · Approver' },
                { name: 'Eric Inkoom Danso', role: 'CEO · Sample Bank', access: 'Read-only · Board view' },
              ].map((u) => (
                <li key={u.name} className="px-5 py-3 flex items-center gap-4">
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-teal text-white text-caption font-semibold shrink-0">
                    {u.name.split(' ').map((n) => n[0]).slice(0, 2).join('')}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-body text-navy font-medium truncate">{u.name}</p>
                    <p className="text-caption text-slate truncate">{u.role}</p>
                  </div>
                  <span className="text-caption text-slate shrink-0">{u.access}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
