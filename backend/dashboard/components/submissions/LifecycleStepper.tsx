/**
 * Package lifecycle stepper: draft → generated → validated → pending approval
 * → approved → submitted → acknowledged. Rejected/superseded render as a
 * terminal badge beside the last reached step (spec §2 lifecycle).
 */

import { Check, XCircle, History } from 'lucide-react';
import type { PackageStatus } from '@aequoros/risk-service-api';

const STEPS: { status: PackageStatus; label: string }[] = [
  { status: 'generated', label: 'Generated' },
  { status: 'validated', label: 'Validated' },
  { status: 'pending_approval', label: 'Approval' },
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
    status === 'rejected' ? 'rejected' : status === 'superseded' ? 'superseded' : null;
  // A rejected package fell out of the submitted stage; superseded is history.
  const reached = terminal
    ? status === 'rejected'
      ? 4
      : -1
    : STEP_INDEX[status] ?? -1;

  return (
    <div className="flex items-center gap-0 flex-wrap" aria-label="Package lifecycle">
      {STEPS.map((step, i) => {
        const done = i < reached;
        const current = i === reached && !terminal;
        return (
          <div key={step.status} className="flex items-center">
            {i > 0 && (
              <div
                aria-hidden
                className={`h-px w-6 ${i <= reached ? 'bg-action' : 'bg-border'}`}
              />
            )}
            <div className="flex items-center gap-1.5 px-1">
              <span
                aria-hidden
                className={`inline-flex items-center justify-center w-4.5 h-4.5 w-[18px] h-[18px] rounded-full border text-[9px] font-mono ${
                  done
                    ? 'bg-action text-white border-action'
                    : current
                    ? 'border-action text-action bg-action-light'
                    : 'border-border text-slate bg-surface'
                }`}
              >
                {done ? <Check size={10} /> : i + 1}
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
      {terminal === 'superseded' && (
        <span className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium uppercase tracking-wider border bg-surface text-slate border-border">
          <History size={11} aria-hidden />
          Superseded
        </span>
      )}
    </div>
  );
}
