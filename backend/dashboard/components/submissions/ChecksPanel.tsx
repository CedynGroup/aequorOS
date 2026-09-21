/**
 * The machine checks on a generated return, and what they found.
 *
 * This is not a lifecycle step and it is not a person's decision. The checks run
 * with generation; what a preparer does with them is CLEAR them. So this panel
 * names every failing rule and what it failed on, and the word "Validated" does
 * not appear anywhere in it — that word belongs to the Validator, the officer
 * who files the return (docs/filing_workflow_redesign.md §3.1, §4b.2).
 *
 * Shared with the ICAAP filing workspace, which already used this vocabulary
 * ("Checks on the sealed report", "Run the checks"). One vocabulary for one
 * thing.
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

/**
 * What each severity MEANS to the officer reading it. `ERROR` / `WARNING` /
 * `INFO` are the engine's words; a Head of Finance reads what they have to do.
 */
const SEVERITY_LABELS: Record<ValidationSeverity, string> = {
  ERROR: 'Must fix',
  WARNING: 'Warning',
  INFO: 'Note',
};

const SEVERITY_ORDER: Record<ValidationSeverity, number> = {
  ERROR: 0,
  WARNING: 1,
  INFO: 2,
};

const columns: Column<ValidationFindingRead>[] = [
  {
    key: 'severity',
    header: 'Result',
    width: '116px',
    render: (finding) => (
      <StatusPill tone={SEVERITY_TONES[finding.severity]}>
        {SEVERITY_LABELS[finding.severity] ?? finding.severity}
      </StatusPill>
    ),
  },
  {
    key: 'rule',
    header: 'Check',
    width: '220px',
    render: (finding) => (
      // The rule's own name, verbatim: it is the reference a preparer quotes
      // when they ask why a line failed, the same way a return line is quoted.
      <span className="font-mono text-caption text-slate">{finding.rule}</span>
    ),
  },
  {
    key: 'detail',
    header: 'What it found',
    render: (finding) => (
      <span className="text-navy/90 leading-relaxed">{finding.detail}</span>
    ),
  },
];

/** "3 must fix · 1 warning · 2 notes" — the counts, as a sentence fragment. */
export function checkCountSummary(report: ValidationReportRead): string {
  const parts: string[] = [];
  if (report.errorCount > 0) parts.push(`${report.errorCount} to fix`);
  if (report.warningCount > 0) {
    parts.push(
      `${report.warningCount} ${report.warningCount === 1 ? 'warning' : 'warnings'}`
    );
  }
  if (report.infoCount > 0) {
    parts.push(`${report.infoCount} ${report.infoCount === 1 ? 'note' : 'notes'}`);
  }
  return parts.length > 0 ? parts.join(' · ') : 'nothing outstanding';
}

export default function ChecksPanel({
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
              ? `${report.errorCount} ${
                  report.errorCount === 1 ? 'check' : 'checks'
                } to clear before these figures can be certified.`
              : 'Every check passed.'}
          </p>
          <p className="mt-0.5 text-caption text-slate tnum">
            {checkCountSummary(report)} · rule set {report.ruleVersion} · last run{' '}
            {fmtTimestamp(report.validatedAt)}
          </p>
          {blocked && (
            <p className="mt-1 text-caption text-navy/80 leading-relaxed">
              Each one names the rule and what it found. Correct the source
              figures, generate a new version, and the checks run again with it.
            </p>
          )}
        </div>
      </div>

      {findings.length > 0 ? (
        <div className="rounded border border-border-light overflow-hidden">
          <DataTable
            columns={columns}
            rows={findings}
            density="compact"
            scrollLabel="Check findings"
          />
        </div>
      ) : (
        <p className="text-caption text-slate">
          Nothing to clear — every completeness, cross-foot and movement check
          passed.
        </p>
      )}
    </div>
  );
}
