'use client';

/**
 * Regulatory Reporting — Settings. Per-channel submission configuration:
 * institution code, contacts, reporting basis, the ORASS API endpoint
 * (base URL / auth mode / timeout / TLS from the regulator onboarding pack),
 * the ORASS sandbox behavior (ack / reject / slow / decline + downtime and
 * resubmission toggles for demoing the fallback + correction workflows),
 * the ORASS portal principal user, the email downtime recipient, and
 * write-only ORASS credentials (the API returns only the fingerprint —
 * mirroring the market-data vault pattern).
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
import { regShort } from '@/lib/format';

const CHANNELS: ChannelCode[] = ['orass_api', 'orass_sandbox', 'email', 'manual'];

const SANDBOX_BEHAVIORS = [
  { value: 'ack', label: 'Acknowledge (happy path)' },
  { value: 'reject', label: 'Reject (simulated server-side validation failure)' },
  { value: 'slow', label: 'Slow (pending for two polls, then acknowledge)' },
  { value: 'decline', label: 'Decline (terminal supervisor refusal with comments)' },
];

const RESUBMISSION_BEHAVIORS = [
  { value: 'grant', label: 'Grant resubmission requests' },
  { value: 'deny', label: 'Deny resubmission requests' },
];

const AUTH_MODES = [
  { value: 'api_key', label: 'API key' },
  { value: 'basic', label: 'Basic (username + password)' },
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
  resubmissionBehavior: string;
  downtime: boolean;
  fallbackRecipient: string;
  apiBaseUrl: string;
  authMode: string;
  timeoutSeconds: string;
  verifyTls: boolean;
  principalUserName: string;
  principalUserEmail: string;
  username: string;
  password: string;
  apiKey: string;
};

const EMPTY_FORM: FormState = {
  institutionCode: '',
  contactName: '',
  contactEmail: '',
  reportingBasis: 'solo',
  sandboxBehavior: 'ack',
  resubmissionBehavior: 'grant',
  downtime: false,
  fallbackRecipient: '',
  apiBaseUrl: '',
  authMode: 'api_key',
  timeoutSeconds: '',
  verifyTls: true,
  principalUserName: '',
  principalUserEmail: '',
  username: '',
  password: '',
  apiKey: '',
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
    resubmissionBehavior: String(raw.resubmission_behavior ?? 'grant'),
    downtime: raw.downtime === true,
    fallbackRecipient: String(raw.fallback_recipient ?? ''),
    apiBaseUrl: String(raw.api_base_url ?? ''),
    authMode: String(raw.auth_mode ?? 'api_key'),
    timeoutSeconds:
      raw.timeout_seconds == null ? '' : String(raw.timeout_seconds),
    verifyTls: raw.verify_tls !== false,
    principalUserName: String(raw.principal_user_name ?? ''),
    principalUserEmail: String(raw.principal_user_email ?? ''),
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
    if (channel === 'orass_api') {
      configPayload.api_base_url = form.apiBaseUrl.trim();
      configPayload.auth_mode = form.authMode;
      const timeout = Number.parseInt(form.timeoutSeconds, 10);
      if (Number.isFinite(timeout) && timeout > 0) {
        configPayload.timeout_seconds = timeout;
      }
      configPayload.verify_tls = form.verifyTls;
    }
    if (channel === 'orass_sandbox') {
      configPayload.sandbox_behavior = form.sandboxBehavior;
      configPayload.resubmission_behavior = form.resubmissionBehavior;
      configPayload.downtime = form.downtime;
    }
    if (channel === 'orass_api' || channel === 'orass_sandbox') {
      if (form.principalUserName.trim()) {
        configPayload.principal_user_name = form.principalUserName.trim();
      }
      if (form.principalUserEmail.trim()) {
        configPayload.principal_user_email = form.principalUserEmail.trim();
      }
    }
    if (channel === 'email' && form.fallbackRecipient.trim()) {
      configPayload.fallback_recipient = form.fallbackRecipient.trim();
    }
    // Credentials stay write-only: only sent when the operator typed material.
    let credentials: Record<string, unknown> | undefined;
    if (channel === 'orass_api' && form.authMode === 'api_key') {
      if (form.apiKey.trim() !== '') credentials = { api_key: form.apiKey.trim() };
    } else if (channel === 'orass_api' || channel === 'orass_sandbox') {
      if (form.username.trim() !== '' || form.password !== '') {
        credentials = { username: form.username.trim(), password: form.password };
      }
    }
    save.mutate(
      { channel, config: configPayload, credentials },
      {
        onSuccess: () =>
          setForm((prev) => ({ ...prev, username: '', password: '', apiKey: '' })),
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

            {channel === 'orass_api' && (
              <>
                <div className="md:col-span-2">
                  <label className={labelClass} htmlFor="api-base-url">
                    API base URL{' '}
                    <span className="font-normal text-critical">(required)</span>
                  </label>
                  <input
                    id="api-base-url"
                    value={form.apiBaseUrl}
                    onChange={(e) => set('apiBaseUrl', e.target.value)}
                    placeholder="https://orass-portal.example/api"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass} htmlFor="auth-mode">
                    Auth mode
                  </label>
                  <select
                    id="auth-mode"
                    value={form.authMode}
                    onChange={(e) => set('authMode', e.target.value)}
                    className={inputClass}
                  >
                    {AUTH_MODES.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelClass} htmlFor="timeout-seconds">
                    Timeout (seconds)
                  </label>
                  <input
                    id="timeout-seconds"
                    type="number"
                    min={1}
                    value={form.timeoutSeconds}
                    onChange={(e) => set('timeoutSeconds', e.target.value)}
                    placeholder="30"
                    className={inputClass}
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="inline-flex items-center gap-2.5 text-body text-navy cursor-pointer">
                    <input
                      type="checkbox"
                      checked={form.verifyTls}
                      onChange={(e) => set('verifyTls', e.target.checked)}
                      className="w-4 h-4 accent-[rgb(var(--accent))]"
                    />
                    Verify TLS certificates
                  </label>
                </div>
                <p className="md:col-span-2 -mt-2 text-micro text-slate leading-relaxed">
                  Endpoint + credentials come from your {regShort()}/Regnology
                  onboarding pack; the wire contract is aligned at onboarding.
                </p>
              </>
            )}

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
                <div>
                  <label className={labelClass} htmlFor="resubmission-behavior">
                    Resubmission behavior
                  </label>
                  <select
                    id="resubmission-behavior"
                    value={form.resubmissionBehavior}
                    onChange={(e) => set('resubmissionBehavior', e.target.value)}
                    className={inputClass}
                  >
                    {RESUBMISSION_BEHAVIORS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex items-start">
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

            {(channel === 'orass_api' || channel === 'orass_sandbox') && (
              <>
                <div className="md:col-span-2 pt-2">
                  <p className="text-body font-medium text-navy">
                    ORASS portal principal user
                  </p>
                  <p className="mt-1 text-micro text-slate leading-relaxed">
                    Only the ORASS Principal user can submit on the portal;
                    AequorOS gates submission to approver-role logins.
                  </p>
                </div>
                <div>
                  <label className={labelClass} htmlFor="principal-user-name">
                    Principal user name
                  </label>
                  <input
                    id="principal-user-name"
                    value={form.principalUserName}
                    onChange={(e) => set('principalUserName', e.target.value)}
                    placeholder="Name / designation"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass} htmlFor="principal-user-email">
                    Principal user email
                  </label>
                  <input
                    id="principal-user-email"
                    type="email"
                    value={form.principalUserEmail}
                    onChange={(e) => set('principalUserEmail', e.target.value)}
                    placeholder="principal@bank.example"
                    className={inputClass}
                  />
                </div>
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
                  The {regShort()} downtime-return address is UNKNOWN in the public
                  record; bsdletters@bog.gov.gh is confirmed only for
                  directive-consultation correspondence. Use the address your
                  {regShort()} supervision contact provides.
                </p>
              </div>
            )}
          </div>

          {(channel === 'orass_api' || channel === 'orass_sandbox') && (
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
                {channel === 'orass_api' && form.authMode === 'api_key' ? (
                  <div>
                    <label className={labelClass} htmlFor="orass-api-key">
                      API key
                    </label>
                    <input
                      id="orass-api-key"
                      type="password"
                      value={form.apiKey}
                      onChange={(e) => set('apiKey', e.target.value)}
                      autoComplete="new-password"
                      className={inputClass}
                    />
                  </div>
                ) : (
                  <>
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
                  </>
                )}
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
            ({regShort()}/Regnology-issued specs and credentials) is a configuration
            swap behind the same channel interface.
          </p>
        </div>
      </div>
    </div>
  );
}
