import { CheckCircle2, AlertTriangle, XCircle, Info, MinusCircle } from 'lucide-react';
import { labelize, severityTone } from '@/lib/api/values';
import StatusPill from './StatusPill';

export type ValidationItem = {
  ruleCode: string;
  passed: boolean;
  severity: 'error' | 'warning' | 'info' | string;
  message: string;
  /**
   * `false` when the rule's threshold does not resolve from the institution's
   * governed parameter set, so the comparison cannot be made for the current
   * period whatever a stored run once recorded (NEW-53). Such a row renders
   * "Not assessed" — never a pass, never a breach. Omitted (`undefined`) means
   * the backend evaluated the rule normally, which is every other caller.
   */
  assessed?: boolean;
};

function ValidationIcon({ item }: { item: ValidationItem }) {
  if (item.assessed === false) {
    return <MinusCircle size={15} className="text-slate shrink-0 mt-0.5" aria-hidden />;
  }
  if (item.passed) {
    return (
      <CheckCircle2 size={15} className="text-success shrink-0 mt-0.5" aria-hidden />
    );
  }
  if (item.severity === 'error') {
    return <XCircle size={15} className="text-critical shrink-0 mt-0.5" aria-hidden />;
  }
  if (item.severity === 'warning') {
    return (
      <AlertTriangle size={15} className="text-warning shrink-0 mt-0.5" aria-hidden />
    );
  }
  return <Info size={15} className="text-slate shrink-0 mt-0.5" aria-hidden />;
}

/** Regulatory rule evaluations for the selected period / run. */
export default function ValidationList({
  validations,
}: {
  validations: ValidationItem[];
}) {
  if (!validations.length) {
    return (
      <p className="px-5 py-4 text-body text-slate">
        No validation rules evaluated for this period.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border-light">
      {validations.map((v) => (
        <li key={v.ruleCode} className="px-5 py-3.5 flex items-start gap-3">
          <ValidationIcon item={v} />
          <div className="min-w-0 flex-1">
            <p className="text-body font-medium text-navy">
              {labelize(v.ruleCode)}
            </p>
            <p className="mt-0.5 text-caption text-slate leading-relaxed">
              {v.message}
            </p>
          </div>
          <StatusPill
            tone={
              v.assessed === false
                ? 'pending'
                : v.passed
                ? 'success'
                : severityTone(v.severity)
            }
            className="shrink-0"
          >
            {v.assessed === false
              ? 'Not assessed'
              : v.passed
              ? 'Pass'
              : labelize(v.severity)}
          </StatusPill>
        </li>
      ))}
    </ul>
  );
}
