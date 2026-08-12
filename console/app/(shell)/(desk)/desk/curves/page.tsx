'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowRight, Loader2, Plus, ShieldCheck, Waypoints } from 'lucide-react';
import {
  approveCurveDefinitionVersion,
  createCurveDefinition,
  listCurveDefinitions,
  proposeCurveDefinitionVersion,
  toApiError,
  type ApiError,
  type DeskCurveDefinition,
  type DeskCurveDefinitionFields,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
import {
  CeremonyBanner,
  CurveDefinitionStatusPill,
  DefinitionForm,
} from '@/components/curves';
import { Chip, EmptyState, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

/**
 * /desk/curves — governed forward-curve DEFINITIONS register (FC-4, spec §4.1).
 *
 * The Eikon "Curve 1/2/3" analogue as versioned, dual-controlled objects: each
 * curve code carries a version history (draft ▸ approved), and a definition is
 * the immutable recipe the construction workspace applies to a cob's quotes.
 * Creating, proposing, and approving are Track-2 events behind an amber
 * ceremony — deliberately heavier than a Track-1 run (spec §2.6).
 */

function currentApproved(versions: DeskCurveDefinition[]): DeskCurveDefinition | null {
  const approved = versions.filter((v) => v.status === 'approved');
  return approved.length > 0 ? approved[approved.length - 1] : null;
}

export default function CurveDefinitionsPage() {
  const { data, error, loading, reload } = useApi(() => listCurveDefinitions());

  const byCode = useMemo(() => {
    const map = new Map<string, DeskCurveDefinition[]>();
    for (const row of data?.definitions ?? []) {
      const bucket = map.get(row.curve_code) ?? [];
      bucket.push(row);
      map.set(row.curve_code, bucket);
    }
    return map;
  }, [data]);

  const stats = useMemo(() => {
    const codes = [...byCode.values()];
    return {
      codes: codes.length,
      approved: codes.filter((v) => currentApproved(v) !== null).length,
      drafts: (data?.definitions ?? []).filter((d) => d.status === 'draft').length,
    };
  }, [byCode, data]);

  // ---- create (Track 2) ---------------------------------------------------
  const [creating, setCreating] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<ApiError | null>(null);

  async function submitCreate(fields: DeskCurveDefinitionFields, curveCode: string) {
    setCreateBusy(true);
    setCreateError(null);
    try {
      await createCurveDefinition({ ...fields, curve_code: curveCode });
      setCreating(false);
      reload();
    } catch (err) {
      setCreateError(toApiError(err));
    }
    setCreateBusy(false);
  }

  // ---- propose (Track 2) --------------------------------------------------
  const [proposeFor, setProposeFor] = useState<string | null>(null);
  const [proposeBusy, setProposeBusy] = useState(false);
  const [proposeError, setProposeError] = useState<ApiError | null>(null);

  async function submitPropose(code: string, fields: DeskCurveDefinitionFields) {
    setProposeBusy(true);
    setProposeError(null);
    try {
      await proposeCurveDefinitionVersion(code, fields);
      setProposeFor(null);
      reload();
    } catch (err) {
      setProposeError(toApiError(err));
    }
    setProposeBusy(false);
  }

  // ---- approve (Track 2, dual control) ------------------------------------
  const [approveFor, setApproveFor] = useState<{ code: string; version: number } | null>(null);
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [approveBusy, setApproveBusy] = useState(false);
  const [approveError, setApproveError] = useState<ApiError | null>(null);

  const approveDualControl =
    approveError !== null &&
    (approveError.status === 409 || approveError.status === 422) &&
    /proposer/i.test(approveError.message);

  async function submitApprove() {
    if (approveFor === null) return;
    setApproveBusy(true);
    setApproveError(null);
    try {
      await approveCurveDefinitionVersion(approveFor.code, approveFor.version, {
        effective_from: effectiveFrom,
      });
      setApproveFor(null);
      reload();
    } catch (err) {
      setApproveError(toApiError(err));
    }
    setApproveBusy(false);
  }

  const inputClass =
    'rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink focus:border-focus focus:outline-none';

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <PageHeader
          title="Curve definitions"
          sub="Governed forward-curve recipes (the Eikon Curve 1/2/3 analogue) under Track-2 dual control. The construction workspace applies an APPROVED definition to a cob's quotes; changing a definition is a rare, effective-dated, second-line-approved event."
        />
        <button
          type="button"
          onClick={() => {
            setCreating((v) => !v);
            setCreateError(null);
          }}
          className="btn-primary inline-flex shrink-0 items-center gap-1.5 px-3 py-2 text-body font-medium"
        >
          <Plus size={15} /> New definition
        </button>
      </div>

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <div className="card p-3">
          <div className="text-micro uppercase tracking-wide text-slate">Curve codes</div>
          <div className="mt-1 font-mono text-h2 text-navy">{stats.codes}</div>
          <p className="text-caption text-slate">Governed definitions in the register</p>
        </div>
        <div className="card p-3">
          <div className="text-micro uppercase tracking-wide text-slate">With approved version</div>
          <div className="mt-1 font-mono text-h2 text-navy">{stats.approved}</div>
          <p className="text-caption text-slate">Constructable & publishable today</p>
        </div>
        <div className="card p-3">
          <div className="text-micro uppercase tracking-wide text-slate">Draft versions</div>
          <div className="mt-1 font-mono text-h2 text-navy">{stats.drafts}</div>
          <p className="text-caption text-slate">Awaiting second-line approval</p>
        </div>
      </div>

      {creating && (
        <div className="card mb-6 space-y-3 p-5">
          <CeremonyBanner>
            <p className="font-medium text-navy">Track-2 — register a new curve definition</p>
            <p className="mt-1">
              Creates v1 as a DRAFT with a documented rationale. A second operator must approve it
              (dual control) before any construction or publish can run against this code. The curve
              code is the AEQ.* golden-copy name and cannot change later.
            </p>
          </CeremonyBanner>
          <DefinitionForm
            mode="create"
            busy={createBusy}
            submitLabel="Create v1 draft"
            onSubmit={(fields, curveCode) => void submitCreate(fields, curveCode)}
            onCancel={() => setCreating(false)}
          />
          {createError && <ErrorPanel error={createError} context="Creating the curve definition" />}
        </div>
      )}

      {loading && (
        <div className="card">
          <SkeletonRows rows={6} />
        </div>
      )}
      {error && <ErrorPanel error={error} onRetry={reload} context="Loading curve definitions" />}

      {data && data.definitions.length === 0 && !creating && (
        <div className="card p-5">
          <EmptyState
            title="No curve definitions yet"
            hint="Register a definition to start. It needs a currency, calendar, instrument set, interpolation method and output basis — then a second operator approves it before construction can run."
          />
        </div>
      )}

      {data &&
        [...byCode.entries()].map(([code, versions]) => {
          const active = currentApproved(versions);
          return (
            <section key={code} className="card mb-6">
              {/* code header */}
              <div className="flex flex-wrap items-center gap-3 border-b border-border-light px-5 py-3">
                <Waypoints size={16} className="text-slate" />
                <h2 className="text-h3 text-navy">
                  <span className="font-mono">{code}</span>
                </h2>
                {active ? (
                  <Chip tone="ok">
                    approved · v{active.version}
                    {active.effective_from ? ` · from ${fmtDate(active.effective_from)}` : ''}
                  </Chip>
                ) : (
                  <Chip tone="warn">no approved version — construction refused</Chip>
                )}
                {versions[0] && <Chip mono>{versions[0].currency}</Chip>}
                {versions[0] && (
                  <span className="text-micro uppercase tracking-wide text-slate">
                    {versions[0].curve_kind}
                  </span>
                )}
                <div className="ml-auto flex items-center gap-2">
                  <Link
                    href={`/desk/curves/${encodeURIComponent(code)}`}
                    className="inline-flex items-center gap-1.5 rounded border border-action/50 bg-action-light px-3 py-1.5 text-caption font-medium text-action hover:opacity-90"
                  >
                    Open workspace <ArrowRight size={13} />
                  </Link>
                  <button
                    type="button"
                    onClick={() => {
                      setProposeFor(proposeFor === code ? null : code);
                      setProposeError(null);
                    }}
                    className="inline-flex items-center gap-1.5 rounded border border-warning/60 bg-warning-light px-3 py-1.5 text-caption font-medium text-warning hover:opacity-90"
                  >
                    <AlertTriangle size={13} /> Propose new version (Track 2)
                  </button>
                </div>
              </div>

              {/* version list */}
              <div className="overflow-x-auto border-b border-border-light">
                <table className="w-full text-body">
                  <thead>
                    <tr className="bg-surface text-left text-micro uppercase tracking-wide text-slate">
                      <th className="px-5 py-2 font-medium">Version</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                      <th className="px-3 py-2 font-medium">Effective from</th>
                      <th className="px-3 py-2 font-medium">Projection</th>
                      <th className="px-3 py-2 font-medium">Interp</th>
                      <th className="px-3 py-2 font-medium">Proposed by</th>
                      <th className="px-3 py-2 font-medium">Approved by</th>
                      <th className="px-5 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((v) => {
                      const isActive = active?.version === v.version;
                      return (
                        <tr
                          key={v.id}
                          className={`border-b border-border-light last:border-b-0 ${
                            isActive ? 'bg-success-light/30' : ''
                          }`}
                        >
                          <td className="px-5 py-2 font-mono text-ink">v{v.version}</td>
                          <td className="px-3 py-2">
                            <CurveDefinitionStatusPill status={v.status} />
                          </td>
                          <td className="px-3 py-2 text-caption text-ink">
                            {fmtDate(v.effective_from)}
                          </td>
                          <td className="px-3 py-2 font-mono text-micro text-slate">
                            {v.projection_index ?? DASH}
                          </td>
                          <td className="px-3 py-2 font-mono text-micro text-slate">
                            {v.interpolation_method}
                          </td>
                          <td className="px-3 py-2 font-mono text-micro text-slate">
                            {v.proposed_by}
                          </td>
                          <td className="px-3 py-2 font-mono text-micro text-slate">
                            {v.approved_by ? (
                              <span title={fmtTs(v.approved_at)}>{v.approved_by}</span>
                            ) : (
                              DASH
                            )}
                          </td>
                          <td className="px-5 py-2 text-right">
                            {v.status === 'draft' && (
                              <button
                                type="button"
                                onClick={() => {
                                  setApproveFor(
                                    approveFor?.code === code && approveFor.version === v.version
                                      ? null
                                      : { code, version: v.version },
                                  );
                                  setApproveError(null);
                                }}
                                className="inline-flex items-center gap-1 rounded border border-warning/60 bg-warning-light px-2.5 py-1 text-micro font-medium uppercase tracking-wide text-warning hover:opacity-90"
                              >
                                <ShieldCheck size={12} /> Approve…
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* approve ceremony */}
              {approveFor?.code === code && (
                <div className="space-y-3 border-b border-border-light px-5 py-4">
                  <CeremonyBanner>
                    <p className="font-medium text-navy">
                      Track-2 approval — v{approveFor.version} of{' '}
                      <span className="font-mono">{code}</span>
                    </p>
                    <p className="mt-1">
                      Approving makes this the governing definition from its effective date. Dual
                      control applies: the proposer cannot approve their own version. Construction
                      picks the latest approved version whose effective date is on or before the cob.
                    </p>
                  </CeremonyBanner>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="block">
                      <span className="mb-1 block text-caption font-medium text-slate">
                        Effective from
                      </span>
                      <input
                        type="date"
                        className={inputClass}
                        value={effectiveFrom}
                        onChange={(e) => setEffectiveFrom(e.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      disabled={approveBusy || !effectiveFrom}
                      onClick={() => void submitApprove()}
                      className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:opacity-50"
                    >
                      {approveBusy && <Loader2 size={14} className="animate-spin" />}
                      Approve v{approveFor.version}
                    </button>
                    <button
                      type="button"
                      onClick={() => setApproveFor(null)}
                      className="rounded border border-border px-4 py-2 text-body font-medium text-ink hover:bg-surface"
                    >
                      Cancel
                    </button>
                  </div>
                  {approveError && approveDualControl && (
                    <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                      <ShieldCheck size={14} className="mt-0.5 shrink-0 text-warning" />
                      <div className="min-w-0">
                        <p className="text-body font-medium text-navy">
                          Dual control: the proposer cannot approve their own curve definition —
                          sign in as a second operator.
                        </p>
                        <p className="mt-1 text-caption text-slate">
                          API said: “{approveError.message}”
                        </p>
                      </div>
                    </div>
                  )}
                  {approveError && !approveDualControl && (
                    <ErrorPanel error={approveError} context="Approving the version" />
                  )}
                </div>
              )}

              {/* propose ceremony */}
              {proposeFor === code && (
                <div className="space-y-3 border-b border-border-light px-5 py-4">
                  <CeremonyBanner>
                    <p className="font-medium text-navy">
                      Track-2 definition change — NOT part of a weekly run
                    </p>
                    <p className="mt-1">
                      Drafts v{versions[versions.length - 1].version + 1} prefilled from the current
                      version. Requires approval by a second operator and is effective-dated; running
                      constructions keep their bound version. The latest version must be approved
                      before a new one can be proposed.
                    </p>
                  </CeremonyBanner>
                  <DefinitionForm
                    mode="propose"
                    base={active ?? versions[versions.length - 1]}
                    busy={proposeBusy}
                    submitLabel={`Propose v${versions[versions.length - 1].version + 1}`}
                    onSubmit={(fields) => void submitPropose(code, fields)}
                    onCancel={() => setProposeFor(null)}
                  />
                  {proposeError && (
                    <ErrorPanel error={proposeError} context="Proposing a new version" />
                  )}
                </div>
              )}
            </section>
          );
        })}
    </div>
  );
}
