'use client';

import { useState, type ReactNode } from 'react';
import {
  AlertTriangle,
  Gavel,
  History,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
} from 'lucide-react';
import {
  fixBiBackfill,
  fixConfig,
  fixOfficialRun,
  fixRecompute,
  fixRerunIngestion,
  type BiBackfillRequest,
  type TenantConfigResponse,
  type TenantFixConfigKind,
  type TenantFixJob,
  type TenantIngestionBatch,
} from '@/lib/api';
import { backfillWindowProblem, biBackfillRequest, oldestAsOfDate } from '@/lib/bi-backfill';
import { useInspector } from '@/lib/inspector';
import { useMutation } from '@/lib/use-api';
import { fmtDate, shortId } from '@/lib/format';
import {
  Button,
  Field,
  Input,
  Modal,
  SectionCard,
  Select,
  StatusPill,
  Textarea,
  useToast,
} from '@/components/ui';

/**
 * Remediation (fix) panel — the WRITE side of the Tenant Inspector.
 *
 * Renders ONLY while the operator holds an ACTIVE inspection session for THIS
 * org (the same session gate the deep-read sections use). Every action is a
 * single confirmed, audited operation: each opens a confirmation Modal that
 * REQUIRES a note (min length), calls the corresponding /fix endpoint through
 * `useMutation` (which auto-toasts success/failure), and on success triggers a
 * reload of the relevant read section.
 *
 * The panel is self-gating (reads `useInspector()` directly) so it can never
 * render a write control without a live session, independent of the page's own
 * gate.
 */

/** Minimum note length across every fix action — these are higher-stakes than a read. */
const NOTE_MIN = 5;

function noteValid(note: string): boolean {
  return note.trim().length >= NOTE_MIN;
}

function pct(value: number): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

/** The required, audited note every fix carries — one control, one copy. */
function NoteField({ note, setNote }: { note: string; setNote: (v: string) => void }) {
  return (
    <Field
      label="Note"
      required
      hint={`Recorded against this inspection session in the operator audit log (min ${NOTE_MIN} characters).`}
    >
      <Textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={3}
        placeholder="Why this change is needed…"
        invalid={note.length > 0 && !noteValid(note)}
      />
    </Field>
  );
}

/** One remediation action laid out as a divided row: describe it, then the trigger. */
function ActionRow({
  icon,
  title,
  description,
  danger = false,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-light px-5 py-4 last:border-b-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-body font-medium text-navy">
          <span className={danger ? 'text-critical' : 'text-slate'}>{icon}</span>
          {title}
        </div>
        <p className="mt-0.5 max-w-prose text-caption text-slate">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

/** Toast the job id on success — the caller relays the queued job. */
function jobToast(verb: string): (job: TenantFixJob) => string {
  return (job) => `${verb} queued · job ${shortId(job.job_id, 10)}`;
}

// ---- Recompute live metrics ----------------------------------------------

function RecomputeAction({ orgId, onDone }: { orgId: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');

  const mut = useMutation((n: string) => fixRecompute(orgId, { note: n }), {
    errorContext: 'Recompute',
    successMessage: jobToast('Recompute'),
    onSuccess: () => {
      setOpen(false);
      setNote('');
      // The pipeline_refresh is debounced/async — reload now and again shortly
      // so the Metrics/Findings sections pick up the re-derived state.
      onDone();
      window.setTimeout(onDone, 2500);
    },
  });

  return (
    <ActionRow
      icon={<RefreshCw size={15} aria-hidden />}
      title="Recompute live metrics"
      description="Re-derives this bank's live metrics and findings by re-running the ingestion pipeline (a debounced pipeline_refresh job). No official filing run is minted."
    >
      <Button
        variant="secondary"
        size="sm"
        icon={<RefreshCw size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Recompute
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Recompute live metrics"
        description="Re-derives this tenant's metrics on their behalf. Audited to the inspection session."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={mut.loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mut.loading}
              disabled={!noteValid(note)}
              onClick={() => void mut.mutate(note.trim())}
            >
              Queue recompute
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-caption text-slate">
            The pipeline re-derives facts and upserts live metrics and findings — no immutable run is
            written. Results refresh shortly after the job runs; the Metrics section will reload
            automatically.
          </p>
          <NoteField note={note} setNote={setNote} />
        </div>
      </Modal>
    </ActionRow>
  );
}

// ---- Mint official run ----------------------------------------------------

function OfficialRunAction({ orgId, onDone }: { orgId: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [asOf, setAsOf] = useState('');

  const mut = useMutation(
    (args: { asOf: string; note: string }) =>
      fixOfficialRun(orgId, { as_of_date: args.asOf || undefined, note: args.note }),
    {
      errorContext: 'Official run',
      successMessage: jobToast('Official run'),
      onSuccess: () => {
        setOpen(false);
        setNote('');
        setAsOf('');
        onDone();
      },
    },
  );

  return (
    <ActionRow
      icon={<Gavel size={15} aria-hidden />}
      title="Mint official run"
      description="Enqueues an immutable official filing run for this bank. Unlike a recompute, this writes a RegulatoryRun snapshot."
    >
      <Button
        variant="secondary"
        size="sm"
        icon={<Gavel size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Official run
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Mint an official run"
        description="Writes an immutable filing run on this tenant's behalf. Audited to the inspection session."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={mut.loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mut.loading}
              disabled={!noteValid(note)}
              onClick={() => void mut.mutate({ asOf, note: note.trim() })}
            >
              Mint official run
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="As-of date"
            hint="Optional — defaults to the latest reporting period on or before today."
          >
            <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </Field>
          <NoteField note={note} setNote={setNote} />
        </div>
      </Modal>
    </ActionRow>
  );
}

// ---- Re-run ingestion -----------------------------------------------------

function RerunIngestionAction({
  orgId,
  batches,
  onDone,
}: {
  orgId: string;
  batches: TenantIngestionBatch[];
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [batchId, setBatchId] = useState('');
  const toaster = useToast();

  const mut = useMutation(
    (args: { batchId: string; note: string }) =>
      fixRerunIngestion(orgId, { batch_id: args.batchId, note: args.note }),
    {
      errorContext: 'Re-run ingestion',
      // No successMessage: fire the toast in onSuccess so the backend's
      // re-upload caveat (`detail`) rides along as the description, verbatim.
      // Ingestion batches are immutable — the re-run re-derives via
      // pipeline_refresh, so fixing a source-parsing problem still needs a
      // fresh upload through the Data Engine.
      onSuccess: (job) => {
        toaster.success(`Re-run queued · job ${shortId(job.job_id, 10)}`, {
          description: job.detail,
          // Keep the caveat on screen until dismissed when present.
          duration: job.detail ? 0 : undefined,
        });
        setOpen(false);
        setNote('');
        setBatchId('');
        onDone();
      },
    },
  );

  const canConfirm = noteValid(note) && batchId.trim().length > 0;

  return (
    <ActionRow
      icon={<RotateCcw size={15} aria-hidden />}
      title="Re-run ingestion"
      description="Re-runs a specific ingestion batch — the fix for a failed or partial upload. Pick a recent batch or paste a batch id."
    >
      <Button
        variant="secondary"
        size="sm"
        icon={<RotateCcw size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Re-run batch
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Re-run an ingestion batch"
        description="Re-runs one batch on this tenant's behalf. Audited to the inspection session."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={mut.loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mut.loading}
              disabled={!canConfirm}
              onClick={() => void mut.mutate({ batchId: batchId.trim(), note: note.trim() })}
            >
              Re-run batch
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {batches.length > 0 && (
            <Field label="Recent batch" hint="Selecting one fills the batch id below.">
              <Select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
                <option value="">Select a batch…</option>
                {batches.map((b) => (
                  <option key={b.batch_id} value={b.batch_id}>
                    {b.source_system} · {fmtDate(b.as_of_date)} · {b.status} · {shortId(b.batch_id, 8)}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label="Batch id" required hint="The ingestion batch to re-run.">
            <Input
              value={batchId}
              onChange={(e) => setBatchId(e.target.value)}
              placeholder="batch id"
              invalid={batchId.length > 0 && batchId.trim().length === 0}
            />
          </Field>
          <NoteField note={note} setNote={setNote} />
        </div>
      </Modal>
    </ActionRow>
  );
}

// ---- Fix config -----------------------------------------------------------

interface TargetOption {
  id: string;
  label: string;
  /** Human-readable current value, shown as a hint when selected. */
  current: string;
}

function FixConfigAction({
  orgId,
  config,
  onDone,
}: {
  orgId: string;
  config: TenantConfigResponse | null;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [kind, setKind] = useState<TenantFixConfigKind>('mapping_active');
  const [targetId, setTargetId] = useState('');
  const [activeValue, setActiveValue] = useState('true');
  const [thresholdValue, setThresholdValue] = useState('');

  // The dropdown value / target_id is the record's UUID `id` (what fix/config
  // resolves against); the human label keeps source_ref / threshold_code +
  // current value so the operator can still read what they're picking.
  const mappingOptions: TargetOption[] = (config?.active_mappings ?? []).map((m) => ({
    id: m.id,
    label: `${m.name} · ${m.source_system}/${m.source_ref} · v${m.version}`,
    current: m.status,
  }));
  const thresholdOptions: TargetOption[] = [
    ...(config?.threshold_register?.liquidity ?? []).map((t) => ({
      id: t.id,
      label: `${t.threshold_code} · ${t.institution_class} (liquidity) · ${pct(t.threshold_pct)}`,
      current: pct(t.threshold_pct),
    })),
    ...(config?.threshold_register?.capital ?? []).map((t) => ({
      id: t.id,
      label: `${t.threshold_code} (capital) · ${pct(t.value_pct)}`,
      current: pct(t.value_pct),
    })),
  ];
  const options = kind === 'mapping_active' ? mappingOptions : thresholdOptions;
  const selected = options.find((o) => o.id === targetId);

  const numericThreshold = Number(thresholdValue);
  const thresholdOk = thresholdValue.trim().length > 0 && Number.isFinite(numericThreshold);
  const canConfirm =
    noteValid(note) &&
    targetId.trim().length > 0 &&
    (kind === 'threshold_value' ? thresholdOk : true);

  const mut = useMutation((body: Parameters<typeof fixConfig>[1]) => fixConfig(orgId, body), {
    errorContext: 'Fix config',
    successMessage: 'Configuration updated',
    onSuccess: () => {
      setOpen(false);
      setNote('');
      setTargetId('');
      setThresholdValue('');
      setActiveValue('true');
      onDone();
    },
  });

  function changeKind(next: TenantFixConfigKind) {
    setKind(next);
    setTargetId('');
    setThresholdValue('');
    setActiveValue('true');
  }

  function submit() {
    const value = kind === 'mapping_active' ? activeValue === 'true' : numericThreshold;
    void mut.mutate({ kind, target_id: targetId.trim(), value, note: note.trim() });
  }

  return (
    <ActionRow
      icon={<SlidersHorizontal size={15} aria-hidden />}
      title="Fix configuration"
      description="Changes this bank's configuration — toggle a source mapping active, or set a Board threshold value. Changes take effect on their next computation."
      danger
    >
      <Button
        variant="danger"
        size="sm"
        icon={<SlidersHorizontal size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Fix config
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Fix configuration"
        description="Changes this tenant's configuration on their behalf. Audited to the inspection session."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={mut.loading}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={mut.loading}
              disabled={!canConfirm}
              onClick={submit}
            >
              Apply change
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="flex items-start gap-2.5 rounded-md border border-critical/40 bg-critical-light p-3 text-caption text-critical">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
            <p>
              <span className="font-medium">This changes the bank&apos;s configuration.</span> A
              mapping toggle or threshold change alters how their next regulatory computation behaves.
            </p>
          </div>

          <Field label="What to change">
            <Select value={kind} onChange={(e) => changeKind(e.target.value as TenantFixConfigKind)}>
              <option value="mapping_active">Source mapping — active flag</option>
              <option value="threshold_value">Board threshold — value</option>
            </Select>
          </Field>

          {options.length > 0 && (
            <Field
              label={kind === 'mapping_active' ? 'Pick a mapping' : 'Pick a threshold'}
              hint={
                selected
                  ? `Current: ${selected.current}`
                  : 'Selecting one fills the target id below.'
              }
            >
              <Select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                <option value="">Select…</option>
                {options.map((o) => (
                  <option key={`${o.id}-${o.label}`} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>
          )}

          <Field
            label="Target id"
            required
            hint={
              kind === 'mapping_active'
                ? 'The mapping record id (UUID). Pick one above to fill this.'
                : 'The threshold record id (UUID). Pick one above to fill this.'
            }
          >
            <Input
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              placeholder={options.length > 0 ? 'pick above or paste a record id' : 'record id (UUID)'}
            />
          </Field>

          {kind === 'mapping_active' ? (
            <Field label="New active state">
              <Select value={activeValue} onChange={(e) => setActiveValue(e.target.value)}>
                <option value="true">Active</option>
                <option value="false">Inactive</option>
              </Select>
            </Field>
          ) : (
            <Field label="New value (%)" required hint="The threshold percentage to set.">
              <Input
                type="number"
                step="0.01"
                value={thresholdValue}
                onChange={(e) => setThresholdValue(e.target.value)}
                placeholder="e.g. 13.5"
                invalid={thresholdValue.length > 0 && !thresholdOk}
              />
            </Field>
          )}

          <NoteField note={note} setNote={setNote} />
        </div>
      </Modal>
    </ActionRow>
  );
}

// ---- BI mart backfill -----------------------------------------------------

function BiBackfillAction({
  orgId,
  bankId,
  batches,
  onDone,
}: {
  orgId: string;
  bankId: string | null;
  batches: TenantIngestionBatch[];
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [untilDate, setUntilDate] = useState('');
  const toaster = useToast();

  const mut = useMutation((body: BiBackfillRequest) => fixBiBackfill(orgId, body), {
    errorContext: 'Mart backfill',
    // No successMessage: the toast names the window the chain will walk, which
    // is the one thing the operator wants confirmed (the server resolved the
    // from-date when it was left blank).
    onSuccess: (job) => {
      toaster.success(`Backfill queued · job ${shortId(job.job_id, 10)}`, {
        description: `Walks ${job.bank_id} from ${fmtDate(job.cursor_date)} back to ${fmtDate(job.until_date)}, one reporting date per hop.`,
      });
      setOpen(false);
      setNote('');
      setFromDate('');
      setUntilDate('');
      onDone();
    },
  });

  const oldestIngested = oldestAsOfDate(batches);
  const windowProblem = backfillWindowProblem({ fromDate, untilDate });
  const canConfirm = bankId !== null && noteValid(note) && windowProblem === null;

  return (
    <ActionRow
      icon={<History size={15} aria-hidden />}
      title="Backfill analytics history"
      description="Builds this bank's BI marts for past reporting dates, newest-first, as a chain of bi_mart_backfill jobs on the bi worker lane. Without it only the live date is ever built, so every twelve-month widget and trend pack answers needs-data — and after three terminal failures this is the only way back."
    >
      <Button
        variant="secondary"
        size="sm"
        icon={<History size={14} aria-hidden />}
        onClick={() => setOpen(true)}
        disabled={bankId === null}
        title={bankId === null ? 'This tenant has no institution to build for yet.' : undefined}
      >
        Backfill history
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Backfill analytics history"
        description="Queues a mart history walk on this tenant's behalf. Audited to the inspection session."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={mut.loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mut.loading}
              disabled={!canConfirm}
              onClick={() =>
                void mut.mutate(
                  biBackfillRequest({ bankId: bankId ?? '', fromDate, untilDate, note }),
                )
              }
            >
              Queue backfill
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-caption text-slate">
            One <span className="font-mono">bi_mart_backfill</span> job is queued and re-queues
            itself hop by hop from the newest date back to the oldest, inclusive. The server
            refuses (409) if BI mart builds are switched off in this deployment, if a chain for
            this bank is already queued or running, or if the bank has no canonical snapshot to
            start from and no from-date is given.
          </p>
          <Field label="Institution" hint="The bank whose marts are built. Fixed to this tenant.">
            <Input value={bankId ?? ''} readOnly disabled />
          </Field>
          <Field
            label="From date"
            hint="Optional — the newest date to build. Blank means the bank's latest canonical snapshot."
          >
            <Input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </Field>
          <Field
            label="Until date"
            required
            hint={
              oldestIngested
                ? `The oldest date to build, inclusive. The oldest ingested batch is as of ${fmtDate(oldestIngested)}.`
                : 'The oldest date to build, inclusive.'
            }
          >
            <div className="flex items-center gap-2">
              <Input
                type="date"
                value={untilDate}
                onChange={(e) => setUntilDate(e.target.value)}
                invalid={untilDate.length > 0 && windowProblem !== null}
              />
              {oldestIngested && untilDate !== oldestIngested && (
                <Button variant="ghost" size="sm" onClick={() => setUntilDate(oldestIngested)}>
                  Use oldest batch
                </Button>
              )}
            </div>
          </Field>
          {windowProblem && (untilDate.length > 0 || fromDate.length > 0) && (
            <p className="text-caption text-critical">{windowProblem}</p>
          )}
          <NoteField note={note} setNote={setNote} />
        </div>
      </Modal>
    </ActionRow>
  );
}

// ---- Panel ----------------------------------------------------------------

export function RemediationPanel({
  orgId,
  orgLabel,
  bankId,
  ingestionBatches,
  config,
  onRecomputed,
  onOfficialRun,
  onReran,
  onConfigChanged,
  onBackfill,
}: {
  orgId: string;
  orgLabel?: string;
  /** The tenant's institution (`OperatorTenant.bank_id`); null for a half-provisioned tenant. */
  bankId: string | null;
  ingestionBatches: TenantIngestionBatch[];
  config: TenantConfigResponse | null;
  onRecomputed: () => void;
  onOfficialRun: () => void;
  onReran: () => void;
  onConfigChanged: () => void;
  onBackfill: () => void;
}) {
  const { active } = useInspector();
  // Same gate the deep-read sections use: an ACTIVE session for THIS org.
  if (!active || active.organization_id !== orgId) return null;

  const breakGlass = active.mode === 'break_glass';

  return (
    <SectionCard
      title="Remediation"
      subtitle={`Write actions on ${orgLabel ?? orgId} — available only during this inspection session`}
      noPadding
      className="mb-5"
      actions={
        <StatusPill tone={breakGlass ? 'critical' : 'action'}>
          {breakGlass ? 'Break glass' : 'Consent'}
        </StatusPill>
      }
    >
      <div className="flex items-start gap-2.5 border-b border-border-light bg-warning-light/60 px-5 py-3 text-caption text-warning">
        <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
        <p>
          <span className="font-medium">
            Actions here change this bank&apos;s state on their behalf.
          </span>{' '}
          Every action is audited to the inspection session.
        </p>
      </div>

      <RecomputeAction orgId={orgId} onDone={onRecomputed} />
      <OfficialRunAction orgId={orgId} onDone={onOfficialRun} />
      <RerunIngestionAction orgId={orgId} batches={ingestionBatches} onDone={onReran} />
      <FixConfigAction orgId={orgId} config={config} onDone={onConfigChanged} />
      <BiBackfillAction
        orgId={orgId}
        bankId={bankId}
        batches={ingestionBatches}
        onDone={onBackfill}
      />
    </SectionCard>
  );
}
