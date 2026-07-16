'use client';

/**
 * Regulatory Reporting — Settings. Per-channel submission configuration:
 * institution code, contacts, reporting basis, the ORASS sandbox behavior
 * (ack / reject / slow + downtime toggle for demoing the email fallback),
 * the email downtime recipient, and write-only ORASS credentials (the API
 * returns only the fingerprint — mirroring the market-data vault pattern).
 */

import { useEffect, useState } from 'react';
import { FlaskConical, KeyRound, Loader2, Save } from 'lucide-react';
import type { ChannelCode, ChannelConfigRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isChannelConfigMissingError,
  useChannelConfig,
  useSaveChannelConfig,
} from '@/lib/api/hooks';
import { fmtTimestamp, shortId } from '@/lib/api/values';
import { CHANNEL_LABELS } from '@/components/submissions/shared';

const CHANNELS: ChannelCode[] = ['orass_sandbox', 'email', 'manual'];

const SANDBOX_BEHAVIORS = [
  { value: 'ack', label: 'Acknowledge (happy path)' },
  { value: 'reject', label: 'Reject (simulated server-side validation failure)' },
  { value: 'slow', label: 'Slow (pending for two polls, then acknowledge)' },
];

const BASIS_OPTIONS = [
  { value: 'solo', label: 'Solo (standalone licensed entity)' },
  { value: 'consolidated', label: 'Consolidated (banking group)' },
];

type FormState = {
  institutionCode: string;
  contactName: string;
  contactEmail: string;
  reportingBasis: string;
  sandboxBehavior: string;
  downtime: boolean;
  fallbackRecipient: string;
  username: string;
  password: string;
};

const EMPTY_FORM: FormState = {
  institutionCode: '',
  contactName: '',
  contactEmail: '',
  reportingBasis: 'solo',
  sandboxBehavior: 'ack',
  downtime: false,
  fallbackRecipient: '',
  username: '',
  password: '',
};

function formFromConfig(config: ChannelConfigRead | undefined): FormState {
  if (!config) return EMPTY_FORM;
  const raw = config.config as Record<string, unknown>;
  return {
    ...EMPTY_FORM,
    institutionCode: String(raw.institution_code ?? ''),
    contactName: String(raw.contact_name ?? ''),
    contactEmail: String(raw.contact_email ?? ''),
    reportingBasis: String(raw.reporting_basis ?? 'solo'),
    sandboxBehavior: String(raw.sandbox_behavior ?? 'ack'),
    downtime: raw.downtime === true,
    fallbackRecipient: String(raw.fallback_recipient ?? ''),
  };
}

export default function SettingsPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const [channel, setChannel] = useState<ChannelCode>('orass_sandbox');

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting', href: '/submissions' },
          { label: 'Settings' },
        ]}
        title="Channel settings"
        subtitle="Per-channel submission configuration · credentials are write-only (fingerprint back, never the material)"
        action={
          <label className="flex items-center gap-2 text-caption text-slate">
            Channel
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value as ChannelCode)}
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
            >
              {CHANNELS.map((code) => (
                <option key={code} value={code}>
                  {CHANNEL_LABELS[code]}
                </option>
              ))}
            </select>
          </label>
        }
      />

      <div className="px-8 py-6">
        {bankId && <ChannelForm key={channel} bankId={bankId} channel={channel} />}
      </div>
    </>
  );
}

function ChannelForm({
  bankId,
  channel,
}: {
  bankId: string;
  channel: ChannelCode;
}) {
  const configQuery = useChannelConfig(bankId, channel);
  const save = useSaveChannelConfig(bankId);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [hydrated, setHydrated] = useState(false);

  const unconfigured = isChannelConfigMissingError(configQuery.error);
  const config = configQuery.data;

  useEffect(() => {
    if (hydrated) return;
    if (config) {
      setForm(formFromConfig(config));
      setHydrated(true);
    } else if (unconfigured) {
      setForm(EMPTY_FORM);
      setHydrated(true);
    }
  }, [config, unconfigured, hydrated]);

  if (configQuery.isLoading || !hydrated) {
    return <SkeletonCard />;
  }
  if (configQuery.error && !unconfigured) {
    return (
      <ErrorPanel
        error={configQuery.error}
        onRetry={() => configQuery.refetch()}
        title="Could not load the channel configuration"
      />
    );
  }

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const handleSave = () => {
    const configPayload: Record<string, unknown> = {
      institution_code: form.institutionCode.trim(),
      contact_name: form.contactName.trim(),
      contact_email: form.contactEmail.trim(),
      reporting_basis: form.reportingBasis,
    };
    if (channel === 'orass_sandbox') {
      configPayload.sandbox_behavior = form.sandboxBehavior;
      configPayload.downtime = form.downtime;
    }
    if (channel === 'email' && form.fallbackRecipient.trim()) {
      configPayload.fallback_recipient = form.fallbackRecipient.trim();
    }
    const hasCredentials =
      channel === 'orass_sandbox' &&
      (form.username.trim() !== '' || form.password !== '');
    save.mutate(
      {
        channel,
        config: configPayload,
        credentials: hasCredentials
          ? { username: form.username.trim(), password: form.password }
          : undefined,
      },
      {
        onSuccess: () => setForm((prev) => ({ ...prev, username: '', password: '' })),
      }
    );
  };

  const inputClass =
    'w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light';
  const labelClass = 'block text-caption font-medium text-navy mb-1.5';

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
      <div className="xl:col-span-2">
        <SectionCard
          title={CHANNEL_LABELS[channel]}
          subtitle={
            unconfigured && !save.isSuccess
              ? 'Not configured yet — saving creates the configuration'
              : config
              ? `Last updated ${fmtTimestamp(config.updatedAt)}`
              : undefined
          }
          actions={
            <button
              type="button"
              disabled={save.isPending}
              onClick={handleSave}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {save.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <Save size={13} aria-hidden />
              )}
              Save configuration
            </button>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl">
            <div>
              <label className={labelClass} htmlFor="institution-code">
                Institution code
              </label>
              <input
                id="institution-code"
                value={form.institutionCode}
                onChange={(e) => set('institutionCode', e.target.value)}
                placeholder="e.g. SBL"
                className={inputClass}
              />
              <p className="mt-1 text-micro text-slate leading-relaxed">
                Internal identifier — the ORASS institution-code scheme is not
                public (research gap G9).
              </p>
            </div>
            <div>
              <label className={labelClass} htmlFor="reporting-basis">
                Reporting basis
              </label>
              <select
                id="reporting-basis"
                value={form.reportingBasis}
                onChange={(e) => set('reportingBasis', e.target.value)}
                className={inputClass}
              >
                {BASIS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass} htmlFor="contact-name">
                Reporting contact
              </label>
              <input
                id="contact-name"
                value={form.contactName}
                onChange={(e) => set('contactName', e.target.value)}
                placeholder="Name / designation"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass} htmlFor="contact-email">
                Contact email
              </label>
              <input
                id="contact-email"
                type="email"
                value={form.contactEmail}
                onChange={(e) => set('contactEmail', e.target.value)}
                placeholder="reporting@bank.example"
                className={inputClass}
              />
            </div>

            {channel === 'orass_sandbox' && (
              <>
                <div>
                  <label className={labelClass} htmlFor="sandbox-behavior">
                    Sandbox behavior
                  </label>
                  <select
                    id="sandbox-behavior"
                    value={form.sandboxBehavior}
                    onChange={(e) => set('sandboxBehavior', e.target.value)}
                    className={inputClass}
                  >
                    {SANDBOX_BEHAVIORS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex items-start pt-6">
                  <label className="inline-flex items-center gap-2.5 text-body text-navy cursor-pointer">
                    <input
                      type="checkbox"
                      checked={form.downtime}
                      onChange={(e) => set('downtime', e.target.checked)}
                      className="w-4 h-4 accent-[rgb(var(--accent))]"
                    />
                    Simulate ORASS downtime
                  </label>
                </div>
                <p className="md:col-span-2 -mt-2 text-micro text-slate leading-relaxed">
                  With downtime on, ORASS submissions return the structured
                  BG/FMD/2026/07 fallback — use it to demo the email downtime
                  workflow and the subsequent ORASS re-upload.
                </p>
              </>
            )}

            {channel === 'email' && (
              <div className="md:col-span-2">
                <label className={labelClass} htmlFor="fallback-recipient">
                  Downtime return recipient
                </label>
                <input
                  id="fallback-recipient"
                  type="email"
                  value={form.fallbackRecipient}
                  onChange={(e) => set('fallbackRecipient', e.target.value)}
                  placeholder="Supervisor-provided return-desk address"
                  className={inputClass}
                />
                <p className="mt-1 text-micro text-slate leading-relaxed">
                  The BoG downtime-return address is UNKNOWN in the public
                  record; bsdletters@bog.gov.gh is confirmed only for
                  directive-consultation correspondence. Use the address your
                  BoG supervision contact provides.
                </p>
              </div>
            )}
          </div>

          {channel === 'orass_sandbox' && (
            <div className="mt-6 pt-5 border-t border-border-light max-w-3xl">
              <p className="inline-flex items-center gap-2 text-body font-medium text-navy">
                <KeyRound size={14} className="text-slate" aria-hidden />
                ORASS credentials (write-only)
              </p>
              <p className="mt-1 text-caption text-slate leading-relaxed">
                Encrypted with AES-256-GCM in the credential vault and
                retrieved per submission cycle only. Responses expose the
                SHA-256 fingerprint — never the material. Leave blank to keep
                the stored credential.
              </p>
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className={labelClass} htmlFor="orass-username">
                    Portal username
                  </label>
                  <input
                    id="orass-username"
                    value={form.username}
                    onChange={(e) => set('username', e.target.value)}
                    autoComplete="off"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass} htmlFor="orass-password">
                    Portal password
                  </label>
                  <input
                    id="orass-password"
                    type="password"
                    value={form.password}
                    onChange={(e) => set('password', e.target.value)}
                    autoComplete="new-password"
                    className={inputClass}
                  />
                </div>
              </div>
            </div>
          )}

          {save.error && (
            <div className="mt-4 max-w-3xl">
              <ErrorPanel error={save.error} title="Could not save" />
            </div>
          )}
          {save.isSuccess && (
            <p className="mt-4 text-caption text-success font-medium">
              Configuration saved.
            </p>
          )}
        </SectionCard>
      </div>

      <div className="space-y-6">
        <SectionCard title="Credential status">
          {config?.hasCredentials || save.data?.hasCredentials ? (
            <div className="space-y-2">
              <StatusPill tone="success">Credentials stored</StatusPill>
              <p className="font-mono text-caption text-slate tnum">
                fingerprint{' '}
                {shortId(
                  save.data?.credentialFingerprint ??
                    config?.credentialFingerprint ??
                    '',
                  16
                )}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <StatusPill tone="slate">No credentials stored</StatusPill>
              <p className="text-caption text-slate leading-relaxed">
                The ORASS sandbox works credential-less; storing credentials
                exercises the vault seam that real ORASS onboarding will use.
              </p>
            </div>
          )}
        </SectionCard>

        <div className="card px-5 py-4 flex items-start gap-3">
          <FlaskConical size={15} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <p className="text-caption text-navy/80 leading-relaxed">
            ORASS integration ships as a clearly-labeled sandbox simulator —
            the portal&apos;s API is not publicly documented. Real onboarding
            (BoG/Regnology-issued specs and credentials) is a configuration
            swap behind the same channel interface.
          </p>
        </div>
      </div>
    </div>
  );
}
