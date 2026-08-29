"use client";

/**
 * API Push integration guide: connection details, integration-key management
 * (generate-once, revocable service-account credentials), the three-call flow
 * with copyable curl examples, and the per-entity record schemas — rendered
 * from the typed content in api-reference.ts (authored against
 * docs/API_INTEGRATION.md, the authoritative contract).
 */

import { useState } from "react";
import { useSession } from "next-auth/react";
import { Download, KeyRound, ShieldAlert, Webhook } from "lucide-react";
import CopyButton from "@/components/ui/CopyButton";
import StatusPill from "@/components/ui/StatusPill";
import { apiOrigin } from "@/lib/api/client";
import {
  useIntegrationKeys,
  useIssueIntegrationKey,
  useRevokeIntegrationKey,
} from "@/lib/api/hooks";
import { fmtRelative } from "@/lib/api/values";
import { hasAccountAdministrationRole } from "@/lib/api/accountAdministration";
import { downloadTextFile } from "@/lib/download";
import {
  ENTITY_SPECS,
  EXAMPLE_SCRIPT,
  PUSH_FLOW_STEPS,
  REFERENCE_KINDS,
  VALUE_CONVENTIONS,
  type EntitySpec,
} from "./api-reference";

function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="relative rounded bg-nav text-white">
      <div className="absolute right-2 top-2">
        <CopyButton text={code} label={label} variant="dark" />
      </div>
      <pre className="overflow-x-auto px-4 py-3 pr-20 text-caption font-mono leading-relaxed">
        {code}
      </pre>
    </div>
  );
}

/** A copyable connection value (base URL, tenant headers). */
function ConnectionField({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <p className="text-micro uppercase tracking-wider text-slate">{label}</p>
      <div className="mt-1 flex items-center gap-2">
        <code className="text-body font-mono text-navy break-all">{value}</code>
        <CopyButton text={value} label={label} className="shrink-0" />
      </div>
      {hint && (
        <p className="mt-0.5 text-caption text-slate leading-relaxed">{hint}</p>
      )}
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
        <div className="space-y-3">
          <ConnectionField label="Base URL" value={`${apiOrigin}/api/v1`} />
          <ConnectionField
            label="Authorization"
            value="Bearer aeq_live_…"
            hint="Your integration key, sent as the bearer credential on every request — generate one below."
          />
        </div>
        <div className="rounded border border-warning/30 bg-warning-light/40 p-4">
          <p className="inline-flex items-center gap-1.5 text-caption font-medium text-warning">
            <ShieldAlert size={13} aria-hidden /> Key handling
          </p>
          <p className="mt-1 text-body text-navy/80 leading-relaxed">
            The key identifies + authorizes your institution as a service
            account (verified server-side, analyst rights — data push only). It
            is shown once at generation and stored only as a hash. If a key is
            exposed, revoke it here immediately; rotate by generating a new key
            before revoking the old one.
          </p>
        </div>
      </div>
      <IntegrationKeysPanel />
    </section>
  );
}

function IntegrationKeysPanel() {
  const { data: session } = useSession();
  const isAdmin = hasAccountAdministrationRole(session?.user?.roles ?? []);
  const keysQuery = useIntegrationKeys(isAdmin);
  const issue = useIssueIntegrationKey();
  const revoke = useRevokeIntegrationKey();
  const [label, setLabel] = useState("");
  // The one moment the raw key is visible — held in memory only, never listed.
  const [freshKey, setFreshKey] = useState<string | null>(null);

  if (!isAdmin) {
    return (
      <p className="mt-4 text-caption text-slate">
        Integration keys are managed by an administrator.
      </p>
    );
  }
  const keys = keysQuery.data?.keys ?? [];

  return (
    <div className="mt-5 border-t border-border-light pt-4">
      <div className="flex items-center gap-2">
        <KeyRound size={15} className="text-action" aria-hidden />
        <h3 className="text-h3 text-navy">Integration keys</h3>
      </div>

      {freshKey && (
        <div className="mt-3 rounded border border-success/40 bg-success-light/40 p-4">
          <p className="text-caption font-medium text-navy">
            Key generated — copy it now. It will not be shown again.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="text-body font-mono text-navy break-all">
              {freshKey}
            </code>
            <CopyButton
              text={freshKey}
              label="Integration key"
              className="shrink-0"
            />
          </div>
          <button
            type="button"
            className="mt-2 text-caption font-medium text-action hover:text-action-hover"
            onClick={() => setFreshKey(null)}
          >
            I have stored it securely — dismiss
          </button>
        </div>
      )}

      <form
        className="mt-3 flex items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!label.trim() || issue.isPending) return;
          issue.mutate(label.trim(), {
            onSuccess: (result) => {
              setFreshKey(result.key);
              setLabel("");
            },
          });
        }}
      >
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Key label — e.g. Core banking nightly push"
          maxLength={80}
          className="flex-1 rounded border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy placeholder:text-slate-light"
          aria-label="Key label"
        />
        <button
          type="submit"
          disabled={!label.trim() || issue.isPending}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60 shrink-0"
        >
          {issue.isPending ? "Generating…" : "Generate key"}
        </button>
      </form>
      {issue.isError && (
        <p className="mt-1 text-caption text-critical">
          {issue.error instanceof Error
            ? issue.error.message
            : "Key generation failed."}
        </p>
      )}

      {keys.length > 0 && (
        <ul className="mt-4 divide-y divide-line">
          {keys.map((key) => (
            <li key={key.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="text-body text-navy truncate">{key.label}</p>
                <p className="text-caption text-slate font-mono">
                  {key.keyPrefix}
                  <span className="ml-2 font-sans">
                    created {fmtRelative(key.createdAt)}
                    {key.lastUsedAt
                      ? ` · last used ${fmtRelative(key.lastUsedAt)}`
                      : " · never used"}
                  </span>
                </p>
              </div>
              {key.revokedAt ? (
                <StatusPill tone="slate">Revoked</StatusPill>
              ) : (
                <>
                  <StatusPill tone="success">Active</StatusPill>
                  <button
                    type="button"
                    className="text-caption font-medium text-critical hover:underline"
                    disabled={revoke.isPending}
                    onClick={() => {
                      const reason = window.prompt(
                        `Revoke "${key.label}"? Middleware using it stops authenticating immediately. Reason:`,
                      );
                      if (reason?.trim()) {
                        revoke.mutate({ keyId: key.id, reason: reason.trim() });
                      }
                    }}
                  >
                    Revoke
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PushFlowSteps() {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-h2 text-navy">The three-call flow</h2>
        <p className="mt-1 text-body text-slate">
          Open a push batch, stage record pages, commit. Commit runs the exact
          same ingestion pipeline as a file upload — validation gating, lineage,
          immutable artifacts — so batch history and module activation behave
          identically.
        </p>
      </div>
      <ol className="space-y-4">
        {PUSH_FLOW_STEPS.map((step) => (
          <li key={step.step} className="card p-5">
            <div className="flex flex-wrap items-center gap-3">
              <span className="shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full bg-nav text-white text-caption font-mono">
                {step.step}
              </span>
              <h3 className="text-h3 text-navy">{step.title}</h3>
              <span className="font-mono text-caption text-slate">
                <span className="font-medium text-action">{step.method}</span>{" "}
                {step.path}
              </span>
            </div>
            <p className="mt-2 text-body text-slate leading-relaxed">
              {step.summary}
            </p>
            <div className="mt-3">
              <CodeBlock
                code={step.curl}
                label={`step ${step.step} curl example`}
              />
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ExampleClient() {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-h2 text-navy">Runnable example</h2>
          <p className="mt-1 text-body text-slate leading-relaxed">
            A self-contained script that opens a batch, stages a page, commits,
            and prints the validation summary. Set the three variables and run
            it.
          </p>
        </div>
        <button
          type="button"
          onClick={() =>
            downloadTextFile(
              "aequoros_push_example.sh",
              EXAMPLE_SCRIPT,
              "text/x-shellscript;charset=utf-8",
            )
          }
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded text-caption font-medium border border-border text-slate hover:text-navy hover:border-slate"
        >
          <Download size={13} aria-hidden /> Download .sh
        </button>
      </div>
      <CodeBlock code={EXAMPLE_SCRIPT} label="runnable push example" />
    </section>
  );
}

function EntityTable({ entity }: { entity: EntitySpec }) {
  return (
    <div className="card p-5">
      <h3 className="text-h3 font-mono text-navy">{entity.title}</h3>
      {entity.note && (
        <p className="mt-1 text-caption text-slate">{entity.note}</p>
      )}
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
                <td className="py-1.5 pr-4 font-mono text-slate">
                  {field.type}
                </td>
                <td className="py-1.5 pr-4">
                  {field.required ? (
                    <span className="font-medium text-navy">yes</span>
                  ) : (
                    <span className="text-slate">no</span>
                  )}
                </td>
                <td className="py-1.5 text-navy/80 leading-relaxed">
                  {field.description}
                </td>
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
          By default, field names ARE the canonical field names below — zero
          mapping configuration for a conformant client (an identity mapping is
          auto-provisioned on first commit). If your middleware cannot rename
          its fields, activate an <code className="font-mono">API_PUSH</code>{" "}
          mapping config — see §4 of the contract.
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
          Reference rows have no fixed schema: each row is preserved verbatim as
          a payload under its dataset kind and consumed as-is by the calculation
          modules.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-caption">
            <thead>
              <tr className="text-left text-micro uppercase tracking-wider text-slate border-b border-border">
                <th className="py-1.5 pr-4 font-medium">Key</th>
                <th className="py-1.5 font-medium">
                  Typical row fields (Sample Bank dataset)
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-light">
              {REFERENCE_KINDS.map((kind) => (
                <tr key={kind.key}>
                  <td className="py-1.5 pr-4 font-mono text-navy whitespace-nowrap">
                    {kind.key}
                  </td>
                  <td className="py-1.5 font-mono text-slate">
                    {kind.typicalFields}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
