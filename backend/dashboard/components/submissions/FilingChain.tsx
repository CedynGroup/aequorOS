'use client';

/**
 * Where a return is, who holds it, every decision taken on it, and what happens
 * next (docs/filing_workflow_redesign.md §4b.4).
 *
 * This replaced the lifecycle stepper, which drew six fixed statuses that do not
 * correspond to people — and labelled one of them "Validated", which a bank
 * reads as "the Validator signed off" when it means the rules engine found no
 * errors. The stepper was the visible half of the whole problem.
 *
 * Collapsed it is ONE line — the chain as a sequence of officers, the current
 * holder marked — because that is all an officer checking figures needs while
 * they are checking figures. Expanded it is the record: every decision with its
 * round, its comment and its time, a send-back naming where it went, and the
 * transmission to the regulator stated in words before anyone is offered a
 * button for it.
 */

import { useState } from 'react';
import { Check, ChevronDown, CornerUpLeft, Send, XCircle } from 'lucide-react';
import type {
  AttestationStatusRead,
  PackageApprovalRead,
  PackageStatus,
  SubmissionEventRead,
} from '@aequoros/risk-service-api';
import { fmtTimestamp } from '@/lib/api/values';
import { centralBankName, regShort, submissionPortal } from '@/lib/format';
import {
  buildFilingChain,
  outcomeLabel,
  type ChainEntry,
  type ChainStage,
  type FilingChain,
  type RegulatorNaming,
} from '@/lib/submissions/filingChain';

/** The active jurisdiction's own words — never a literal in this package. */
export function activeRegulatorNaming(): RegulatorNaming {
  return {
    short: regShort(),
    full: centralBankName(),
    portal: submissionPortal(),
  };
}

const MARK_TONE: Record<ChainStage['state'], string> = {
  done: 'bg-success',
  current: 'bg-action ring-4 ring-action-light',
  ahead: 'bg-border',
  returned: 'bg-critical',
  closed: 'bg-border',
};

const NAME_TONE: Record<ChainStage['state'], string> = {
  done: 'text-navy/80',
  current: 'text-navy font-medium',
  ahead: 'text-slate-light',
  returned: 'text-critical',
  closed: 'text-slate-light',
};

function StageChip({ stage }: { stage: ChainStage }) {
  const holder = stage.holder
    ? `${stage.holder}${stage.holderTitle ? ` — ${stage.holderTitle}` : ''}`
    : 'No officer named for this stage yet';
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap"
      title={`${stage.title}: ${stage.roleName} ${stage.act}. ${holder}.`}
    >
      <span
        aria-hidden
        className={`inline-block h-2 w-2 rounded-full ${MARK_TONE[stage.state]}`}
      />
      <span className={`text-caption ${NAME_TONE[stage.state]}`}>
        {stage.roleName}
      </span>
    </span>
  );
}

function EntryRow({
  entry,
  regulator,
}: {
  entry: ChainEntry;
  regulator: RegulatorNaming;
}) {
  const returned = entry.outcome === 'returned' || entry.outcome === 'rejected';
  const declined = entry.outcome === 'declined';
  return (
    <li className="rounded border border-border-light bg-surface px-3 py-2">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="font-mono text-micro text-slate tnum whitespace-nowrap">
          {entry.at ? fmtTimestamp(entry.at) : 'Time not recorded'}
        </span>
        <span className="text-caption text-navy/85">
          {entry.actorName ?? 'Officer not named on the record'}
          {entry.actorTitle ? ` — ${entry.actorTitle}` : ''}
        </span>
        <span className="text-caption text-slate">· {entry.roleName} ·</span>
        <span className="text-caption text-slate">
          {entry.round !== null ? `round ${entry.round}` : 'round not recorded'}
        </span>
        <span
          className={`inline-flex items-center gap-1.5 text-caption font-medium ${
            declined || returned ? 'text-critical' : 'text-navy'
          }`}
        >
          {returned ? (
            <CornerUpLeft size={12} aria-hidden />
          ) : declined ? (
            <XCircle size={12} aria-hidden />
          ) : entry.outcome === 'filed' ? (
            <Send size={12} aria-hidden />
          ) : (
            <Check size={12} aria-hidden />
          )}
          {outcomeLabel(entry.outcome, regulator)}
        </span>
        {entry.returnedTo && (
          <span className="text-caption text-critical">
            to {entry.returnedTo}
          </span>
        )}
      </div>
      {entry.comment ? (
        <p className="mt-1 text-caption text-navy/85 leading-relaxed whitespace-pre-wrap">
          “{entry.comment}”
        </p>
      ) : returned ? (
        <p className="mt-1 text-caption text-slate">No comment was recorded.</p>
      ) : null}
    </li>
  );
}

/** The chain as a sequence: Preparer → Approver → Validator → the regulator. */
export function FilingChainStrip({
  chain,
  showRegulatorStage = false,
}: {
  chain: FilingChain;
  /**
   * The regulator is the end of the chain for the officer who transmits. For
   * everybody else it is a stage they never touch, and drawing it invites them
   * to think the channel is theirs.
   */
  showRegulatorStage?: boolean;
}) {
  const stages = showRegulatorStage
    ? chain.stages
    : chain.stages.filter((stage) => stage.key !== 'regulator');
  return (
    <div className="flex items-center gap-2 flex-wrap min-w-0">
      {stages.map((stage, index) => (
        <span key={stage.key} className="inline-flex items-center gap-2">
          {index > 0 && (
            <span aria-hidden className="text-slate-light">
              →
            </span>
          )}
          <StageChip stage={stage} />
        </span>
      ))}
    </div>
  );
}

/**
 * The chain, compact by default.
 *
 * `defaultOpen` exists for the officer whose turn it is: someone about to send a
 * return to a regulator should see the decisions behind it without asking.
 */
export default function FilingChainPanel({
  status,
  approvals,
  attestation,
  events,
  checksClean,
  resolveOfficer,
  heldByViewer = false,
  showRegulatorStage = false,
  defaultOpen = false,
}: {
  status: PackageStatus;
  approvals: readonly PackageApprovalRead[];
  attestation: AttestationStatusRead | null;
  events: readonly SubmissionEventRead[];
  checksClean: boolean;
  resolveOfficer: (actorUserId: string) => {
    name: string;
    title: string | null;
  } | null;
  /** This officer holds the stage the return is sitting at right now. */
  heldByViewer?: boolean;
  showRegulatorStage?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const regulator = activeRegulatorNaming();
  const chain = buildFilingChain({
    status,
    approvals,
    attestation,
    events,
    checksClean,
    regulator,
    resolveOfficer,
  });

  return (
    <section
      aria-label="Filing chain"
      data-testid="filing-chain"
      className="card px-5 py-3"
    >
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <FilingChainStrip
          chain={chain}
          showRegulatorStage={showRegulatorStage}
        />
        <div className="flex items-center gap-3 ml-auto">
          <span className="text-caption text-slate whitespace-nowrap">
            {heldByViewer ? 'Held by you' : chain.position}
            {chain.round !== null ? ` · round ${chain.round}` : ''}
          </span>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="shrink-0 inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-caption font-medium text-navy hover:bg-surface"
          >
            {open ? 'Hide history' : 'Show history'}
            <ChevronDown
              size={13}
              aria-hidden
              className={`transition-transform ${open ? 'rotate-180' : ''}`}
            />
          </button>
        </div>
      </div>

      <p className="mt-1.5 text-caption text-slate leading-relaxed">
        {chain.positionDetail}
      </p>

      {open && (
        <div className="mt-3 border-t border-border-light pt-3 space-y-3">
          {chain.entries.length === 0 ? (
            <p className="text-caption text-slate leading-relaxed">
              Nothing has been decided on this version yet. The first decision is
              the preparer certifying the figures.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {chain.entries.map((entry, index) => (
                <EntryRow
                  key={`${entry.outcome}-${entry.at?.getTime() ?? index}`}
                  entry={entry}
                  regulator={regulator}
                />
              ))}
            </ul>
          )}
          {chain.next && (
            <p className="rounded border border-border-light bg-surface px-3 py-2 text-caption text-navy/85 leading-relaxed">
              <span className="font-medium text-navy">What happens next.</span>{' '}
              {chain.next}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * The chain line for a surface that has only the package status to hand — the
 * ICAAP filing tab, which carries its own stage chain beside it.
 */
export function PackageChainStrip({
  status,
  checksClean,
}: {
  status: PackageStatus;
  checksClean: boolean;
}) {
  const chain = buildFilingChain({
    status,
    approvals: [],
    attestation: null,
    events: [],
    checksClean,
    regulator: activeRegulatorNaming(),
    resolveOfficer: () => null,
  });
  return (
    <div className="space-y-1.5">
      <FilingChainStrip chain={chain} />
      <p className="text-caption text-slate leading-relaxed">
        {chain.positionDetail}
      </p>
    </div>
  );
}
