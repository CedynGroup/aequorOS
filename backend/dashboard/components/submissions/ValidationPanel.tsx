/**
 * Validation report panel: severity counts + the findings table. ERROR
 * findings block the approval request — the workspace disables the button
 * and this panel explains why.
 */

import { ShieldAlert, ShieldCheck } from 'lucide-react';
import type {
  ValidationFindingRead,
  ValidationReportRead,
  ValidationSeverity,
} from '@aequoros/risk-service-api';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { fmtTimestamp } from '@/lib/api/values';

const SEVERITY_TONES: Record<ValidationSeverity, StatusTone> = {
  ERROR: 'critical',
  WARNING: 'amber',
  INFO: 'slate',
};

const SEVERITY_ORDER: Record<ValidationSeverity, number> = {
  ERROR: 0,
  WARNING: 1,
  INFO: 2,
};

const columns: Column<ValidationFindingRead>[] = [
  {
    key: 'severity',
    header: 'Severity',
    width: '110px',
    render: (finding) => (
      <StatusPill tone={SEVERITY_TONES[finding.severity]}>
        {finding.severity}
      </StatusPill>
    ),
  },
  {
    key: 'rule',
    header: 'Rule',
    width: '220px',
    render: (finding) => (
      <span className="font-mono text-caption text-slate">{finding.rule}</span>
    ),
  },
  {
    key: 'detail',
    header: 'Detail',
    render: (finding) => (
      <span className="text-navy/90 leading-relaxed">{finding.detail}</span>
    ),
  },
];

export default function ValidationPanel({
  report,
}: {
  report: ValidationReportRead;
}) {
  const findings = [...report.findings].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]
  );
  const blocked = report.errorCount > 0 || !report.passed;

  return (
    <div className="space-y-3">
      <div
        className={`flex items-start gap-2.5 rounded border px-3.5 py-2.5 ${
          blocked
            ? 'border-critical/25 bg-critical-light/50'
            : 'border-success/25 bg-success-light/50'
        }`}
      >
        {blocked ? (
          <ShieldAlert size={15} className="text-critical shrink-0 mt-0.5" aria-hidden />
        ) : (
          <ShieldCheck size={15} className="text-success shrink-0 mt-0.5" aria-hidden />
        )}
        <div className="min-w-0 text-body">
          <p className="font-medium text-navy">
            {blocked
              ? 'Validation failed — ERROR findings block the approval request.'
              : 'Validation passed.'}
          </p>
          <p className="mt-0.5 text-caption text-slate tnum">
            {report.errorCount} error · {report.warningCount} warning ·{' '}
            {report.infoCount} info · rules {report.ruleVersion} · validated{' '}
            {fmtTimestamp(report.validatedAt)}
          </p>
          {blocked && (
            <p className="mt-1 text-caption text-navy/80">
              Resolve the ERROR findings (typically by regenerating from fresh
              source runs), then re-validate before requesting approval.
            </p>
          )}
        </div>
      </div>

      {findings.length > 0 ? (
        <div className="rounded border border-border-light overflow-hidden">
          <DataTable columns={columns} rows={findings} density="compact" />
        </div>
      ) : (
        <p className="text-caption text-slate">
          No findings — every completeness, cross-foot and movement check passed.
        </p>
      )}
    </div>
  );
}
