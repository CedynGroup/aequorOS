'use client';

/**
 * Verification panel — the five independent checks of
 * docs/attestation_esignature.md §3.5, each reported separately.
 *
 * Two display rules matter:
 *
 * 1. **Skipped is neutral, never failure.** A deployment with no signed-PDF
 *    requirement legitimately has nothing to validate for checks 1, 2 and 5.
 *    Rendering that as red would train operators to ignore red.
 * 2. **Every check shows its own verdict.** A single green badge over five
 *    checks hides which one carried the weight, and the whole point of §3.5 is
 *    that the checks are independent — a green overall verdict requires all five.
 *
 * The panel renders whatever checks the API returns: the labels below are for
 * ordering and phrasing only, and unknown keys fall through to the payload's own
 * name plus its `detail`. The backend is the source of truth for what was
 * actually checked; this file never asserts a check ran.
 *
 * `evidence.status` is where the service puts the tri-state, because the closed
 * response model exposes only a boolean `passed`
 * (app/services/attestation/verify.py). Rendering keys off that rather than off
 * `passed` alone is what keeps skipped from reading as failure.
 */

import type { ReactNode } from 'react';
import {
  CheckCircle2,
  Download,
  Link2Off,
  MinusCircle,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import type {
  VerificationCheckRead,
  VerificationReportRead,
} from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { isoDate, labelize } from '@/lib/api/values';
import { downloadTextFile } from '@/lib/download';
import { DigestChip, fmtSignatureTimestamp } from './shared';

type Outcome = 'passed' | 'failed' | 'skipped';

/**
 * The §3.5 order, mirroring the service's own `CHECK_ORDER`, so the panel reads
 * in the same sequence as the specification and the offline CLI report. Keys not
 * listed here render after these, in payload order — the API decides what ran.
 */
const CHECK_ORDER = [
  'pdf_signature',
  'inter_signature_tamper',
  'detached_attestation',
  'content_binding',
  'artifact_binding',
];

/** Human phrasing per check key; anything unknown falls back to `labelize`. */
const CHECK_LABELS: Record<string, string> = {
  pdf_signature: 'PDF cryptographic validity',
  inter_signature_tamper: 'Tamper between signatures',
  detached_attestation: 'Detached attestation',
  content_binding: 'Content binding',
  artifact_binding: 'Artifact binding',
};

/**
 * The response model carries only a boolean `passed`, so the service puts the
 * tri-state in `evidence.status` ('passed' | 'failed' | 'skipped'). Read
 * defensively — `evidence` is an untyped record — and treat the legacy shapes as
 * skipped too rather than mis-reporting a no-op check as a failure.
 */
function outcomeOf(check: VerificationCheckRead): Outcome {
  const evidence = check.evidence ?? {};
  if (evidence.status === 'skipped') return 'skipped';
  if (evidence.skipped === true) return 'skipped';
  if (evidence.applicable === false) return 'skipped';
  return check.passed ? 'passed' : 'failed';
}

const OUTCOME_TONES: Record<Outcome, StatusTone> = {
  passed: 'success',
  failed: 'critical',
  skipped: 'slate',
};

const OUTCOME_LABELS: Record<Outcome, string> = {
  passed: 'Pass',
  failed: 'Fail',
  skipped: 'Skipped',
};

const OUTCOME_ICONS: Record<Outcome, typeof CheckCircle2> = {
  passed: CheckCircle2,
  failed: XCircle,
  skipped: MinusCircle,
};

const OUTCOME_ICON_COLORS: Record<Outcome, string> = {
  passed: 'text-success',
  failed: 'text-critical',
  skipped: 'text-slate',
};

function orderedChecks(checks: VerificationCheckRead[]): VerificationCheckRead[] {
  const rank = (check: VerificationCheckRead) => {
    const index = CHECK_ORDER.indexOf(check.check);
    return index === -1 ? CHECK_ORDER.length : index;
  };
  return [...checks].sort((a, b) => rank(a) - rank(b));
}

export default function VerificationPanel({
  report,
}: {
  report: VerificationReportRead;
}) {
  const checks = orderedChecks(report.checks);
  const failed = checks.filter((check) => outcomeOf(check) === 'failed').length;
  const skipped = checks.filter((check) => outcomeOf(check) === 'skipped').length;
  const passed = checks.length - failed - skipped;

  return (
    <div className="space-y-3">
      <div
        className={`flex items-start gap-2.5 rounded border px-3.5 py-2.5 ${
          report.overallPassed
            ? 'border-success/25 bg-success-light/50'
            : 'border-critical/30 bg-critical-light/50'
        }`}
      >
        <ShieldCheck
          size={15}
          className={`shrink-0 mt-0.5 ${
            report.overallPassed ? 'text-success' : 'text-critical'
          }`}
          aria-hidden
        />
        <div className="min-w-0 text-body">
          <p className="font-medium text-navy">
            {report.overallPassed
              ? 'Every applicable check passed.'
              : 'Verification did not pass — at least one check failed.'}
          </p>
          <p className="mt-0.5 text-caption text-slate tnum">
            {passed} pass · {failed} fail · {skipped} skipped · verified{' '}
            {fmtSignatureTimestamp(report.verifiedAt)}
          </p>
          {skipped > 0 && (
            <p className="mt-1 text-caption text-navy/80 leading-relaxed">
              Skipped checks had nothing to validate — typically because this
              deployment does not require a signed PDF artifact. They are not
              failures and do not weaken the checks that did run.
            </p>
          )}
        </div>
      </div>

      <ul className="space-y-2">
        {checks.map((check) => (
          <CheckRow key={check.check} check={check} />
        ))}
      </ul>

      <ChainStatus
        chainOk={report.chainOk}
        chainBrokenAt={report.chainBrokenAt}
        signatureCount={report.signatures.length}
      />

      {/* The report as returned. Independent verifiability is the point of §3.5,
          so the operator keeps a copy rather than a screenshot of one. */}
      <button
        type="button"
        onClick={() =>
          downloadTextFile(
            `verification-${report.returnCode}-${isoDate(report.reportingDate)}.json`,
            JSON.stringify(report, null, 2),
            'application/json;charset=utf-8'
          )
        }
        className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
      >
        <Download size={13} aria-hidden />
        Download verification report (JSON)
      </button>
    </div>
  );
}

function CheckRow({ check }: { check: VerificationCheckRead }) {
  const outcome = outcomeOf(check);
  const Icon = OUTCOME_ICONS[outcome];
  const label = CHECK_LABELS[check.check] ?? labelize(check.check);
  const evidence = Object.entries(check.evidence ?? {});

  return (
    <li className="rounded border border-border-light bg-surface px-3.5 py-2.5">
      <div className="flex items-start gap-2.5">
        <Icon
          size={15}
          className={`shrink-0 mt-0.5 ${OUTCOME_ICON_COLORS[outcome]}`}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-body font-medium text-navy">{label}</p>
            <StatusPill tone={OUTCOME_TONES[outcome]}>
              {OUTCOME_LABELS[outcome]}
            </StatusPill>
            <code className="ml-auto font-mono text-micro text-slate">
              {check.check}
            </code>
          </div>
          <p className="mt-1 text-caption text-navy/85 leading-relaxed">
            {check.detail}
          </p>
          {evidence.length > 0 && (
            <dl className="mt-1.5 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
              {evidence.map(([key, value]) => (
                <div key={key} className="min-w-0 flex items-baseline gap-1.5">
                  <dt className="text-micro font-medium uppercase tracking-wider text-slate shrink-0">
                    {labelize(key)}
                  </dt>
                  <dd className="min-w-0 text-micro text-navy/85 break-all">
                    <EvidenceValue value={value} label={`${check.check} ${key}`} />
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    </li>
  );
}

/**
 * Evidence is an untyped record. Hash-shaped strings become copyable digest
 * chips (an examiner needs the whole value); everything else renders as text.
 */
function EvidenceValue({ value, label }: { value: unknown; label: string }): ReactNode {
  if (value === null || value === undefined) return <span className="text-slate">—</span>;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return <span className="font-mono tnum">{value}</span>;
  if (typeof value === 'string') {
    if (/^[0-9a-f]{32,}$/i.test(value)) {
      return <DigestChip digest={value} label={label} />;
    }
    return value;
  }
  return (
    <code className="font-mono text-micro">{JSON.stringify(value)}</code>
  );
}

/**
 * The per-tenant hash chain over the signature rows (§3.6). A broken chain is
 * reported with the offending row, because "somewhere" is not evidence.
 */
function ChainStatus({
  chainOk,
  chainBrokenAt,
  signatureCount,
}: {
  chainOk: boolean;
  chainBrokenAt: string | null;
  signatureCount: number;
}) {
  return (
    <div
      className={`flex items-start gap-2.5 rounded border px-3.5 py-2.5 ${
        chainOk
          ? 'border-border-light bg-surface'
          : 'border-critical/30 bg-critical-light/50'
      }`}
    >
      {chainOk ? (
        <ShieldCheck size={15} className="text-success shrink-0 mt-0.5" aria-hidden />
      ) : (
        <Link2Off size={15} className="text-critical shrink-0 mt-0.5" aria-hidden />
      )}
      <div className="min-w-0 text-body">
        <p className="font-medium text-navy">
          {chainOk
            ? 'Signature hash chain intact'
            : 'Signature hash chain is broken'}
        </p>
        <p className="mt-0.5 text-caption text-navy/80 leading-relaxed">
          {chainOk
            ? `Every signature row (${signatureCount}) links to its predecessor by hash, so an insertion, deletion or edit anywhere in the tenant's signature history would show here.`
            : 'A row does not link to its predecessor — the append-only history has been tampered with or was written out of order. Escalate: this is an integrity failure, not a data-entry problem.'}
        </p>
        {!chainOk && chainBrokenAt && (
          <p className="mt-1 font-mono text-micro text-critical break-all">
            first broken row: {chainBrokenAt}
          </p>
        )}
      </div>
    </div>
  );
}
