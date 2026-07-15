'use client';

import { useQuery } from '@tanstack/react-query';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { useBankContext } from '@/components/shell/BankContext';
import { apiBaseUrl } from '@/lib/api/client';
import { useBank, useCashflowHistory } from '@/lib/api/hooks';
import { labelize } from '@/lib/api/values';

const JURISDICTIONS: Record<string, string> = {
  GH: 'Ghana',
};

/** Ping the risk-service liveness endpoint directly (outside the generated client). */
function useRiskServiceHealth() {
  return useQuery({
    queryKey: ['health', 'risk-service'],
    queryFn: async () => {
      const healthUrl = `${apiBaseUrl.replace(/\/api\/v1\/?$/, '')}/api/health/live`;
      const response = await fetch(healthUrl, {
        signal: AbortSignal.timeout(4000),
      });
      if (!response.ok) throw new Error(`Health check failed (${response.status})`);
      return (await response.json()) as { status?: string };
    },
    retry: false,
    refetchInterval: 60_000,
  });
}

export default function SettingsPage() {
  const { bank, periods } = useBankContext();
  const bankQuery = useBank(bank?.id);
  const profile = bankQuery.data ?? bank;

  const health = useRiskServiceHealth();
  // Tiny query against the cashflow proxy — 503 means the ML sidecar is offline.
  const sidecarProbe = useCashflowHistory(bank?.id, 30);

  const riskServiceTone: StatusTone = health.isLoading
    ? 'slate'
    : health.data?.status === 'ok'
    ? 'success'
    : 'critical';
  const riskServiceStatus = health.isLoading
    ? 'Checking…'
    : health.data?.status === 'ok'
    ? 'OK'
    : 'Down';

  const sidecarTone: StatusTone = sidecarProbe.isLoading
    ? 'slate'
    : sidecarProbe.data
    ? 'success'
    : 'amber';
  const sidecarStatus = sidecarProbe.isLoading
    ? 'Checking…'
    : sidecarProbe.data
    ? 'OK'
    : 'Offline';

  return (
    <>
      <PageHeader title="Settings" subtitle="Bank profile, integrations, governance" />

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader title="Bank profile" subtitle="Reporting entity from the risk service" />
          <CardBody>
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-body">
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Legal name
                </dt>
                <dd className="mt-1 text-navy">{profile?.name ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Jurisdiction
                </dt>
                <dd className="mt-1 text-navy">
                  {profile
                    ? JURISDICTIONS[profile.jurisdictionCode] ??
                      profile.jurisdictionCode
                    : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Regulator
                </dt>
                <dd className="mt-1 text-navy">Bank of Ghana</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  License class
                </dt>
                <dd className="mt-1 text-navy">
                  {profile ? labelize(profile.licenseType) : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Reporting currency
                </dt>
                <dd className="mt-1 font-mono text-navy">
                  {profile?.currency ?? '—'}
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Reporting periods
                </dt>
                <dd className="mt-1 text-navy">
                  {periods.length} loaded
                  {periods.length > 0 &&
                    ` · latest ${periods[0]?.label ?? ''}`}
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Platform services"
            subtitle="Live service connections behind this demo"
          />
          <CardBody className="space-y-3">
            <div className="flex items-center justify-between gap-3 py-2 border-b border-border-light">
              <div className="min-w-0">
                <p className="text-body text-navy">Risk service API</p>
                <p className="text-caption text-slate font-mono truncate">
                  {apiBaseUrl}
                </p>
              </div>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-caption text-slate">
                  Regulatory engines · canonical data
                </span>
                <StatusPill tone={riskServiceTone}>{riskServiceStatus}</StatusPill>
              </span>
            </div>
            <div className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <p className="text-body text-navy">Cash-flow ML sidecar</p>
                <p className="text-caption text-slate">
                  LSTM daily cash-flow forecasts, proxied via the risk service
                </p>
              </div>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-caption text-slate">
                  Optional — LCR forecasting page degrades gracefully
                </span>
                <StatusPill tone={sidecarTone}>{sidecarStatus}</StatusPill>
              </span>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Governance" subtitle="Platform design principles" />
          <CardBody>
            <ul className="space-y-3 text-body text-navy/85">
              {[
                'Every calculation persists an immutable run with engine version and input hash',
                'Regulatory math executes server-side only — the UI renders API payloads',
                'Tenant isolation on every request via organization-scoped headers',
                'Deterministic engines: identical inputs reproduce identical outputs',
                'Full audit trail and data lineage across all calculations',
                'Synthetic demo dataset — no production bank data in this environment',
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
          <CardHeader
            title="Demo personas"
            subtitle="Illustrative roles for the demo walkthrough — not real accounts"
          />
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
