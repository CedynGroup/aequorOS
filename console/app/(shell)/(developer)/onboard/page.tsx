'use client';

import { useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, Eye, Loader2 } from 'lucide-react';
import {
  ApiError,
  provisionTenant,
  toApiError,
  type ProvisionStepStatus,
  type ProvisionTenantRequest,
  type ProvisionTenantResponse,
} from '@/lib/api';
import {
  Button,
  CopyButton,
  ErrorPanel,
  Field,
  FieldRow,
  Input,
  MonoId,
  PageHeader,
  SectionCard,
  Select,
  StatusPill,
  Stepper,
  type Step,
  type StepStatus,
  type StatusTone,
} from '@/components/ui';

/**
 * /onboard — tenant provisioning.
 * Source: POST /operator/v1/tenants (the saga endpoint). The saga logic is
 * unchanged from the original page — this refit only re-skins it onto the
 * Stepper / SectionCard / Form primitives. The step list is rendered exactly as
 * the API returns it (succeeded / failed / skipped / rolled_back) and the
 * handoff panel only appears when the API returned real identifiers.
 */

// Hardcoded: the four codes seeded in the global `jurisdictions` registry
// (GH/NG/KE/ZA). This becomes an operator API lookup once a
// GET /operator/v1/jurisdictions endpoint exists — do not grow this list by
// hand beyond the seeded four.
const JURISDICTIONS = [
  { code: 'GH', label: 'Ghana', currency: 'GHS' },
  { code: 'NG', label: 'Nigeria', currency: 'NGN' },
  { code: 'KE', label: 'Kenya', currency: 'KES' },
  { code: 'ZA', label: 'South Africa', currency: 'ZAR' },
] as const;

// license_type is a free string on the backend (banks.license_type,
// String(40)); these presets are suggestions, not an enum.
const LICENSE_PRESETS = ['universal', 'commercial', 'savings_and_loans', 'rural'];

type Phase = 'form' | 'review' | 'submitting' | 'done';

const EMPTY_FORM: ProvisionTenantRequest = {
  organization_name: '',
  bank_name: '',
  license_type: '',
  jurisdiction_code: 'GH',
  currency: 'GHS',
  admin_email: '',
  admin_full_name: '',
};

const PHASE_INDEX: Record<Phase, number> = { form: 0, review: 1, submitting: 2, done: 3 };

const STEP_TONE: Record<ProvisionStepStatus, StatusTone> = {
  succeeded: 'success',
  failed: 'critical',
  rolled_back: 'amber',
  skipped: 'slate',
};

export default function OnboardPage() {
  const [phase, setPhase] = useState<Phase>('form');
  const [form, setForm] = useState<ProvisionTenantRequest>(EMPTY_FORM);
  const [currencyTouched, setCurrencyTouched] = useState(false);
  const [result, setResult] = useState<ProvisionTenantResponse | null>(null);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [otpRevealed, setOtpRevealed] = useState(false);

  function set<K extends keyof ProvisionTenantRequest>(key: K, value: ProvisionTenantRequest[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function onJurisdictionChange(code: string) {
    setForm((f) => {
      const j = JURISDICTIONS.find((x) => x.code === code);
      return {
        ...f,
        jurisdiction_code: code,
        // Prefill the jurisdiction's currency but never clobber an operator
        // override — banks.currency has NO default on the backend and the
        // operator must be able to set it deliberately.
        currency: currencyTouched || !j ? f.currency : j.currency,
      };
    });
  }

  const currencyValid = /^[A-Z]{3}$/.test(form.currency);
  const formComplete =
    form.organization_name.trim() !== '' &&
    form.bank_name.trim() !== '' &&
    form.license_type.trim() !== '' &&
    form.jurisdiction_code !== '' &&
    currencyValid &&
    /.+@.+\..+/.test(form.admin_email) &&
    form.admin_full_name.trim() !== '';

  async function provision() {
    setPhase('submitting');
    setSubmitError(null);
    setResult(null);
    setOtpRevealed(false);
    try {
      const res = await provisionTenant({
        ...form,
        organization_name: form.organization_name.trim(),
        bank_name: form.bank_name.trim(),
        license_type: form.license_type.trim(),
        admin_email: form.admin_email.trim(),
        admin_full_name: form.admin_full_name.trim(),
      });
      setResult(res);
    } catch (err) {
      setSubmitError(toApiError(err));
    }
    setPhase('done');
  }

  // The API's own verdict is the authority; the id null-checks only narrow
  // the types for rendering.
  const succeeded = Boolean(result?.succeeded && result.organization_id && result.bank_id);
  const failedStep = result?.steps.find((s) => s.status === 'failed') ?? null;
  const handoffEmail = result?.admin_email ?? form.admin_email;
  const failedRun = phase === 'done' && (Boolean(submitError) || Boolean(result && !succeeded));

  function resetAll() {
    setForm(EMPTY_FORM);
    setCurrencyTouched(false);
    setResult(null);
    setSubmitError(null);
    setOtpRevealed(false);
    setPhase('form');
  }

  const steps: Step[] = [
    { key: 'form', label: 'Details' },
    { key: 'review', label: 'Review' },
    {
      key: 'provision',
      label: 'Provision',
      status: failedRun ? ('error' as StepStatus) : undefined,
    },
    {
      key: 'done',
      label: 'Complete',
      status: failedRun ? ('error' as StepStatus) : undefined,
    },
  ];

  return (
    <div className="max-w-3xl">
      <PageHeader
        title="Onboard a tenant"
        subtitle="Provisions the organization, bank, and first admin through the operator API's saga. Nothing is created until you confirm the review step."
      />

      <div className="card mb-6 px-5 py-4">
        <Stepper steps={steps} current={PHASE_INDEX[phase]} />
      </div>

      {/* ---------------------------------------------------------- form */}
      {phase === 'form' && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (formComplete) setPhase('review');
          }}
        >
          <SectionCard
            title="Institution details"
            subtitle="Jurisdictions are the four seeded in the registry (GH/NG/KE/ZA); no jurisdictions endpoint exists yet."
            footer={
              <span className="text-caption text-slate">
                A bank has no default currency — it reports in exactly the unit set here.
              </span>
            }
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Organization name" required>
                <Input
                  value={form.organization_name}
                  onChange={(e) => set('organization_name', e.target.value)}
                  placeholder="e.g. Horizon Financial Group"
                  required
                />
              </Field>
              <Field label="Bank name" required>
                <Input
                  value={form.bank_name}
                  onChange={(e) => set('bank_name', e.target.value)}
                  placeholder="e.g. Horizon Bank Ghana"
                  required
                />
              </Field>
              <Field label="License type" required hint="Free text; presets are suggestions.">
                <Input
                  value={form.license_type}
                  onChange={(e) => set('license_type', e.target.value)}
                  list="license-presets"
                  placeholder="e.g. universal"
                  required
                />
                <datalist id="license-presets">
                  {LICENSE_PRESETS.map((l) => (
                    <option key={l} value={l} />
                  ))}
                </datalist>
              </Field>
              <Field label="Jurisdiction" required>
                <Select
                  value={form.jurisdiction_code}
                  onChange={(e) => onJurisdictionChange(e.target.value)}
                >
                  {JURISDICTIONS.map((j) => (
                    <option key={j.code} value={j.code}>
                      {j.code} — {j.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Reporting currency"
                required
                error={form.currency && !currencyValid ? 'Must be a 3-letter ISO-4217 code.' : undefined}
                hint="Prefilled from the jurisdiction; override deliberately — the backend has no default."
              >
                <Input
                  className="font-mono uppercase"
                  value={form.currency}
                  maxLength={3}
                  invalid={Boolean(form.currency) && !currencyValid}
                  onChange={(e) => {
                    setCurrencyTouched(true);
                    set('currency', e.target.value.toUpperCase());
                  }}
                  placeholder="GHS"
                  required
                />
              </Field>
              <Field label="Admin email" required>
                <Input
                  type="email"
                  value={form.admin_email}
                  onChange={(e) => set('admin_email', e.target.value)}
                  placeholder="admin@bank.example"
                  required
                />
              </Field>
              <Field label="Admin full name" required className="sm:col-span-2">
                <Input
                  value={form.admin_full_name}
                  onChange={(e) => set('admin_full_name', e.target.value)}
                  placeholder="First admin of the institution"
                  required
                />
              </Field>
            </div>

            <div className="mt-4 flex justify-end border-t border-border-light pt-4">
              <Button type="submit" disabled={!formComplete}>
                Review
              </Button>
            </div>
          </SectionCard>
        </form>
      )}

      {/* -------------------------------------------------------- review */}
      {phase === 'review' && (
        <SectionCard
          title="This will create…"
          subtitle="The API runs this as a saga: organization → bank → admin. Each step reports its own outcome, and a failure rolls back what preceded it."
        >
          <div className="divide-y divide-border-light">
            <FieldRow label="Organization">{form.organization_name}</FieldRow>
            <FieldRow label="Bank">{form.bank_name}</FieldRow>
            <FieldRow label="License type">{form.license_type}</FieldRow>
            <FieldRow label="Jurisdiction">
              {form.jurisdiction_code} —{' '}
              {JURISDICTIONS.find((j) => j.code === form.jurisdiction_code)?.label ?? ''}
            </FieldRow>
            <FieldRow label="Reporting currency">
              <span className="font-mono">{form.currency}</span>
            </FieldRow>
            <FieldRow label="First admin">
              {form.admin_full_name} · <span className="font-mono">{form.admin_email}</span>
            </FieldRow>
          </div>
          <div className="mt-4 flex justify-between border-t border-border-light pt-4">
            <Button variant="secondary" onClick={() => setPhase('form')}>
              Back
            </Button>
            <Button onClick={() => void provision()}>Provision tenant</Button>
          </div>
        </SectionCard>
      )}

      {/* ------------------------------------------- submitting / result */}
      {phase === 'submitting' && (
        <div className="card flex items-center gap-3 p-5">
          <Loader2 size={18} className="animate-spin text-action" aria-hidden />
          <div>
            <p className="text-body font-medium text-navy">Provisioning…</p>
            <p className="text-caption text-slate">
              Running the saga on the operator API. Do not close this tab.
            </p>
          </div>
        </div>
      )}

      {phase === 'done' && submitError && (
        <div className="space-y-4">
          <ErrorPanel error={submitError} context="Provisioning request" />
          <p className="text-caption text-slate">
            The request itself failed — the API returned no step record, so the saga may not have
            started. Verify on the{' '}
            <Link href="/tenants" className="text-action hover:underline">
              tenant list
            </Link>{' '}
            before retrying.
          </p>
          <Button variant="secondary" onClick={() => setPhase('review')}>
            Back to review
          </Button>
        </div>
      )}

      {phase === 'done' && result && (
        <div className="space-y-4">
          {/* Saga step record — rendered verbatim from the API */}
          <SectionCard title="Provisioning steps" noPadding>
            <ul className="divide-y divide-border-light">
              {result.steps.map((s, i) => (
                <li key={`${s.step}-${i}`} className="flex items-start justify-between gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <span className="font-mono text-body text-ink">{s.step}</span>
                    {s.detail && (
                      <p className="mt-0.5 break-words text-caption text-slate">{s.detail}</p>
                    )}
                  </div>
                  <StatusPill tone={STEP_TONE[s.status]}>{s.status.replace(/_/g, ' ')}</StatusPill>
                </li>
              ))}
            </ul>
          </SectionCard>

          {/* API warnings — rendered verbatim, success or not */}
          {result.warnings.length > 0 && (
            <div className="card border-warning/40 p-4">
              <div className="flex items-start gap-2">
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-body font-medium text-navy">Warnings from the API</p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-caption text-slate">
                    {result.warnings.map((w, i) => (
                      <li key={i} className="break-words">
                        {w}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}

          {succeeded && result.organization_id && result.bank_id ? (
            <div className="card border-success/40 p-5">
              <h2 className="text-h3 text-navy">Tenant provisioned</h2>

              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <div className="rounded-md border border-border-light bg-surface p-3">
                  <div className="text-micro uppercase tracking-wide text-slate">Bank</div>
                  <MonoId id={result.bank_id} className="mt-1" />
                </div>
                <div className="rounded-md border border-border-light bg-surface p-3">
                  <div className="text-micro uppercase tracking-wide text-slate">Organization</div>
                  <MonoId id={result.organization_id} className="mt-1" />
                </div>
              </div>

              {/* One-time admin password — reveal once, copy now */}
              <div className="mt-4 rounded-md border border-warning/50 bg-warning-light p-4">
                <div className="flex items-start gap-2">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-body font-medium text-navy">
                      One-time admin password — copy it NOW
                    </p>
                    <p className="mt-1 text-caption text-slate">
                      This is the only time the operator API will ever show this password — only a
                      hash is stored server-side. Copy it and hand it to{' '}
                      <span className="font-medium">{handoffEmail}</span> over a secure channel; it
                      leaves this screen forever when you navigate away.
                    </p>
                    <div className="mt-3">
                      {result.admin_one_time_password === null ? (
                        <p className="text-caption text-slate">
                          The API returned no one-time password for this run.
                        </p>
                      ) : otpRevealed ? (
                        <div className="flex items-center gap-2 rounded border border-border bg-surface-base px-3 py-2">
                          <code className="min-w-0 flex-1 break-all font-mono text-body text-ink">
                            {result.admin_one_time_password}
                          </code>
                          <CopyButton
                            value={result.admin_one_time_password}
                            label="Copy one-time password"
                          />
                        </div>
                      ) : (
                        <Button
                          variant="secondary"
                          size="sm"
                          icon={<Eye size={13} aria-hidden />}
                          onClick={() => setOtpRevealed(true)}
                        >
                          Reveal one-time password
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* The two OIDC redirect URIs bank IT must register at their
                  IdP — registering only the first is the documented foot-gun
                  (sign-in works, signing step-up fails). */}
              {result.sso_redirect_uris.length > 0 && (
                <div className="mt-4 rounded-md border border-border-light bg-surface p-3">
                  <div className="text-micro uppercase tracking-wide text-slate">
                    OIDC redirect URIs for the bank&apos;s IdP — register ALL of them
                  </div>
                  <ul className="mt-2 space-y-1">
                    {result.sso_redirect_uris.map((uri) => (
                      <li key={uri} className="flex items-center gap-1">
                        <code className="min-w-0 flex-1 break-all font-mono text-caption text-ink">
                          {uri}
                        </code>
                        <CopyButton value={uri} label={`Copy ${uri}`} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="mt-4 border-t border-border-light pt-4">
                <h3 className="text-body font-medium text-navy">Next steps</h3>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-caption text-slate">
                  <li>
                    Hand the admin their credentials. On first sign-in they set a real password.
                  </li>
                  <li>
                    The bank&apos;s IT completes SSO in the product under Settings → Authentication
                    (issuer, client id/secret, allowed domains — and the redirect URIs above
                    registered at their IdP; sign-in AND signing step-up).
                  </li>
                  <li>
                    The bank goes live on its first ingestion — there is no seeded data; the Data
                    Engine (upload, adapter, or API push) is the only way data enters.
                  </li>
                </ul>
              </div>

              <div className="mt-4 flex gap-2">
                <Link
                  href={`/tenants/${result.organization_id}`}
                  className="btn-primary inline-flex items-center justify-center rounded-md px-4 py-2 text-body font-medium text-white"
                >
                  Open tenant
                </Link>
                <Button variant="secondary" onClick={resetAll}>
                  Onboard another
                </Button>
              </div>
            </div>
          ) : (
            <div className="card border-critical/40 p-5">
              <h2 className="text-h3 text-navy">Provisioning failed</h2>
              {failedStep ? (
                <p className="mt-2 text-body text-slate">
                  Failed at <span className="font-mono text-ink">{failedStep.step}</span>
                  {failedStep.detail ? <> — {failedStep.detail}</> : null}
                </p>
              ) : (
                <p className="mt-2 text-body text-slate">
                  The API reported the saga as not succeeded. The step record above is the
                  authoritative account of what ran.
                </p>
              )}
              {(result.organization_id || result.bank_id) && (
                <p className="mt-2 text-body text-slate">
                  Identifiers the API still returned:{' '}
                  {result.organization_id && (
                    <span className="font-mono text-ink">{result.organization_id}</span>
                  )}
                  {result.organization_id && result.bank_id && ' · '}
                  {result.bank_id && <span className="font-mono text-ink">{result.bank_id}</span>}
                  {' — these rows may still exist.'}
                </p>
              )}
              <p className="mt-2 text-caption text-slate">
                Steps marked <span className="font-medium text-warning">rolled back</span> above were
                undone by the saga; anything marked{' '}
                <span className="font-medium text-success">succeeded</span> without a rollback may
                still exist — check the tenant list before re-running.
              </p>
              <div className="mt-4 flex gap-2">
                <Button onClick={() => setPhase('review')}>Back to review</Button>
                <Link
                  href="/tenants"
                  className="inline-flex items-center justify-center rounded-md border border-border px-4 py-2 text-body font-medium text-ink hover:bg-surface"
                >
                  Check tenant list
                </Link>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
