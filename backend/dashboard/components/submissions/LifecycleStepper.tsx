/**
 * Package lifecycle stepper: draft → generated → validated → pending approval
 * → approved → submitted → acknowledged. Rejected/declined/superseded render
 * as a terminal badge beside the last reached step (spec §2 lifecycle);
 * declined is the regulator's final refusal — treated like rejected, it drops
 * at the submitted step.
 */

import { Check, XCircle, Ban, History } from 'lucide-react';
import type { PackageStatus } from '@aequoros/risk-service-api';

// "Approval" and "Approved" sat next to each other and read as the same word, so
// a package resting in the approved state looked stuck at the approval step.
// The waiting stage now says it is waiting.
const STEPS: { status: PackageStatus; label: string }[] = [
  { status: 'generated', label: 'Generated' },
  { status: 'validated', label: 'Validated' },
  { status: 'pending_approval', label: 'Awaiting approval' },
  { status: 'approved', label: 'Approved' },
  { status: 'submitted', label: 'Submitted' },
  { status: 'acknowledged', label: 'Acknowledged' },
];

const STEP_INDEX: Partial<Record<PackageStatus, number>> = {
  draft: -1,
  generated: 0,
  validated: 1,
  pending_approval: 2,
  approved: 3,
  submitted: 4,
  acknowledged: 5,
};

export default function LifecycleStepper({ status }: { status: PackageStatus }) {
  const terminal =
    status === 'rejected' || status === 'declined' || status === 'superseded'
      ? status
      : null;
  // A rejected/declined package fell out of the submitted stage; superseded
  // is history.
  const reached = terminal
    ? terminal === 'superseded'
      ? -1
      : 4
    : STEP_INDEX[status] ?? -1;

  return (
    <div className="flex items-center gap-0 flex-wrap w-full" aria-label="Package lifecycle">
      {STEPS.map((step, i) => {
        // These are states REACHED, not activities in progress: the state a
        // package is in has been achieved, so it ticks. The highlight moves to
        // the step that has not happened yet, which is the one someone has to
        // act on — otherwise "approved" looked identical to "stuck at approval".
        // A rejected package still reached every stage up to submission, so
        // `done` must not be suppressed for terminal states — only the
        // "what's next" highlight is, since a terminal package has no next.
        const done = i <= reached;
        const current = i === reached + 1 && !terminal && reached >= 0;
        return (
          <div key={step.status} className="flex items-center">
            {i > 0 && (
              <div
                aria-hidden
                className={`h-px flex-1 min-w-[26px] ${
                  i <= reached ? 'bg-success/50' : 'bg-border'
                }`}
              />
            )}
            <div className="flex items-center gap-1.5 px-1">
              <span
                aria-hidden
                className={`inline-flex items-center justify-center w-6 h-6 rounded-full border text-micro font-mono ${
                  done
                    ? 'bg-success-light text-success border-success/40'
                    : current
                    ? 'bg-action text-white border-action ring-4 ring-action-light'
                    : 'border-border text-slate bg-surface'
                }`}
              >
                {done ? <Check size={12} /> : i + 1}
              </span>
              <span
                className={`text-caption whitespace-nowrap ${
                  current
                    ? 'font-medium text-navy'
                    : done
                    ? 'text-navy/80'
                    : 'text-slate'
                }`}
              >
                {step.label}
              </span>
            </div>
          </div>
        );
      })}
      {terminal === 'rejected' && (
        <span className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium uppercase tracking-wider border bg-critical-light text-critical border-critical/20">
          <XCircle size={11} aria-hidden />
          Rejected
        </span>
      )}
      {terminal === 'declined' && (
        <span className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium uppercase tracking-wider border bg-critical-light text-critical border-critical/20">
          <Ban size={11} aria-hidden />
          Declined
        </span>
      )}
      {terminal === 'superseded' && (
        <span className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium uppercase tracking-wider border bg-surface text-slate border-border">
          <History size={11} aria-hidden />
          Superseded
        </span>
      )}
    </div>
  );
}
