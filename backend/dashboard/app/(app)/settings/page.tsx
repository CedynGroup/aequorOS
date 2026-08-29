'use client';

/**
 * Settings — token'd governance cards:
 *   · Institution profile — identity from the corporate register (single
 *     source of truth, managed under Governance → Institution Profile) plus
 *     platform reporting facts from the bank record
 *   · Appearance — real theme toggle (ThemeProvider)
 *   · Members — tenant-scoped identity, lifecycle, and indivisible scoped grants
 *   · Your account — the signed-in account and its permanent signer identity
 *   · Data & compute — real service health, market-data connections, and the
 *     official-run schedule note (read-only)
 *   · About — engine versions and provenance from persisted regulatory runs
 */

import Link from 'next/link';
import type { InstitutionProfileRead } from '@aequoros/risk-service-api';
import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { Monitor, Moon, Sun } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import AuthenticationPanel from '@/components/settings/AuthenticationPanel';
import MembersPanel from '@/components/settings/MembersPanel';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import CopyButton from '@/components/ui/CopyButton';
import RunBadge from '@/components/ui/RunBadge';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useTheme,
  type ThemePreference,
} from '@/components/shell/ThemeProvider';
import { useUserProfile } from '@/components/profile/ProfileProvider';
import { MODULE_LABELS, useLatestRunsByModule } from '@/components/reports/hooks';
import { apiBaseUrl, apiOrigin } from '@/lib/api/client';
import {
  useBank,
  useCashflowHistory,
  useInstitutionProfile,
  useMarketDataConnections,
  useMySignerIdentity,
} from '@/lib/api/hooks';
import { fmtRelative, labelize } from '@/lib/api/values';
import { avatarColor, initialsFrom, roleLabel } from '@/lib/api/identity';

/** A copyable identifier row for the identity grid. */
function IdField({
  label,
  value,
  wide = true,
}: {
  label: string;
  value: string | undefined | null;
  wide?: boolean;
}) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
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
  const registerQuery = useInstitutionProfile(bank?.id);
  const register = registerQuery.data?.profile ?? null;

  return (
    <>
      <PageHeader
        title="Settings"
      />

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <MembersPanel />
        <InstitutionProfile
          profile={profile}
          register={register}
          registerLoading={registerQuery.isLoading}
          periodCount={periods.length}
          latestPeriodLabel={periods[0]?.label}
        />
        <div className="space-y-6">
          <AppearancePanel />
          <CurrentAccountPanel />
        </div>
        <AuthenticationPanel />
        <DataComputePanel bankId={bank?.id} />
        <AboutPanel bankId={bank?.id} />
      </div>
    </>
  );
}

/** A plain label/value pair for the identity grid. */
function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className={`mt-1 text-navy ${mono ? 'font-mono' : ''}`}>
        {value || '—'}
      </dd>
    </div>
  );
}

function InstitutionProfile({
  profile,
  register,
  registerLoading,
  periodCount,
  latestPeriodLabel,
}: {
  profile: ReturnType<typeof useBank>['data'] | null;
  register: InstitutionProfileRead | null;
  registerLoading: boolean;
  periodCount: number;
  latestPeriodLabel: string | undefined;
}) {
  return (
    <Card>
      <CardHeader
        title="Institution profile"
        subtitle="Identity from the corporate register · reporting facts from the risk service"
        action={
          <Link
            href="/institution"
            className="text-caption font-medium text-action hover:text-action-hover"
          >
            Manage register →
          </Link>
        }
      />
      <CardBody>
        {registerLoading ? (
          <div className="space-y-3">
            <SkeletonLine className="w-2/3" />
            <SkeletonLine className="w-1/2" />
            <SkeletonLine className="w-3/5" />
          </div>
        ) : (
          <>
            {!register && (
              <p className="mb-4 rounded border border-warning/25 bg-warning-light/50 px-3.5 py-2.5 text-caption text-navy/85">
                No corporate profile configured yet — the fields below fall
                back to the platform bank record.{' '}
                <Link
                  href="/institution"
                  className="font-medium text-action hover:text-action-hover"
                >
                  Set up the register →
                </Link>
              </p>
            )}
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-body">
              <Field label="Legal name" value={profile?.name} />
              <Field
                label="Institution type"
                value={register?.institutionType}
              />
              <Field
                label="Registration number"
                value={register?.registrationNumber}
                mono
              />
              <Field
                label="ORASS institution code"
                value={register?.orassInstitutionCode}
                mono
              />
              <Field label="TIN" value={register?.tin} mono />
              <Field
                label="Legal entity structure"
                value={register?.legalEntityStructure}
              />
              <Field
                label="Jurisdiction"
                value={
                  profile
                    ? profile.jurisdiction?.countryName ??
                      profile.jurisdictionCode
                    : null
                }
              />
              <Field
                label="Regulator"
                value={profile?.jurisdiction?.centralBankName}
              />
              <Field
                label="License class"
                value={profile ? labelize(profile.licenseType) : null}
              />
              <Field
                label="Reporting currency"
                value={profile?.currency}
                mono
              />
              <Field
                label="Reporting periods"
                value={
                  `${periodCount} loaded` +
                  (latestPeriodLabel ? ` · latest ${latestPeriodLabel}` : '')
                }
              />
              <IdField
                label="Institution ID"
                value={profile?.id}
                wide={false}
              />
              <IdField
                label="Organization ID"
                value={profile?.organizationId}
                wide={false}
              />
            </dl>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function AppearancePanel() {
  const { theme, setTheme } = useTheme();
  const options: {
    value: ThemePreference;
    label: string;
    Icon: typeof Sun;
  }[] = [
    { value: 'dark', label: 'Dark', Icon: Moon },
    { value: 'light', label: 'Light', Icon: Sun },
    { value: 'system', label: 'System', Icon: Monitor },
  ];
  return (
    <Card>
      <CardHeader
        title="Appearance"
        subtitle="Theme preference — synced to your profile"
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

function CurrentAccountPanel() {
  const { data: session } = useSession();
  const { profile } = useUserProfile();
  const email = profile?.email ?? session?.user?.email ?? '';
  const name =
    profile?.displayName || session?.user?.name || email || 'Signed in';
  const roles = session?.user?.roles ?? [];
  const role = profile?.role
    ? roleLabel(profile.role)
    : roles.length
      ? roleLabel(roles[0])
      : 'Signed in';
  const avatarBackground = avatarColor(profile?.userId ?? email);

  return (
    <Card>
      <CardHeader
        title="Your account"
        action={<StatusPill tone="success">You</StatusPill>}
      />
      <CardBody className="p-0">
        <ul className="divide-y divide-border-light">
          <li className="px-5 py-3 flex items-center gap-4">
            <span
              className="inline-flex items-center justify-center w-8 h-8 rounded-full text-white text-caption font-semibold shrink-0"
              style={{ backgroundColor: avatarBackground }}
            >
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
        <SignerIdentityRow />
      </CardBody>
    </Card>
  );
}

/**
 * Your permanent signer identity (docs/attestation_esignature.md §2.5).
 *
 * Presented in monospace with a copy control, exactly like the BK-/OR- platform
 * IDs above, because it is the same kind of thing: an opaque, permanent
 * identifier. It is what every signature you ever record is attributed to, and
 * it survives your user row being deprovisioned — so it is the identifier an
 * attribution question years from now actually turns on. The same string appears
 * beneath the rendered signature block and stamped inside the signed PDF; §2.5
 * requires all three to agree.
 */
function SignerIdentityRow() {
  const identity = useMySignerIdentity();

  if (identity.isLoading) {
    return (
      <div className="px-5 py-3 border-t border-border-light">
        <SkeletonLine width="45%" />
      </div>
    );
  }
  // A viewer-only or service principal legitimately has no signer identity;
  // failing quietly is right here — this card is not the place to explain why.
  if (identity.error || !identity.data) return null;

  return (
    <div className="px-5 py-3 border-t border-border-light">
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <IdField label="Signer ID" value={identity.data.signerId} wide={false} />
        <div>
          <dt className="text-micro font-medium uppercase tracking-wider text-slate">
            Signing key
          </dt>
          <dd className="mt-1 flex items-center gap-2">
            <StatusPill tone={identity.data.hasActiveKey ? 'success' : 'amber'}>
              {identity.data.hasActiveKey ? 'Enrolled' : 'Not enrolled'}
            </StatusPill>
            <span className="text-caption text-slate">
              provisioned {fmtRelative(identity.data.provisionedAt)}
            </span>
          </dd>
        </div>
      </dl>
      {!identity.data.hasActiveKey && (
        <p className="mt-2 text-caption text-slate leading-relaxed">
          You hold a signer identity but no active signing key, so certification
          is refused rather than recorded unsigned. An administrator enrols the
          key.
        </p>
      )}
    </div>
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
      {/* flex-wrap lets the RunBadge chip drop below the title block instead of
          overlapping it when the card is narrow (two-column settings grid). */}
      <CardHeader
        className="flex-wrap"
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
            inputs reproduce identical outputs.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}
