'use client';

import { useMemo, useState } from 'react';
import { AlertTriangle, Download, Eye, GitCompare, Plus, ScrollText, ShieldCheck } from 'lucide-react';
import {
  approveDeskMethodologyVersion,
  ensureDefaultDeskMethodology,
  listDeskMethodologies,
  proposeDeskMethodologyVersion,
  type DeskMethodology,
} from '@/lib/api';
import { useApi, useMutation } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
import {
  Button,
  Chip,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  Modal,
  PageHeader,
  SectionCard,
  SkeletonRows,
  StatusChip,
  Textarea,
} from '@/components/ui';
import { CeremonyBanner } from '@/components/curves';
import { MethodologyVersionDiff } from '@/components/deskdata/MethodologyVersionDiff';
import { NewMethodologyDialog } from '@/components/deskdata/NewMethodologyDialog';

/**
 * /desk/methodology — the methodology register (spec §5): for each code,
 * every version with its full parameter set, rationale, proposer, approver,
 * and effective date. This is the artifact a bank's due-diligence team or a
 * regulator inspects.
 *
 * Track 2 is DELIBERATELY heavier than the weekly screen (spec §11a: "the
 * two must be visibly different actions"): proposing or approving a version
 * runs through an amber-ceremony MODAL, never a one-click affordance. A
 * side-by-side VersionDiff makes every parameter change legible.
 *
 * Source: GET/POST /operator/v1/desk/methodologies…
 */

// ---------------------------------------------------------------------------
// The page.
// ---------------------------------------------------------------------------

export default function MethodologyPage() {
  const { data, error, loading, reload } = useApi(() => listDeskMethodologies());

  // versions grouped by code, ascending version (API sorts that way already).
  const byCode = useMemo(() => {
    const map = new Map<string, DeskMethodology[]>();
    for (const row of data?.methodologies ?? []) {
      const bucket = map.get(row.methodology_code) ?? [];
      bucket.push(row);
      map.set(row.methodology_code, bucket);
    }
    return map;
  }, [data]);

  // The current approved version is the proposal base; document content is
  // deliberately read through its PDF preview, not expanded in the register.
  function currentApproved(versions: DeskMethodology[]): DeskMethodology | null {
    const approved = versions.filter((v) => v.status === 'approved');
    return approved.length > 0 ? approved[approved.length - 1] : null;
  }
  function selectedVersion(code: string, versions: DeskMethodology[]): DeskMethodology {
    return currentApproved(versions) ?? versions[versions.length - 1];
  }

  // ---- create a new methodology code (Track-2 register write) --------------
  const [newCodeOpen, setNewCodeOpen] = useState(false);

  // ---- bootstrap (empty register) ----------------------------------------
  const seed = useMutation(ensureDefaultDeskMethodology, {
    successMessage: 'Seeded AEQ-GHS-CURVES v1 (draft)',
    errorContext: 'Seed methodology',
    onSuccess: () => reload(),
  });

  // ---- Track 2: propose ----------------------------------------------------
  const [proposeFor, setProposeFor] = useState<string | null>(null);
  const [rationale, setRationale] = useState('');
  const [paramsText, setParamsText] = useState('');

  const propose = useMutation(proposeDeskMethodologyVersion, {
    successMessage: (row) => `Proposed ${row.methodology_code} v${row.version} (draft)`,
    errorContext: 'Propose version',
    onSuccess: () => {
      setProposeFor(null);
      reload();
    },
  });

  function openPropose(code: string, versions: DeskMethodology[]) {
    const base = selectedVersion(code, versions);
    setProposeFor(code);
    setRationale('');
    setParamsText(JSON.stringify(base.parameters, null, 2));
    propose.reset();
  }

  const paramsParse = useMemo(():
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string } => {
    if (proposeFor === null) return { ok: false, error: '' };
    try {
      const parsed: unknown = JSON.parse(paramsText);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Parameters must be a JSON object.' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : 'Invalid JSON.' };
    }
  }, [proposeFor, paramsText]);

  function submitPropose() {
    if (proposeFor === null || !paramsParse.ok || rationale.trim() === '') return;
    void propose.mutate(proposeFor, {
      parameters: paramsParse.value,
      change_rationale: rationale.trim(),
    });
  }

  const proposeVersions = proposeFor !== null ? byCode.get(proposeFor) ?? [] : [];
  const proposeNextVersion =
    (proposeVersions[proposeVersions.length - 1]?.version ?? 0) + 1;

  // ---- Track 2: approve ----------------------------------------------------
  const [approveFor, setApproveFor] = useState<{ code: string; version: number } | null>(null);
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));

  const approve = useMutation(approveDeskMethodologyVersion, {
    successMessage: (row) => `Approved ${row.methodology_code} v${row.version}`,
    errorContext: 'Approve version',
    onSuccess: () => {
      setApproveFor(null);
      reload();
    },
  });

  const approveDualControl =
    approve.error !== null &&
    (approve.error.status === 409 || approve.error.status === 422) &&
    /proposer/i.test(approve.error.message);

  function openApprove(code: string, version: number) {
    setApproveFor({ code, version });
    approve.reset();
  }

  function submitApprove() {
    if (approveFor === null || !effectiveFrom) return;
    void approve.mutate(approveFor.code, approveFor.version, { effective_from: effectiveFrom });
  }

  const [preview, setPreview] = useState<DeskMethodology | null>(null);
  function pdfPath(methodology: DeskMethodology, download = false): string {
    const suffix = download ? '?download=1' : '';
    return `/api/op/operator/v1/desk/methodologies/${encodeURIComponent(methodology.methodology_code)}/versions/${methodology.version}/pdf${suffix}`;
  }

  return (
    <div>
      <PageHeader
        title="Methodology register"
        sub="Track 2: versioned parameters under dual control. The weekly run READS from this register; changing it is a rare, documented, effective-dated event — never something done in passing during a publish."
        action={
          <Button icon={<Plus size={15} />} onClick={() => setNewCodeOpen(true)}>
            New methodology code
          </Button>
        }
      />

      {loading && (
        <div className="card">
          <SkeletonRows rows={6} />
        </div>
      )}
      {error && <ErrorPanel error={error} onRetry={reload} context="Loading the register" />}

      {data && data.methodologies.length === 0 && (
        <SectionCard title="No methodologies registered">
          <EmptyState
            title="No methodologies registered"
            hint="Seed the default AEQ-GHS-CURVES v1 draft, or register a new code. Seeding only creates the DRAFT — a second operator must approve it (Track 2) before any determination can run."
            action={
              <Button loading={seed.loading} onClick={() => void seed.mutate()}>
                Seed default methodology (v1 draft)
              </Button>
            }
          />
        </SectionCard>
      )}

      {data &&
        [...byCode.entries()].map(([code, versions]) => {
          const active = currentApproved(versions);
          return (
            <section key={code} className="card mb-6">
              {/* -------------------------------------------- code header */}
              <div className="flex flex-wrap items-center gap-3 border-b border-border-light px-5 py-3">
                <ScrollText size={16} className="text-slate" />
                <h2 className="text-h3 text-navy">
                  <span className="font-mono">{code}</span>
                </h2>
                {active ? (
                  <Chip tone="ok">
                    current approved · v{active.version}
                    {active.effective_from ? ` · effective ${fmtDate(active.effective_from)}` : ''}
                  </Chip>
                ) : (
                  <Chip tone="warn">no approved version — determinations will be refused</Chip>
                )}
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<AlertTriangle size={13} className="text-warning" />}
                  className="ml-auto border-warning/60 bg-warning-light text-warning hover:bg-warning-light"
                  onClick={() => openPropose(code, versions)}
                >
                  Propose new version (Track 2)
                </Button>
              </div>

              {/* ------------------------------------------- version list */}
              <div className="overflow-x-auto border-b border-border-light">
                <table className="w-full text-body">
                  <thead>
                    <tr className="bg-surface text-left text-micro uppercase tracking-wide text-slate">
                      <th className="px-5 py-2 font-medium">Version</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                      <th className="px-3 py-2 font-medium">Effective from</th>
                      <th className="px-3 py-2 font-medium">Proposed by</th>
                      <th className="px-3 py-2 font-medium">Approved by</th>
                      <th className="px-5 py-2 font-medium">Rationale</th>
                      <th className="px-5 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((v) => {
                      const isActive = active?.version === v.version;
                      return (
                        <tr
                          key={v.id}
                          className={`cursor-pointer border-b border-border-light last:border-b-0 ${
                            isActive ? 'bg-success-light/40' : ''
                          } hover:bg-surface`}
                        >
                          <td className="px-5 py-2 font-mono text-ink">v{v.version}</td>
                          <td className="px-3 py-2">
                            <StatusChip value={v.status} />
                          </td>
                          <td className="px-3 py-2 text-caption text-ink">
                            {fmtDate(v.effective_from)}
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
                          <td
                            className="max-w-sm truncate px-5 py-2 text-caption text-slate"
                            title={v.change_rationale}
                          >
                            {v.change_rationale}
                          </td>
                          <td className="px-5 py-2 text-right">
                            <div className="inline-flex items-center gap-1.5">
                              <Button
                                size="sm"
                                variant="ghost"
                                icon={<Eye size={13} />}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setPreview(v);
                                }}
                              >
                                Preview PDF
                              </Button>
                              <a
                                href={pdfPath(v, true)}
                                download={`${v.methodology_code}-v${v.version}-methodology.pdf`}
                                onClick={(e) => e.stopPropagation()}
                                className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-caption font-medium text-slate transition-colors hover:bg-surface hover:text-ink"
                              >
                                <Download size={13} aria-hidden /> Download
                              </a>
                            {v.status === 'draft' && (
                              <Button
                                size="sm"
                                variant="secondary"
                                icon={<ShieldCheck size={12} className="text-warning" />}
                                className="border-warning/60 bg-warning-light text-warning hover:bg-warning-light"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openApprove(code, v.version);
                                }}
                              >
                                Approve…
                              </Button>
                            )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* ------------------------------------- version comparison */}
              {versions.length >= 2 && (
                <details className="border-b border-border-light px-5 py-3">
                  <summary className="flex cursor-pointer items-center gap-2 text-caption font-medium text-slate">
                    <GitCompare size={14} aria-hidden /> Compare versions (parameter diff)
                  </summary>
                  <div className="mt-3">
                    <MethodologyVersionDiff versions={versions} />
                  </div>
                </details>
              )}

            </section>
          );
        })}

      {/* ================================================== ceremony modals */}

      {/* Track-2 propose */}
      <Modal
        open={proposeFor !== null}
        onClose={() => setProposeFor(null)}
        size="lg"
        title="Propose new version (Track 2)"
        description={
          proposeFor !== null ? (
            <span>
              <span className="font-mono">{proposeFor}</span> · drafts v{proposeNextVersion}
            </span>
          ) : undefined
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setProposeFor(null)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="methodology-propose-form"
              loading={propose.loading}
              disabled={rationale.trim() === '' || !paramsParse.ok}
            >
              Propose v{proposeNextVersion}
            </Button>
          </>
        }
      >
        <form
          id="methodology-propose-form"
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submitPropose();
          }}
        >
          <CeremonyBanner>
            <p className="font-medium text-navy">
              Track-2 methodology change — this is NOT part of a weekly publish
            </p>
            <p className="mt-1">
              Changing any versioned parameter or formula is a controlled event: it drafts v
              {proposeNextVersion} with a documented rationale, requires approval by a second
              operator at a higher bar, and is effective-dated. Running determinations keep their
              bound version.
            </p>
          </CeremonyBanner>
          <Field label="Change rationale" required hint="Recorded in the register.">
            <Textarea
              rows={3}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="Why is this parameter set changing? Cite re-estimation evidence."
            />
          </Field>
          <Field
            label="Parameters (JSON)"
            error={!paramsParse.ok && paramsParse.error ? `JSON error: ${paramsParse.error}` : undefined}
            hint={paramsParse.ok ? 'Prefilled from the shown version — edit only what changes.' : undefined}
          >
            <Textarea
              rows={16}
              spellCheck={false}
              className="font-mono text-caption"
              value={paramsText}
              onChange={(e) => setParamsText(e.target.value)}
            />
          </Field>
          {propose.error && <ErrorPanel error={propose.error} context="Proposing the version" />}
        </form>
      </Modal>

      {/* Track-2 approve */}
      <Modal
        open={approveFor !== null}
        onClose={() => setApproveFor(null)}
        size="md"
        title="Approve version (Track 2)"
        description={
          approveFor !== null ? (
            <span>
              v{approveFor.version} of <span className="font-mono">{approveFor.code}</span>
            </span>
          ) : undefined
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setApproveFor(null)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="methodology-approve-form"
              loading={approve.loading}
              disabled={!effectiveFrom}
            >
              Approve{approveFor ? ` v${approveFor.version}` : ''}
            </Button>
          </>
        }
      >
        <form
          id="methodology-approve-form"
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submitApprove();
          }}
        >
          <CeremonyBanner>
            <p className="font-medium text-navy">Track-2 approval</p>
            <p className="mt-1">
              Approving makes this parameter set the governing methodology from its effective date.
              Dual control applies: the proposer cannot approve their own version. This is an
              audited, effective-dated event; history is never silently altered.
            </p>
          </CeremonyBanner>
          <Field label="Effective from" required>
            <Input
              type="date"
              value={effectiveFrom}
              onChange={(e) => setEffectiveFrom(e.target.value)}
            />
          </Field>
          {approve.error && approveDualControl && (
            <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-warning" />
              <div className="min-w-0">
                <p className="text-body font-medium text-navy">
                  Dual control: the proposer cannot approve their own methodology version — sign in
                  as a second operator.
                </p>
                <p className="mt-1 text-caption text-slate">API said: “{approve.error.message}”</p>
              </div>
            </div>
          )}
          {approve.error && !approveDualControl && (
            <ErrorPanel error={approve.error} context="Approving the version" />
          )}
        </form>
      </Modal>

      {/* Create a new methodology code */}
      <NewMethodologyDialog
        open={newCodeOpen}
        onClose={() => setNewCodeOpen(false)}
        onCreated={() => reload()}
      />

      <Modal
        open={preview !== null}
        onClose={() => setPreview(null)}
        size="xl"
        title={preview ? `${preview.methodology_code} v${preview.version}` : 'Methodology PDF'}
        description="Read-only governed methodology export."
        footer={
          <>
            <Button variant="secondary" onClick={() => setPreview(null)}>
              Close
            </Button>
            {preview && (
              <a
                href={pdfPath(preview, true)}
                download={`${preview.methodology_code}-v${preview.version}-methodology.pdf`}
                className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium text-white"
              >
                <Download size={15} aria-hidden /> Download PDF
              </a>
            )}
          </>
        }
      >
        {preview && (
          <div className="overflow-hidden border border-border-light bg-surface" style={{ height: '70vh' }}>
            <iframe
              title={`${preview.methodology_code} v${preview.version} methodology PDF`}
              src={pdfPath(preview)}
              className="h-full w-full"
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
