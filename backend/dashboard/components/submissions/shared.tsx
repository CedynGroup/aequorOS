'use client';

/**
 * Shared vocabulary for the Regulatory Reporting hub: family/status/RAG/
 * fidelity labels and tones, the return registry's deadline-rule wording and
 * template section lists (transcribed from the backend registry/templates —
 * display copy only, the API stays the source of truth for data), the demo
 * approver roster for the maker-checker affordance, and the tenant-header
 * artifact download helper (fetch + Blob — plain anchors cannot carry the
 * tenant headers).
 */

import type {
  ChannelCode,
  FidelityGrade,
  ObligationRag,
  PackageStatus,
} from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { apiBaseUrl } from '@/lib/api/client';
import { getAccessToken } from '@/lib/api/token';

// ---------------------------------------------------------------------------
// Labels + tones
// ---------------------------------------------------------------------------

export const FAMILY_LABELS: Record<string, string> = {
  liquidity: 'Liquidity',
  capital: 'Capital',
  irrbb: 'IRRBB',
  fx: 'FX',
  icaap_stress: 'ICAAP & Stress',
};

export const CHANNEL_LABELS: Record<ChannelCode, string> = {
  orass_sandbox: 'ORASS (sandbox)',
  email: 'Email fallback',
  manual: 'Manual record',
};

const PACKAGE_STATUS_TONES: Record<PackageStatus, StatusTone> = {
  draft: 'slate',
  generated: 'action',
  validated: 'action',
  pending_approval: 'amber',
  approved: 'success',
  submitted: 'action',
  acknowledged: 'success',
  rejected: 'critical',
  superseded: 'slate',
};

export const PACKAGE_STATUS_LABELS: Record<PackageStatus, string> = {
  draft: 'Draft',
  generated: 'Generated',
  validated: 'Validated',
  pending_approval: 'Pending approval',
  approved: 'Approved',
  submitted: 'Submitted',
  acknowledged: 'Acknowledged',
  rejected: 'Rejected',
  superseded: 'Superseded',
};

export function PackageStatusPill({ status }: { status: PackageStatus }) {
  return (
    <StatusPill tone={PACKAGE_STATUS_TONES[status] ?? 'pending'}>
      {PACKAGE_STATUS_LABELS[status] ?? status}
    </StatusPill>
  );
}

const RAG_TONES: Record<ObligationRag, StatusTone> = {
  overdue: 'critical',
  due_soon: 'amber',
  on_track: 'success',
};

export const RAG_LABELS: Record<ObligationRag, string> = {
  overdue: 'Overdue',
  due_soon: 'Due soon',
  on_track: 'On track',
};

export function RagPill({ rag }: { rag: ObligationRag }) {
  return <StatusPill tone={RAG_TONES[rag]}>{RAG_LABELS[rag]}</StatusPill>;
}

/** The honest one-liner per fidelity grade (spec §1 principle 4). */
export const FIDELITY_INFO: Record<
  FidelityGrade,
  { tone: StatusTone; blurb: string }
> = {
  CONFIRMED: {
    tone: 'success',
    blurb: 'Official appendix structure verified from the published directive.',
  },
  PARTIAL: {
    tone: 'amber',
    blurb:
      'Directive-described; the official appendix is not fully public — unpublished parameters follow Basel defaults.',
  },
  REPRESENTATIVE: {
    tone: 'slate',
    blurb:
      'Professional reconstruction — the official form is not public; nothing invented is passed off as official.',
  },
};

export function FidelityPill({ fidelity }: { fidelity: FidelityGrade }) {
  const info = FIDELITY_INFO[fidelity];
  return (
    <span title={info.blurb}>
      <StatusPill tone={info.tone}>{fidelity}</StatusPill>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Registry display copy (deadline rules + template sections) — transcribed
// from app/services/regulatory_reporting/{registry,templates}.py.
// ---------------------------------------------------------------------------

export const DEADLINE_RULE_TEXT: Record<string, string> = {
  BSD3: 'Monthly — due within 9 days after month end (LMTD Part II ¶7). The LCR deadline is assumed to match until the LCR Directive is published (research gap G1).',
  LMT: 'Monthly — due within 9 days after month end (LMTD Part II ¶7).',
  BSD2: 'Monthly — day 14 of the following month is a placeholder: the official CAR return deadline is UNKNOWN in the public record (research §2 row 7).',
  'IRRBB-PILOT':
    'Quarterly pilot — due within 9 days after quarter end (IRRBB Guideline ¶11, ¶55).',
  'FX-NOP':
    'Monthly summary registered by AequorOS — day 10 of the following month (placeholder). The confirmed BoG obligation is DAILY Bank Returns (DBK) by 10:00 a.m. the next business day via ORASS.',
  'ICAAP-STRESS':
    'Annual — due by 31 March of the ensuing year (ICAAP Guideline ¶72; Stress Testing Guideline ¶67).',
};

/** Section sheet titles per template id (display list for the Templates tab). */
export const TEMPLATE_SECTIONS: Record<string, string[]> = {
  'bog-bsd3-liquidity-v1': [
    'Stock of HQLA',
    'Cash Outflows (30 days)',
    'Cash Inflows (30 days)',
    'Liquidity Coverage Ratio Summary',
    'Available Stable Funding',
    'Required Stable Funding',
    'Net Stable Funding Ratio Summary',
  ],
  'bog-lmt-liquidity-v1': [
    'Stock of HQLA',
    'Cash Outflows (30 days)',
    'Cash Inflows (30 days)',
    'Liquidity Coverage Ratio Summary',
  ],
  'bog-bsd2-capital-v1': [
    'Common Equity Tier 1',
    'Additional Tier 1 Capital',
    'Tier 2 Capital',
    'Credit Risk-Weighted Assets',
    'Market Risk-Weighted Assets',
    'Operational Risk-Weighted Assets',
    'Capital Adequacy Ratios',
  ],
  'bog-irrbb-pilot-v1': [
    'Repricing Gap by Bucket',
    'ΔEVE by Supervisory Shock',
    'ΔNII / Earnings at Risk',
    'IRRBB Summary',
  ],
  'bog-fx-nop-v1': [
    'Net Open Position by Currency',
    'Standalone VaR by Currency',
    'Hedge Effectiveness',
    'NOP under Depreciation Scenarios',
    'Net Open Position Summary',
  ],
  'bog-icaap-stress-v1': [
    '5-Year Forecast Summary',
    'Projected Balance-Sheet Path',
    'Stress Scenario Outcomes',
  ],
};

// ---------------------------------------------------------------------------
// Act 930 penalty framing (research §5.1) — indicative only, no invented math
// beyond units × the GH¢12/unit statutory rate.
// ---------------------------------------------------------------------------

export const PENALTY_UNIT_GHS = 12;
export const PENALTY_BASE_UNITS = 500;
export const PENALTY_DAILY_UNITS = 50;
export const PENALTY_CITATION = 'Act 930 s.93(3)';
export const PENALTY_FOOTNOTE =
  `${PENALTY_CITATION}: non-submission, incomplete, delayed or inaccurate submission ` +
  `attracts up to ${PENALTY_BASE_UNITS} penalty units on the institution AND the responsible key ` +
  `management personnel, plus ${PENALTY_DAILY_UNITS} penalty units per day the default continues ` +
  `(GH¢${PENALTY_UNIT_GHS} per unit — Fines (Penalty Units) Act 572). Figures shown are indicative: units × rate.`;

/** Indicative running exposure for an overdue obligation: units × GH¢12. */
export function indicativePenaltyGhs(daysOverdue: number): {
  baseGhs: number;
  dailyGhs: number;
  runningGhs: number;
} {
  const baseGhs = PENALTY_BASE_UNITS * PENALTY_UNIT_GHS;
  const dailyGhs = PENALTY_DAILY_UNITS * PENALTY_UNIT_GHS;
  return { baseGhs, dailyGhs, runningGhs: dailyGhs * Math.max(daysOverdue, 0) };
}

// ---------------------------------------------------------------------------
// Demo approver roster — maker-checker affordance. Stable fixed UUIDs; the
// select is clearly labeled "Demo: acting as a second officer — production
// uses your login". The session user is included so the same-user 409 can be
// demonstrated verbatim.
// ---------------------------------------------------------------------------

export type DemoOfficer = { id: string; name: string; role: string };

export const DEMO_OFFICERS: DemoOfficer[] = [
  {
    id: 'bbbbbbbb-0000-4000-8000-000000000001',
    name: 'Kojo Aboagye',
    role: 'ALM Manager',
  },
  {
    id: 'bbbbbbbb-0000-4000-8000-000000000002',
    name: 'Yaa Adjei',
    role: 'Risk Officer',
  },
  {
    id: 'bbbbbbbb-0000-4000-8000-000000000003',
    name: 'Eric Inkoom Danso',
    role: 'CFO',
  },
];

/** Resolve a demo-roster or session user id to a display name. */
export function officerName(userId: string): string {
  const officer = DEMO_OFFICERS.find((entry) => entry.id === userId);
  if (officer) return `${officer.name} (${officer.role})`;
  return userId.slice(0, 8);
}

// ---------------------------------------------------------------------------
// Artifact download — tenant-scoped endpoint, so fetch the bytes and hand
// them to the browser as a Blob (precedent: market-data downloadTemplate).
// ---------------------------------------------------------------------------

export async function downloadArtifact(
  bankId: string,
  artifact: { id: string; objectPath: string }
): Promise<void> {
  const response = await fetch(
    `${apiBaseUrl}/banks/${bankId}/regulatory-artifacts/${artifact.id}/download`,
    { headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` } }
  );
  if (!response.ok) {
    throw new Error(`Artifact download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = artifact.objectPath.split('/').pop() ?? 'return-artifact';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** "12.3 KB" / "1.8 MB" for artifact rows. */
export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Deep link into the Returns workspace for one obligation/package. */
export function returnsHref(code: string, isoDate?: string): string {
  const params = new URLSearchParams({ code });
  if (isoDate) params.set('date', isoDate);
  return `/submissions/returns?${params.toString()}`;
}
