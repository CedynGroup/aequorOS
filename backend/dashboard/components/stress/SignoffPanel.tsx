'use client';

/**
 * Governance & Board sign-off for an enterprise-stress run (docs/stress.md §3.8,
 * BoG Guideline ¶20, ¶57-63, ¶67(b)(c)). Surfaces the Phase-5 governed sign-off:
 * the preparer captures the scenario narrative + assumptions rationale (¶67(a)(b),
 * ¶45), submits for attestation, and the Board (a different user — maker≠checker)
 * attests with a credibility rationale + challenge record (¶20). Only an attested
 * run may be filed as the ICAAP Appendix II stress return.
 */

import { useState } from 'react';
import { ShieldCheck, Send, PenLine } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { fmtDateUTC } from '@/lib/api/values';
import { ApiError } from '@/lib/api/client';
import {
  useBankStressSignoffs,
  useStressSignoff,
  useCreateStressSignoff,
  useUpdateStressSignoff,
  useSubmitStressSignoff,
  useAttestStressSignoff,
  useWithdrawStressSignoff,
} from './hooks';
import type { EnterpriseStressRead, StressSignoffRead, StressSignoffStatus } from './types';

const STATUS_TONE: Record<StressSignoffStatus, StatusTone> = {
  draft: 'slate',
  pending_attestation: 'amber',
  attested: 'success',
  withdrawn: 'slate',
};
const STATUS_LABEL: Record<StressSignoffStatus, string> = {
  draft: 'Draft',
  pending_attestation: 'Pending Board attestation',
  attested: 'Board-attested',
  withdrawn: 'Withdrawn',
};

const inputCls =
  'w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-caption text-navy placeholder:text-slate-light focus:border-action focus:outline-none';
const btnCls =
  'inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-1.5 text-caption font-medium text-white hover:bg-action/90 disabled:opacity-50';
const btnGhost =
  'inline-flex items-center gap-1.5 rounded-md border border-border-light px-3 py-1.5 text-caption text-navy hover:bg-slate-50 disabled:opacity-50';

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

function Field({
  label,
  hint,
  value,
  onChange,
  readOnly,
  rows = 3,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  rows?: number;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-micro font-medium uppercase tracking-wide text-slate">{label}</span>
      {hint ? <span className="block text-micro text-slate-light">{hint}</span> : null}
      <textarea
        className={inputCls}
        rows={rows}
        value={value}
        readOnly={readOnly}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
      />
    </label>
  );
}

function CreateForm({ bankId, runId }: { bankId: string | undefined; runId: string }) {
  const create = useCreateStressSignoff(bankId);
  const [narrative, setNarrative] = useState('');
  const [rationale, setRationale] = useState('');
  const [methodology, setMethodology] = useState('');
  const [reason, setReason] = useState('');
  const canSubmit = narrative.trim() && rationale.trim() && reason.trim();
  return (
    <div className="space-y-3 p-5">
      <p className="text-caption text-slate">
        Record the sign-off for this run: the risks, exposures and macro assumptions covered, and
        the justification of the methodology and any expert-judgement overlays.
      </p>
      <Field
        label="Scenario narrative"
        hint="¶67(a)(b): risks / exposures / entities covered + macro conditions and assumptions."
        value={narrative}
        onChange={setNarrative}
      />
      <Field
        label="Assumptions rationale"
        hint="¶67(d), ¶45: methodologies and justification of expert-judgement overlays."
        value={rationale}
        onChange={setRationale}
      />
      <Field label="Methodology summary (optional)" value={methodology} onChange={setMethodology} rows={2} />
      <input
        className={inputCls}
        placeholder="Reason (audit)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {create.error ? <p className="text-caption text-critical">{errText(create.error)}</p> : null}
      <button
        type="button"
        className={btnCls}
        disabled={!canSubmit || create.isPending}
        onClick={() =>
          create.mutate({
            run_id: runId,
            scenario_narrative: narrative,
            assumptions_rationale: rationale,
            methodology_summary: methodology || null,
            reason,
          })
        }
      >
        <PenLine size={14} /> Create sign-off (draft)
      </button>
    </div>
  );
}

function AttestForm({ bankId, signoffId }: { bankId: string | undefined; signoffId: string }) {
  const attest = useAttestStressSignoff(bankId);
  const [credibility, setCredibility] = useState('');
  const [challenge, setChallenge] = useState('');
  const [reason, setReason] = useState('');
  const canAttest = credibility.trim() && reason.trim();
  return (
    <div className="space-y-3 rounded-md border border-border-light bg-slate-50 p-4">
      <p className="text-caption font-medium text-navy">Board attestation (¶20)</p>
      <p className="text-micro text-slate">
        A different user from the preparer must attest — the Board&apos;s rationale for the
        credibility of the framework and results, and its challenge record.
      </p>
      <Field
        label="Credibility rationale"
        hint="¶20: the Board's rationale for the credibility of the framework + results."
        value={credibility}
        onChange={setCredibility}
      />
      <Field label="Board challenge (optional)" value={challenge} onChange={setChallenge} rows={2} />
      <input
        className={inputCls}
        placeholder="Reason (audit)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {attest.error ? <p className="text-caption text-critical">{errText(attest.error)}</p> : null}
      <button
        type="button"
        className={btnCls}
        disabled={!canAttest || attest.isPending}
        onClick={() =>
          attest.mutate({
            signoffId,
            body: { credibility_rationale: credibility, board_challenge: challenge || null, reason },
          })
        }
      >
        <ShieldCheck size={14} /> Attest as Board
      </button>
    </div>
  );
}

function SignoffDetail({ bankId, signoffId }: { bankId: string | undefined; signoffId: string }) {
  const detail = useStressSignoff(bankId, signoffId);
  const submit = useSubmitStressSignoff(bankId);
  const withdraw = useWithdrawStressSignoff(bankId);
  const update = useUpdateStressSignoff(bankId);
  const [reason, setReason] = useState('');

  return (
    <QueryBoundary isLoading={detail.isLoading} error={detail.error} onRetry={() => detail.refetch()}>
      {detail.data ? (
        <SignoffBody
          s={detail.data}
          reason={reason}
          setReason={setReason}
          onSubmit={() => submit.mutate({ signoffId, body: { reason } })}
          onWithdraw={() => withdraw.mutate({ signoffId, body: { reason } })}
          onSaveDraft={(body) => update.mutate({ signoffId, body: { ...body, reason } })}
          busy={submit.isPending || withdraw.isPending || update.isPending}
          actionError={errText(submit.error ?? withdraw.error ?? update.error ?? '')}
        />
      ) : null}
    </QueryBoundary>
  );
}

function SignoffBody({
  s,
  reason,
  setReason,
  onSubmit,
  onWithdraw,
  onSaveDraft,
  busy,
  actionError,
}: {
  s: StressSignoffRead;
  reason: string;
  setReason: (v: string) => void;
  onSubmit: () => void;
  onWithdraw: () => void;
  onSaveDraft: (body: { scenario_narrative?: string; assumptions_rationale?: string; methodology_summary?: string | null }) => void;
  busy: boolean;
  actionError: string;
}) {
  const isDraft = s.status === 'draft';
  const [narrative, setNarrative] = useState(s.scenario_narrative);
  const [rationale, setRationale] = useState(s.assumptions_rationale);
  const [methodology, setMethodology] = useState(s.methodology_summary ?? '');

  return (
    <div className="space-y-4 p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusPill tone={STATUS_TONE[s.status]}>{STATUS_LABEL[s.status]}</StatusPill>
          <span className="text-micro text-slate-light">v{s.version} · {s.scenario_code}</span>
        </div>
        {s.attested_at ? (
          <span className="text-micro text-slate-light">attested {fmtDateUTC(new Date(s.attested_at))}</span>
        ) : null}
      </div>

      {s.status === 'attested' ? (
        <div className="rounded-md border border-border-light bg-slate-50 p-3 text-caption text-navy">
          This run is <strong>Board-attested</strong> and may be filed as the ICAAP Appendix II
          stress return. The governance record below is immutable.
        </div>
      ) : null}

      <Field label="Scenario narrative" value={isDraft ? narrative : s.scenario_narrative} onChange={isDraft ? setNarrative : undefined} readOnly={!isDraft} />
      <Field label="Assumptions rationale" value={isDraft ? rationale : s.assumptions_rationale} onChange={isDraft ? setRationale : undefined} readOnly={!isDraft} />
      <Field
        label="Methodology summary"
        value={isDraft ? methodology : s.methodology_summary ?? '—'}
        onChange={isDraft ? setMethodology : undefined}
        readOnly={!isDraft}
        rows={2}
      />

      {s.status === 'attested' || s.status === 'pending_attestation' ? (
        <>
          <Field label="Board credibility rationale" value={s.credibility_rationale ?? '—'} readOnly rows={2} />
          {s.board_challenge ? <Field label="Board challenge" value={s.board_challenge} readOnly rows={2} /> : null}
        </>
      ) : null}

      {s.status !== 'attested' ? (
        <input className={inputCls} placeholder="Reason (audit)" value={reason} onChange={(e) => setReason(e.target.value)} />
      ) : null}
      {actionError ? <p className="text-caption text-critical">{actionError}</p> : null}

      <div className="flex flex-wrap gap-2">
        {isDraft ? (
          <>
            <button
              type="button"
              className={btnGhost}
              disabled={busy || !reason.trim()}
              onClick={() => onSaveDraft({ scenario_narrative: narrative, assumptions_rationale: rationale, methodology_summary: methodology || null })}
            >
              Save draft
            </button>
            <button type="button" className={btnCls} disabled={busy || !reason.trim()} onClick={onSubmit}>
              <Send size={14} /> Submit for Board attestation
            </button>
          </>
        ) : null}
        {s.status === 'pending_attestation' ? (
          <button type="button" className={btnGhost} disabled={busy || !reason.trim()} onClick={onWithdraw}>
            Withdraw to draft
          </button>
        ) : null}
      </div>

      {s.status === 'pending_attestation' ? <AttestForm bankId={s.bank_id} signoffId={s.id} /> : null}
    </div>
  );
}

export default function SignoffPanel({
  run,
  bankId,
}: {
  run: EnterpriseStressRead | null;
  bankId: string | undefined;
}) {
  const signoffs = useBankStressSignoffs(bankId);

  if (!run) {
    return (
      <SectionCard title="Governance & sign-off" subtitle="Board attestation of the stress run (¶20)">
        <div className="p-5">
          <EmptyState
            Icon={ShieldCheck}
            title="Open a run to record its sign-off"
            description="Run an enterprise stress test and open it from the registry — its Board sign-off and attestation are captured here and gate the ICAAP stress return."
          />
        </div>
      </SectionCard>
    );
  }

  const existing = (signoffs.data ?? []).find((x) => x.run_id === run.run_id);

  return (
    <SectionCard
      title="Governance & sign-off"
      subtitle="Analyst/CRO narrative → Board attestation (maker ≠ checker) — gates the ICAAP return"
      noPadding
    >
      <QueryBoundary isLoading={signoffs.isLoading} error={signoffs.error} onRetry={() => signoffs.refetch()}>
        {existing ? (
          <SignoffDetail bankId={bankId} signoffId={existing.id} />
        ) : (
          <CreateForm bankId={bankId} runId={run.run_id} />
        )}
      </QueryBoundary>
    </SectionCard>
  );
}
