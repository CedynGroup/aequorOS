'use client';

/**
 * API Push integration guide: connection details, the three-call flow with
 * copyable curl examples, and the per-entity record schemas — rendered from
 * the typed content in api-reference.ts (authored against
 * docs/API_INTEGRATION.md, the authoritative contract).
 */

import { useState } from 'react';
import { Check, Copy, ShieldAlert, Webhook } from 'lucide-react';
import {
  ENTITY_SPECS,
  PUSH_DOC_PATH,
  PUSH_FLOW_STEPS,
  REFERENCE_KINDS,
  VALUE_CONVENTIONS,
  type EntitySpec,
} from './api-reference';

const BASE_URL =
  (process.env.NEXT_PUBLIC_RISK_API_BASE_URL ?? 'http://127.0.0.1:8003/api/v1').replace(
    /\/api\/v1\/?$/,
    '',
  );

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={`Copy ${label}`}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="inline-flex items-center gap-1 rounded border border-white/20 px-2 py-1 text-micro font-medium text-white/70 hover:text-white hover:border-white/40 transition-colors"
    >
      {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

function CurlBlock({ curl, label }: { curl: string; label: string }) {
  return (
    <div className="relative rounded bg-navy text-white">
      <div className="absolute right-2 top-2">
        <CopyButton text={curl} label={label} />
      </div>
      <pre className="overflow-x-auto px-4 py-3 pr-20 text-caption font-mono leading-relaxed">
        {curl}
      </pre>
    </div>
  );
}

export function ConnectionCard() {
  return (
    <section className="card p-5 border-l-4 border-l-success">
      <div className="flex items-center gap-2">
        <Webhook size={18} className="text-success" aria-hidden />
        <h2 className="text-h3 text-navy">Connection</h2>
      </div>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <p className="text-micro uppercase tracking-wider text-slate">Base URL</p>
          <p className="mt-1 text-body font-mono text-navy">{BASE_URL}/api/v1</p>
          <p className="mt-3 text-micro uppercase tracking-wider text-slate">
            Authentication (MVP)
          </p>
          <p className="mt-1 text-body text-slate leading-relaxed">
            Tenant headers on every request:{' '}
            <code className="font-mono text-navy">X-Org-Id</code> (organization UUID) and{' '}
            <code className="font-mono text-navy">X-User-Id</code> (the service-account
            user acting for your middleware).
          </p>
        </div>
        <div className="rounded border border-warning/30 bg-warning-light/40 p-4">
          <p className="inline-flex items-center gap-1.5 text-caption font-medium text-warning">
            <ShieldAlert size={13} aria-hidden /> Production note
          </p>
          <p className="mt-1 text-body text-navy/80 leading-relaxed">
            The headers identify the tenant inside a trusted perimeter. Production
            deployments put OAuth2 client-credentials or mTLS in front of these
            endpoints; the resource design does not change. Do not build against the
            headers as a security mechanism.
          </p>
        </div>
      </div>
      <p className="mt-4 pt-3 border-t border-border-light text-caption text-slate">
        Full public contract, idempotency semantics, and a runnable example client:{' '}
        <code className="font-mono text-navy">{PUSH_DOC_PATH}</code> (example:{' '}
        <code className="font-mono text-navy">backend/scripts/push_api_example.py</code>).
      </p>
    </section>
  );
}

export function PushFlowSteps() {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-h2 text-navy">The three-call flow</h2>
        <p className="mt-1 text-body text-slate">
          Open a push batch, stage record pages, commit. Commit runs the exact same
          ingestion pipeline as a file upload — validation gating, lineage, immutable
          artifacts — so batch history and module activation behave identically.
        </p>
      </div>
      <ol className="space-y-4">
        {PUSH_FLOW_STEPS.map((step) => (
          <li key={step.step} className="card p-5">
            <div className="flex flex-wrap items-center gap-3">
              <span className="shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full bg-navy text-white text-caption font-mono">
                {step.step}
              </span>
              <h3 className="text-h3 text-navy">{step.title}</h3>
              <span className="font-mono text-caption text-slate">
                <span className="font-medium text-action">{step.method}</span> {step.path}
              </span>
            </div>
            <p className="mt-2 text-body text-slate leading-relaxed">{step.summary}</p>
            <div className="mt-3">
              <CurlBlock curl={step.curl} label={`step ${step.step} curl example`} />
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function EntityTable({ entity }: { entity: EntitySpec }) {
  return (
    <div className="card p-5">
      <h3 className="text-h3 font-mono text-navy">{entity.title}</h3>
      {entity.note && <p className="mt-1 text-caption text-slate">{entity.note}</p>}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-caption">
          <thead>
            <tr className="text-left text-micro uppercase tracking-wider text-slate border-b border-border">
              <th className="py-1.5 pr-4 font-medium">Field</th>
              <th className="py-1.5 pr-4 font-medium">Type</th>
              <th className="py-1.5 pr-4 font-medium">Required</th>
              <th className="py-1.5 font-medium">Description</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-light">
            {entity.fields.map((field) => (
              <tr key={field.name}>
                <td className="py-1.5 pr-4 font-mono text-navy whitespace-nowrap">
                  {field.name}
                </td>
                <td className="py-1.5 pr-4 font-mono text-slate">{field.type}</td>
                <td className="py-1.5 pr-4">
                  {field.required ? (
                    <span className="font-medium text-navy">yes</span>
                  ) : (
                    <span className="text-slate">no</span>
                  )}
                </td>
                <td className="py-1.5 text-navy/80 leading-relaxed">{field.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EntitySchemas() {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-h2 text-navy">Record schemas (identity mapping)</h2>
        <p className="mt-1 text-body text-slate leading-relaxed">
          By default, field names ARE the canonical field names below — zero mapping
          configuration for a conformant client (an identity mapping is
          auto-provisioned on first commit). If your middleware cannot rename its
          fields, activate an <code className="font-mono">API_PUSH</code> mapping config
          — see §4 of the contract.
        </p>
      </div>

      <div className="card p-5">
        <h3 className="text-h3 text-navy">Value conventions (strict)</h3>
        <dl className="mt-3 space-y-2">
          {VALUE_CONVENTIONS.map((convention) => (
            <div key={convention.rule} className="flex gap-3">
              <dt className="shrink-0 w-32 text-caption font-medium text-navy">
                {convention.rule}
              </dt>
              <dd className="text-caption text-navy/80 leading-relaxed">
                {convention.detail}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {ENTITY_SPECS.map((entity) => (
        <EntityTable key={entity.key} entity={entity} />
      ))}

      <div className="card p-5">
        <h3 className="text-h3 text-navy">Reference datasets</h3>
        <p className="mt-1 text-caption text-slate leading-relaxed">
          Reference rows have no fixed schema: each row is preserved verbatim as a
          payload under its dataset kind and consumed as-is by the calculation modules.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-caption">
            <thead>
              <tr className="text-left text-micro uppercase tracking-wider text-slate border-b border-border">
                <th className="py-1.5 pr-4 font-medium">Key</th>
                <th className="py-1.5 font-medium">Typical row fields (Sample Bank dataset)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-light">
              {REFERENCE_KINDS.map((kind) => (
                <tr key={kind.key}>
                  <td className="py-1.5 pr-4 font-mono text-navy whitespace-nowrap">
                    {kind.key}
                  </td>
                  <td className="py-1.5 font-mono text-slate">{kind.typicalFields}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
