'use client';

/**
 * Settings — token'd governance cards:
 *   · Institution profile — real bank fields from the risk service
 *   · Appearance — real theme toggle (ThemeProvider)
 *   · Users & roles — static demo roster, clearly labeled
 *   · Data & compute — real service health, market-data connections, and the
 *     official-run schedule note (read-only)
 *   · About — engine versions and provenance from persisted regulatory runs
 */

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { Moon, Sun } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import AuthenticationPanel from '@/components/settings/AuthenticationPanel';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import CopyButton from '@/components/ui/CopyButton';
import RunBadge from '@/components/ui/RunBadge';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { useTheme, type Theme } from '@/components/shell/ThemeProvider';
import { MODULE_LABELS, useLatestRunsByModule } from '@/components/reports/hooks';
import { apiBaseUrl, apiOrigin } from '@/lib/api/client';
import {
  useBank,
  useCashflowHistory,
  useMarketDataConnections,
} from '@/lib/api/hooks';
import { fmtRelative, labelize } from '@/lib/api/values';
import { initialsFrom, roleLabel } from '@/lib/api/identity';

/** A copyable UUID row for the identity grid. */
function IdField({ label, value }: { label: string; value: string | undefined }) {
  return (
    <div className="sm:col-span-2">
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className="mt-1 flex items-center gap-2">
        <code className="font-mono text-caption text-navy break-all">{value ?? '—'}</code>
        {value && <CopyButton text={value} label={label} className="shrink-0" />}
      </dd>
    </div>
  );
}

/** Ping the risk-service liveness endpoint directly (outside the generated client). */
function useRiskServiceHealth() {
  return useQuery({
    queryKey: ['health', 'risk-service'],
    queryFn: async () => {
      const healthUrl = `${apiOrigin}/api/health/live`;
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

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="Institution profile, appearance, roles, data & compute"
      />

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <InstitutionProfile
          profile={profile}
          periodCount={periods.length}
          latestPeriodLabel={periods[0]?.label}
        />
        <div className="space-y-6">
          <AppearancePanel />
          <UsersRolesPanel />
        </div>
        <AuthenticationPanel />
        <DataComputePanel bankId={bank?.id} />
        <AboutPanel bankId={bank?.id} />
      </div>
    </>
  );
}

function InstitutionProfile({
  profile,
  periodCount,
  latestPeriodLabel,
}: {
  profile: ReturnType<typeof useBank>['data'] | null;
  periodCount: number;
  latestPeriodLabel: string | undefined;
}) {
  return (
    <Card>
      <CardHeader
        title="Institution profile"
        subtitle="Reporting entity from the risk service"
      />
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
                ? profile.jurisdiction?.countryName ?? profile.jurisdictionCode
                : '—'}
            </dd>
          </div>
          <div>
            <dt className="text-micro font-medium uppercase tracking-wider text-slate">
              Regulator
            </dt>
            <dd className="mt-1 text-navy">
              {profile?.jurisdiction?.centralBankName ?? '—'}
            </dd>
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
              {periodCount} loaded
              {latestPeriodLabel && ` · latest ${latestPeriodLabel}`}
            </dd>
          </div>
          <IdField label="Organization ID" value={profile?.organizationId} />
          <IdField label="Bank ID" value={profile?.id} />
        </dl>
      </CardBody>
    </Card>
  );
}

function AppearancePanel() {
  const { theme, setTheme } = useTheme();
  const options: { value: Theme; label: string; Icon: typeof Sun }[] = [
    { value: 'dark', label: 'Dark', Icon: Moon },
    { value: 'light', label: 'Light', Icon: Sun },
  ];
  return (
    <Card>
      <CardHeader
        title="Appearance"
        subtitle="Theme preference — stored locally in this browser"
      />
      <CardBody>
        <div
          role="radiogroup"
          aria-label="Theme"
          className="inline-flex items-center gap-1 p-1 rounded-md bg-surface border border-border-light"
        >
          {options.map(({ value, label, Icon }) => {
            const selected = theme === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setTheme(value)}
                className={`inline-flex items-center gap-2 px-4 py-2 rounded text-caption font-medium transition-colors ${
                  selected
                    ? 'bg-surface-raised text-navy shadow-subtle border border-border-light'
                    : 'text-slate hover:text-navy'
                }`}
              >
                <Icon size={14} aria-hidden />
                {label}
              </button>
            );
          })}
        </div>
        <p className="mt-3 text-caption text-slate leading-relaxed">
          Both themes run on the same semantic tokens; printed reports always
          render in the light palette.
        </p>
      </CardBody>
    </Card>
  );
}

function UsersRolesPanel() {
  const { data: session } = useSession();
  const email = session?.user?.email ?? '';
  const name = session?.user?.name || email || 'Signed in';
  const roles = session?.user?.roles ?? [];
  const role = roles.length ? roleLabel(roles[0]) : 'Signed in';

  return (
    <Card>
      <CardHeader
        title="Users & roles"
        subtitle="Your signed-in account and access level"
        action={<StatusPill tone="success">You</StatusPill>}
      />
      <CardBody className="p-0">
        <ul className="divide-y divide-border-light">
          <li className="px-5 py-3 flex items-center gap-4">
            <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-teal text-white text-caption font-semibold shrink-0">
              {initialsFrom(name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-body text-navy font-medium truncate">{name}</p>
              {email && (
                <p className="text-caption text-slate truncate">{email}</p>
              )}
            </div>
            <StatusPill tone="action" className="shrink-0">
              {role}
            </StatusPill>
          </li>
        </ul>
        <p className="px-5 py-3 border-t border-border-light text-caption text-slate leading-relaxed">
          Roles are enforced server-side (admin · approver · analyst · viewer);
          viewer accounts are read-only. Team management for the institution is
          administered outside this workspace.
        </p>
      </CardBody>
    </Card>
  );
}

function DataComputePanel({ bankId }: { bankId: string | undefined }) {
  const health = useRiskServiceHealth();
  // Tiny query against the cashflow proxy — 503 means the ML sidecar is offline.
  const sidecarProbe = useCashflowHistory(bankId, 30);
  const connections = useMarketDataConnections(bankId);

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

  const connectionRows = connections.data?.connections ?? [];
  const activeConnections = connectionRows.filter(
    (c) => c.status === 'active'
  ).length;

  return (
    <Card>
      <CardHeader
        title="Data & compute"
        subtitle="Read-only view of the services and feeds behind this workspace"
      />
      <CardBody className="space-y-3">
        <div className="flex items-center justify-between gap-3 py-2 border-b border-border-light">
          <div className="min-w-0">
            <p className="text-body text-navy">Risk service API</p>
            <p className="text-caption text-slate font-mono truncate">
              {apiBaseUrl}
            </p>
          </div>
          <StatusPill tone={riskServiceTone} className="shrink-0">
            {riskServiceStatus}
          </StatusPill>
        </div>

        <div className="flex items-center justify-between gap-3 py-2 border-b border-border-light">
          <div className="min-w-0">
            <p className="text-body text-navy">Cash-flow ML sidecar</p>
            <p className="text-caption text-slate">
              LSTM daily forecasts — optional; the LCR forecasting page degrades
              gracefully
            </p>
          </div>
          <StatusPill tone={sidecarTone} className="shrink-0">
            {sidecarStatus}
          </StatusPill>
        </div>

        <div className="py-2 border-b border-border-light">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-body text-navy">Market data pulls</p>
              <p className="text-caption text-slate">
                Vendor connections managed in the Data Engine
              </p>
            </div>
            {connections.isLoading ? (
              <SkeletonLine width={64} height={18} />
            ) : (
              <StatusPill
                tone={activeConnections > 0 ? 'success' : 'slate'}
                className="shrink-0"
              >
                {activeConnections > 0
                  ? `${activeConnections} active`
                  : 'None connected'}
              </StatusPill>
            )}
          </div>
          {connectionRows.length > 0 && (
            <ul className="mt-2 space-y-1">
              {connectionRows.map((connection) => (
                <li
                  key={connection.id}
                  className="flex items-center justify-between gap-3 text-caption"
                >
                  <span className="text-navy/85 truncate">
                    {connection.displayName}
                  </span>
                  <span className="text-slate shrink-0">
                    {labelize(connection.status)}
                    {connection.lastPullAt &&
                      ` · last pull ${fmtRelative(connection.lastPullAt)}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="py-2">
          <p className="text-body text-navy">Official-run schedule</p>
          <p className="mt-1 text-caption text-slate leading-relaxed">
            The live engine recomputes automatically on every ingestion.
            Immutable official runs are minted on demand from each module
            dashboard (or the pipeline actions) — typically at period close,
            ahead of BSD filings.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

function AboutPanel({ bankId }: { bankId: string | undefined }) {
  const { query, byModule } = useLatestRunsByModule(bankId);
  const entries = Array.from(byModule.entries());
  const latest = entries
    .map(([, run]) => run)
    .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime())[0];

  return (
    <Card>
      <CardHeader
        title="About"
        subtitle="Engine versions from the persisted regulatory runs"
        action={latest ? <RunBadge run={latest} /> : undefined}
      />
      <CardBody className="p-0">
        {query.isLoading ? (
          <div className="p-5 space-y-3">
            <SkeletonLine width="60%" />
            <SkeletonLine width="45%" />
            <SkeletonLine width="52%" />
          </div>
        ) : entries.length === 0 ? (
          <p className="px-5 py-4 text-body text-slate">
            No successful runs yet — engine versions appear here once the first
            module run is persisted.
          </p>
        ) : (
          <ul className="divide-y divide-border-light">
            {entries.map(([module, run]) => (
              <li
                key={module}
                className="px-5 py-2.5 flex items-center justify-between gap-3"
              >
                <span className="text-body text-navy">
                  {MODULE_LABELS[module] ?? labelize(module)} engine
                </span>
                <span className="font-mono text-caption text-slate tnum">
                  {run.engineVersion}
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="px-5 py-3 border-t border-border-light bg-surface/60">
          <p className="text-caption text-slate leading-relaxed">
            Every calculation persists an immutable run with engine version and
            input hash · regulatory math executes server-side only · identical
            inputs reproduce identical outputs · synthetic demo dataset — no
            production bank data.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}
