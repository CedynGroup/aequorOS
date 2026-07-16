'use client';

/**
 * "Connect a market data source" — the §9.2 onboarding flow as a stepper:
 * choose vendor, provide credentials (validated on submission), select data
 * scopes grouped by category with live quota impact from the scopes catalog,
 * review the pull schedule defaults, then create + test + activate.
 *
 * Credential inputs are write-only password fields; after creation the only
 * stored representation shown is the fingerprint on the source card.
 */

import { useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Loader2,
  Plug,
  XCircle,
} from 'lucide-react';
import type {
  MarketDataConnectionRead,
  ScopeInfoRead,
  TestPullRead,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import {
  useCreateMarketDataConnection,
  useMarketDataScopes,
  useTestMarketDataConnection,
} from '@/lib/api/hooks';
import CredentialFields from './CredentialFields';
import {
  CATEGORY_LABELS,
  ConnectionStatusPill,
  FREQUENCY_OPTIONS,
  VENDORS,
  scopeShortLabel,
  vendorName,
  type VendorKey,
} from './shared';

// Mirrors the backend quota tracker's pulls-per-month model (§11.1).
const PULLS_PER_MONTH: Record<string, number> = {
  ON_DEMAND: 1,
  HOURLY: 8 * 22,
  END_OF_DAY: 22,
  WEEKLY: 4,
  MONTHLY: 1,
};

type Step = 'vendor' | 'credentials' | 'scopes' | 'schedule' | 'activate';

function stepsFor(vendor: VendorKey | null): Step[] {
  return vendor === 'manual_upload'
    ? ['vendor', 'scopes', 'activate']
    : ['vendor', 'credentials', 'scopes', 'schedule', 'activate'];
}

export default function AddSourcePanel({
  bankId,
  existingVendors,
  onDone,
}: {
  bankId: string;
  existingVendors: string[];
  onDone: () => void;
}) {
  const scopesQuery = useMarketDataScopes(bankId);
  const create = useCreateMarketDataConnection(bankId);
  const test = useTestMarketDataConnection(bankId);

  const [vendor, setVendor] = useState<VendorKey | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [credValues, setCredValues] = useState<Record<string, string>>({});
  const [expiresAt, setExpiresAt] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [schedule, setSchedule] = useState<Record<string, string>>({});
  const [stepIndex, setStepIndex] = useState(0);
  const [created, setCreated] = useState<MarketDataConnectionRead | null>(null);
  const [testResult, setTestResult] = useState<TestPullRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const steps = stepsFor(vendor);
  const step = steps[Math.min(stepIndex, steps.length - 1)];

  const vendorScopes = useMemo(() => {
    if (!vendor || !scopesQuery.data) return [];
    return scopesQuery.data.scopes.filter((scope) =>
      scope.supportedBy.includes(vendor)
    );
  }, [vendor, scopesQuery.data]);

  const byCategory = useMemo(() => {
    const groups = new Map<string, ScopeInfoRead[]>();
    for (const scope of vendorScopes) {
      const group = groups.get(scope.category) ?? [];
      group.push(scope);
      groups.set(scope.category, group);
    }
    return groups;
  }, [vendorScopes]);

  const selectedInfos = vendorScopes.filter((scope) => selected.has(scope.scope));
  const unitsPerPull = selectedInfos.reduce((sum, scope) => sum + scope.quotaUnits, 0);
  const monthlyUnits = selectedInfos.reduce((sum, scope) => {
    const frequency = schedule[scope.category] ?? scope.defaultFrequency;
    return sum + scope.quotaUnits * (PULLS_PER_MONTH[frequency] ?? 1);
  }, 0);
  const selectedCategories = [...new Set(selectedInfos.map((scope) => scope.category))];

  const chooseVendor = (key: VendorKey) => {
    setVendor(key);
    setSelected(new Set());
    setSchedule({});
    setCredValues({});
    if (!displayName || VENDORS.some((option) => option.name === displayName)) {
      setDisplayName(vendorName(key));
    }
  };

  const toggleScope = (scope: ScopeInfoRead) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(scope.scope)) next.delete(scope.scope);
      else next.add(scope.scope);
      return next;
    });
  };

  const createAndTest = async () => {
    if (!vendor) return;
    setRunning(true);
    setError(null);
    try {
      const connection = await create.mutateAsync({
        vendor,
        displayName: displayName.trim() || vendorName(vendor),
        credentials: vendor === 'manual_upload' ? undefined : credValues,
        credentialExpiresAt:
          vendor !== 'manual_upload' && expiresAt
            ? new Date(`${expiresAt}T00:00:00Z`)
            : undefined,
        scopes: [...selected].sort(),
        schedule: Object.keys(schedule).length ? schedule : undefined,
      });
      setCreated(connection);
      if (vendor !== 'manual_upload' && !connection.validationError) {
        setTestResult(await test.mutateAsync(connection.id));
      }
    } catch (caught) {
      setError(
        isApiError(caught)
          ? caught.message
          : caught instanceof Error
            ? caught.message
            : 'Could not create the connection.'
      );
    } finally {
      setRunning(false);
    }
  };

  const credentialsIncomplete =
    vendor !== null &&
    vendor !== 'manual_upload' &&
    Object.values(credValues).every((value) => !value.trim());

  return (
    <section className="card p-5 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-h2 text-navy">Connect a market data source</h2>
          <p className="mt-1 text-body text-slate">
            The flow is identical for every vendor: credentials, scopes, schedule, test,
            activate. Credentials are encrypted at rest and never displayed again.
          </p>
        </div>
        <button
          type="button"
          onClick={onDone}
          className="shrink-0 text-caption font-medium text-slate hover:text-navy"
        >
          Close
        </button>
      </div>

      <ol className="flex flex-wrap gap-2 text-caption">
        {steps.map((name, index) => (
          <li
            key={name}
            className={`px-2.5 py-1 rounded border font-medium uppercase tracking-wider ${
              index === stepIndex
                ? 'border-action text-action bg-action-light'
                : index < stepIndex
                  ? 'border-success/30 text-success bg-success-light'
                  : 'border-border text-slate'
            }`}
          >
            {index + 1}. {name}
          </li>
        ))}
      </ol>

      {step === 'vendor' && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-3">
            {VENDORS.map((option) => {
              const taken =
                existingVendors.includes(option.key) &&
                option.key !== vendor;
              const active = vendor === option.key;
              return (
                <button
                  key={option.key}
                  type="button"
                  disabled={taken}
                  onClick={() => chooseVendor(option.key)}
                  className={`text-left rounded border p-4 space-y-1 disabled:opacity-40 disabled:cursor-not-allowed ${
                    active
                      ? 'border-action bg-action-light/60'
                      : 'border-border hover:border-action/50'
                  }`}
                >
                  <p className="text-h3 text-navy">{option.name}</p>
                  <p className="text-body text-slate">{option.description}</p>
                  {taken && (
                    <p className="text-caption text-slate">
                      Already connected for this bank.
                    </p>
                  )}
                </button>
              );
            })}
          </div>
          <div className="max-w-sm">
            <label
              htmlFor="md-display-name"
              className="block text-caption font-medium text-slate mb-1"
            >
              Display name
            </label>
            <input
              id="md-display-name"
              type="text"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              className="w-full px-3 py-1.5 rounded border border-border text-body text-navy"
            />
          </div>
        </div>
      )}

      {step === 'credentials' && vendor && vendor !== 'manual_upload' && (
        <div className="space-y-4">
          <p className="text-body text-slate">
            {vendor === 'refinitiv'
              ? 'Create an OAuth application in your Refinitiv Data Platform account, then enter its credentials. If you do not have them, contact your Refinitiv account administrator.'
              : 'Contact your Bloomberg administrator to authorize AequorOS access to your subscription and provision an application identifier.'}
          </p>
          <CredentialFields
            vendor={vendor}
            values={credValues}
            onChange={(key, value) =>
              setCredValues((current) => ({ ...current, [key]: value }))
            }
            idPrefix="add-source"
          />
          <div className="max-w-sm">
            <label
              htmlFor="md-expires-at"
              className="block text-caption font-medium text-slate mb-1"
            >
              Credential expiry (optional)
            </label>
            <input
              id="md-expires-at"
              type="date"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
              className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
            />
            <p className="mt-1 text-caption text-slate">
              AequorOS warns 30 days before expiry and guides you through rotation.
            </p>
          </div>
        </div>
      )}

      {step === 'scopes' && (
        <div className="space-y-4">
          <p className="text-body text-slate">
            Which market data should AequorOS pull through this source?
          </p>
          {scopesQuery.isPending && (
            <p className="text-body text-slate">Loading the scope catalog…</p>
          )}
          {[...byCategory.entries()].map(([category, scopes]) => (
            <fieldset key={category} className="rounded border border-border p-4">
              <legend className="px-1 text-caption font-medium uppercase tracking-wider text-slate">
                {CATEGORY_LABELS[category] ?? category}
              </legend>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {scopes.map((scope) => (
                  <label
                    key={scope.scope}
                    className="flex items-center gap-2 text-body text-navy"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(scope.scope)}
                      onChange={() => toggleScope(scope)}
                      className="rounded border-border"
                    />
                    <span>{scopeShortLabel(scope.scope, scope.category)}</span>
                    <span className="ml-auto text-caption font-mono text-slate">
                      {scope.quotaUnits} u
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
          <p className="text-body text-navy">
            Selected scopes will consume approximately{' '}
            <span className="font-mono font-medium">{unitsPerPull}</span> units per pull,
            roughly <span className="font-mono font-medium">{monthlyUnits.toLocaleString('en-GH')}</span>{' '}
            units per month against your subscription.
          </p>
        </div>
      )}

      {step === 'schedule' && (
        <div className="space-y-4">
          <p className="text-body text-slate">
            When should AequorOS refresh this data? Defaults follow each category's
            standard cadence; adjust per category as needed.
          </p>
          {selectedCategories.length === 0 && (
            <p className="text-body text-slate">No scopes selected yet.</p>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            {selectedCategories.map((category) => {
              const defaultFrequency =
                vendorScopes.find((scope) => scope.category === category)
                  ?.defaultFrequency ?? 'END_OF_DAY';
              return (
                <div key={category}>
                  <label
                    htmlFor={`md-schedule-${category}`}
                    className="block text-caption font-medium text-slate mb-1"
                  >
                    {CATEGORY_LABELS[category] ?? category}
                  </label>
                  <select
                    id={`md-schedule-${category}`}
                    value={schedule[category] ?? defaultFrequency}
                    onChange={(event) =>
                      setSchedule((current) => ({
                        ...current,
                        [category]: event.target.value,
                      }))
                    }
                    className="w-full px-3 py-1.5 rounded border border-border text-body text-navy bg-surface-raised"
                  >
                    {FREQUENCY_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {step === 'activate' && (
        <div className="space-y-4">
          {!created && (
            <>
              <p className="text-body text-slate">
                AequorOS will store the connection, validate the credentials against{' '}
                {vendor ? vendorName(vendor) : 'the vendor'}, and run a small
                representative test pull so you can eyeball the values before relying on
                them.
              </p>
              <button
                type="button"
                onClick={() => void createAndTest()}
                disabled={running || !vendor || selected.size === 0}
                className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {running ? (
                  <Loader2 size={15} className="animate-spin" aria-hidden />
                ) : (
                  <Plug size={15} aria-hidden />
                )}
                {vendor === 'manual_upload'
                  ? 'Create manual upload source'
                  : 'Create, validate & test connection'}
              </button>
            </>
          )}

          {created && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <ConnectionStatusPill status={created.status} />
                <p className="text-body text-navy">
                  {created.status === 'TESTING'
                    ? 'Connection stored, but credential validation failed — fix the credentials from the source card (Rotate credentials).'
                    : `${vendorName(created.vendor)} connection active.`}
                </p>
              </div>
              {created.validationError && (
                <div className="rounded border border-warning/30 bg-warning-light/50 px-4 py-3">
                  <p className="text-body text-navy">{created.validationError}</p>
                </div>
              )}
              {testResult && (
                <div
                  className={`rounded border px-4 py-3 space-y-2 ${
                    testResult.success
                      ? 'border-success/30 bg-success-light/50'
                      : 'border-critical/30 bg-critical-light/40'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {testResult.success ? (
                      <CheckCircle2 size={15} className="text-success" aria-hidden />
                    ) : (
                      <XCircle size={15} className="text-critical" aria-hidden />
                    )}
                    <p className="text-body font-medium text-navy">
                      {testResult.success
                        ? "Test successful. Here's a sample of what we pulled:"
                        : 'Test pull failed'}
                    </p>
                  </div>
                  {testResult.success ? (
                    <ul className="space-y-1">
                      {Object.entries(testResult.sampleValues).map(([label, value]) => (
                        <li key={label} className="text-body text-navy font-mono">
                          {label}: {value}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-body text-navy">{testResult.error}</p>
                  )}
                </div>
              )}
              <button
                type="button"
                onClick={onDone}
                className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover"
              >
                Done
              </button>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
          <p className="text-body text-critical">{error}</p>
        </div>
      )}

      {!created && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => setStepIndex((index) => Math.max(0, index - 1))}
            disabled={stepIndex === 0 || running}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-border text-caption font-medium text-navy hover:bg-surface disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <ArrowLeft size={13} aria-hidden />
            Back
          </button>
          {step !== 'activate' && (
            <button
              type="button"
              onClick={() => setStepIndex((index) => index + 1)}
              disabled={
                running ||
                (step === 'vendor' && !vendor) ||
                (step === 'credentials' && credentialsIncomplete) ||
                (step === 'scopes' && selected.size === 0)
              }
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-caption font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Continue
              <ArrowRight size={13} aria-hidden />
            </button>
          )}
        </div>
      )}
    </section>
  );
}
