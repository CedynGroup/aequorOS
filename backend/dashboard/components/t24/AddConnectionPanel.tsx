'use client';

/**
 * "Connect a Temenos core" — the onboarding flow as a stepper: choose the
 * transport mode, provide the endpoint + company context, enter the service
 * credentials (validated on submission), pick the domains to pull, then create
 * + test + activate. Credentials are write-only password fields; after creation
 * the only stored representation shown is the fingerprint on the connection card.
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
  TemenosConnectionRead,
  TemenosDomainInfoRead,
  TemenosTestPullRead,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import {
  useCreateTemenosConnection,
  useTemenosDomains,
  useTestTemenosConnection,
} from '@/lib/api/hooks';
import CredentialFields from './CredentialFields';
import {
  CORE_SYSTEMS,
  ConnectionStatusPill,
  DOMAIN_CATEGORY_LABELS,
  MODES,
  type CoreSystemKey,
  type ModeKey,
  domainShortLabel,
  modeName,
} from './shared';

type Step = 'mode' | 'connection' | 'credentials' | 'domains' | 'activate';
const STEPS: Step[] = ['mode', 'connection', 'credentials', 'domains', 'activate'];

export default function AddConnectionPanel({
  bankId,
  existingNames,
  onDone,
}: {
  bankId: string;
  existingNames: string[];
  onDone: () => void;
}) {
  const [mode, setMode] = useState<ModeKey | null>(null);
  const [coreSystem, setCoreSystem] = useState<CoreSystemKey>('T24');
  const [displayName, setDisplayName] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [companies, setCompanies] = useState('');
  const [defaultCurrency, setDefaultCurrency] = useState('GHS');
  const [credValues, setCredValues] = useState<Record<string, string>>({});
  const [expiresAt, setExpiresAt] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [touchedDomains, setTouchedDomains] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [created, setCreated] = useState<TemenosConnectionRead | null>(null);
  const [testResult, setTestResult] = useState<TemenosTestPullRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const create = useCreateTemenosConnection(bankId);
  const test = useTestTemenosConnection(bankId);
  const domainsQuery = useTemenosDomains(bankId, mode ?? 'OFS');

  const supported = useMemo(
    () => (domainsQuery.data?.domains ?? []).filter((domain) => domain.supported),
    [domainsQuery.data]
  );
  const byCategory = useMemo(() => {
    const groups = new Map<string, TemenosDomainInfoRead[]>();
    for (const domain of supported) {
      const group = groups.get(domain.category) ?? [];
      group.push(domain);
      groups.set(domain.category, group);
    }
    return groups;
  }, [supported]);

  // Default: every supported domain enabled (matches the backend's "empty = all").
  const effectiveSelected = touchedDomains
    ? selected
    : new Set(supported.map((domain) => domain.domain));

  const step = STEPS[stepIndex];

  const chooseMode = (key: ModeKey) => {
    setMode(key);
    setCredValues({});
    setSelected(new Set());
    setTouchedDomains(false);
    if (!displayName || MODES.some((option) => option.name === displayName)) {
      setDisplayName(`Core ${modeName(key)}`);
    }
    if (!endpoint) {
      setEndpoint(MODES.find((option) => option.key === key)?.endpointHint ?? '');
    }
  };

  const toggleDomain = (domain: string) => {
    setTouchedDomains(true);
    setSelected(() => {
      const base = touchedDomains
        ? new Set(selected)
        : new Set(supported.map((item) => item.domain));
      if (base.has(domain)) base.delete(domain);
      else base.add(domain);
      return base;
    });
  };

  const createAndTest = async () => {
    if (!mode) return;
    setRunning(true);
    setError(null);
    try {
      const companyList = companies
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean);
      const allSelected = effectiveSelected.size === supported.length;
      const connection = await create.mutateAsync({
        connectionMode: mode,
        coreSystem,
        displayName: displayName.trim() || `Core ${modeName(mode)}`,
        endpoint: endpoint.trim(),
        companies: companyList,
        defaultCurrency: defaultCurrency.trim() || 'GHS',
        // Empty domains means "every supported domain" on the backend.
        domains: allSelected ? [] : [...effectiveSelected].sort(),
        credentials: credValues,
        // The generated client types this nullable date as an ISO string.
        credentialExpiresAt: expiresAt ? `${expiresAt}T00:00:00Z` : undefined,
      });
      setCreated(connection);
      if (!connection.validationError) {
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
    mode !== null && Object.values(credValues).every((value) => !value.trim());
  const nameTaken =
    displayName.trim().length > 0 && existingNames.includes(displayName.trim());

  return (
    <section className="card p-5 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-h2 text-navy">Connect a Temenos core</h2>
          <p className="mt-1 text-body text-slate">
            The same flow works for every transport: endpoint, credentials, domains,
            test, activate. Credentials are encrypted at rest and never displayed again.
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
        {STEPS.map((name, index) => (
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

      {step === 'mode' && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-3">
            {MODES.map((option) => {
              const active = mode === option.key;
              return (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => chooseMode(option.key)}
                  className={`text-left rounded border p-4 space-y-1 ${
                    active
                      ? 'border-action bg-action-light/60'
                      : 'border-border hover:border-action/50'
                  }`}
                >
                  <p className="text-h3 text-navy">{option.name}</p>
                  <p className="text-caption font-medium text-slate">{option.channel}</p>
                  <p className="text-body text-slate">{option.description}</p>
                </button>
              );
            })}
          </div>
          <div className="grid gap-4 sm:grid-cols-2 max-w-2xl">
            <div>
              <label
                htmlFor="t24-display-name"
                className="block text-caption font-medium text-slate mb-1"
              >
                Display name
              </label>
              <input
                id="t24-display-name"
                type="text"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                className="w-full px-3 py-1.5 rounded border border-border text-body text-navy"
              />
              {nameTaken && (
                <p className="mt-1 text-caption text-critical">
                  A connection with this name already exists for this bank.
                </p>
              )}
            </div>
            <div>
              <label
                htmlFor="t24-core-system"
                className="block text-caption font-medium text-slate mb-1"
              >
                Core system
              </label>
              <select
                id="t24-core-system"
                value={coreSystem}
                onChange={(event) => setCoreSystem(event.target.value as CoreSystemKey)}
                className="w-full px-3 py-1.5 rounded border border-border text-body text-navy bg-surface-raised"
              >
                {CORE_SYSTEMS.map((system) => (
                  <option key={system.key} value={system.key}>
                    {system.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}

      {step === 'connection' && (
        <div className="space-y-4 max-w-2xl">
          <div>
            <label
              htmlFor="t24-endpoint"
              className="block text-caption font-medium text-slate mb-1"
            >
              Endpoint
            </label>
            <input
              id="t24-endpoint"
              type="text"
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
              placeholder={MODES.find((option) => option.key === mode)?.endpointHint}
              className="w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
            />
            <p className="mt-1 text-caption text-slate">
              The core banking host AequorOS signs on to. Reachable only from within your
              network — AequorOS connects from the deployment, never the browser.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label
                htmlFor="t24-companies"
                className="block text-caption font-medium text-slate mb-1"
              >
                Companies / entities
              </label>
              <input
                id="t24-companies"
                type="text"
                value={companies}
                onChange={(event) => setCompanies(event.target.value)}
                placeholder="GH0010001, GH0010002"
                className="w-full px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
              />
              <p className="mt-1 text-caption text-slate">
                Comma-separated T24 company codes. The first is the default.
              </p>
            </div>
            <div>
              <label
                htmlFor="t24-currency"
                className="block text-caption font-medium text-slate mb-1"
              >
                Default currency
              </label>
              <input
                id="t24-currency"
                type="text"
                value={defaultCurrency}
                onChange={(event) => setDefaultCurrency(event.target.value.toUpperCase())}
                maxLength={3}
                className="w-28 px-3 py-1.5 rounded border border-border text-body text-navy font-mono uppercase"
              />
            </div>
          </div>
        </div>
      )}

      {step === 'credentials' && mode && (
        <div className="space-y-4">
          <p className="text-body text-slate">
            Enter the credentials for the AequorOS service user on your{' '}
            {modeName(mode)} channel. They are validated on submission and stored
            encrypted; only the fingerprint is shown afterwards.
          </p>
          <CredentialFields
            mode={mode}
            values={credValues}
            onChange={(key, value) =>
              setCredValues((current) => ({ ...current, [key]: value }))
            }
            idPrefix="add-t24"
          />
          <div className="max-w-sm">
            <label
              htmlFor="t24-expires-at"
              className="block text-caption font-medium text-slate mb-1"
            >
              Credential expiry (optional)
            </label>
            <input
              id="t24-expires-at"
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

      {step === 'domains' && (
        <div className="space-y-4">
          <p className="text-body text-slate">
            Which parts of the core book should AequorOS pull? Everything supported by the{' '}
            {mode ? modeName(mode) : ''} catalog is enabled by default.
          </p>
          {domainsQuery.isPending && (
            <p className="text-body text-slate">Loading the domain catalog…</p>
          )}
          {[...byCategory.entries()].map(([category, domains]) => (
            <fieldset key={category} className="rounded border border-border p-4">
              <legend className="px-1 text-caption font-medium uppercase tracking-wider text-slate">
                {DOMAIN_CATEGORY_LABELS[category] ?? category}
              </legend>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {domains.map((domain) => (
                  <label
                    key={domain.domain}
                    className="flex items-center gap-2 text-body text-navy"
                  >
                    <input
                      type="checkbox"
                      checked={effectiveSelected.has(domain.domain)}
                      onChange={() => toggleDomain(domain.domain)}
                      className="rounded border-border"
                    />
                    <span>{domainShortLabel(domain.domain, domain.category)}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
          <p className="text-body text-navy">
            <span className="font-mono font-medium">{effectiveSelected.size}</span> of{' '}
            <span className="font-mono font-medium">{supported.length}</span> supported
            domains enabled.
          </p>
        </div>
      )}

      {step === 'activate' && (
        <div className="space-y-4">
          {!created && (
            <>
              <p className="text-body text-slate">
                AequorOS will store the connection, validate the credentials, and confirm
                the pull plan. A live pull runs on the schedule once the core transport is
                enabled for this site.
              </p>
              <button
                type="button"
                onClick={() => void createAndTest()}
                disabled={running || !mode || !endpoint.trim() || credentialsIncomplete}
                className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {running ? (
                  <Loader2 size={15} className="animate-spin" aria-hidden />
                ) : (
                  <Plug size={15} aria-hidden />
                )}
                Create, validate &amp; test connection
              </button>
            </>
          )}

          {created && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <ConnectionStatusPill status={created.status} />
                <p className="text-body text-navy">
                  {created.status === 'TESTING'
                    ? 'Connection stored, but credential validation failed — fix it from the connection card (Rotate credentials).'
                    : `${modeName(created.connectionMode)} connection active.`}
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
                        ? 'Connection verified. Pull plan:'
                        : 'Verification failed'}
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
                (step === 'mode' && (!mode || nameTaken)) ||
                (step === 'connection' && !endpoint.trim()) ||
                (step === 'credentials' && credentialsIncomplete)
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
