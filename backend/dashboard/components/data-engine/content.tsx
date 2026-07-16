/**
 * Data Engine content module: integration registry for the console tabs,
 * batch-status explainers, and the warning-rule → operator-hint map.
 *
 * Kept as plain data so wording lives in one reviewable place instead of
 * being scattered through components.
 */

export type IntegrationStatus = 'connected' | 'pending' | 'planned';

export type Integration = {
  key: string;
  name: string;
  href: string;
  status: IntegrationStatus;
  statusLabel: string;
  description: string;
  /** source_system values whose ingestion stats belong to this integration */
  sourceSystems: string[];
};

export const INTEGRATIONS: Integration[] = [
  {
    key: 'excel-csv',
    name: 'Excel / CSV',
    href: '/data-engine/excel-csv',
    status: 'connected',
    statusLabel: 'Connected',
    description:
      'Workbook and CSV drops with mapping-driven translation, cell-level lineage, and validation gating.',
    sourceSystems: ['EXCEL_CSV'],
  },
  {
    key: 'api',
    name: 'API Push',
    href: '/data-engine/api',
    status: 'connected',
    statusLabel: 'Connected · programmatic',
    description:
      'Middleware POSTs JSON through the push endpoints (open → stage → commit) — same pipeline, gating, and lineage as file uploads.',
    sourceSystems: ['API_PUSH', 'API_GENERIC'],
  },
  {
    key: 't24',
    name: 'Temenos T24',
    href: '/data-engine/t24',
    status: 'pending',
    statusLabel: 'Pending partner access',
    description:
      'Native TAFJ API and post-COB batch integration. Adapter skeleton in place; T24 banks onboard via Excel/CSV or the Push API today.',
    sourceSystems: ['T24'],
  },
  {
    key: 'adapters',
    name: 'Finacle · FlexCube · DB-direct',
    href: '/data-engine/adapters',
    status: 'planned',
    statusLabel: 'Planned',
    description:
      'Phase 3 adapter portfolio. Every adapter implements the same contract and passes the same conformance suite before it ships.',
    sourceSystems: ['FINACLE', 'FLEXCUBE', 'DB_DIRECT'],
  },
];

/**
 * What each terminal batch status actually means. Surfaced as tooltips on
 * status pills and as the legend on batch detail — "accepted with warnings"
 * is routinely misread as a failure, so the wording is explicit about
 * persistence and participation in calculations.
 */
export const BATCH_STATUS_EXPLAINERS: Record<string, string> = {
  accepted: 'Every record passed validation and was persisted to the canonical model.',
  accepted_with_warnings:
    'The batch WAS accepted. Warning rows are persisted with a data-quality flag ' +
    '(e.g. unknown counterparty) and participate in calculations — warnings are ' +
    'not rejections. Only rows counted under Errors were rejected.',
  rejected:
    'A blocking validation failure rejected the whole batch — nothing from this source was persisted.',
  failed: 'The batch failed before validation completed (bad file, storage failure, …).',
};

/** One-line legend rendered next to record counts on batch detail. */
export const WARNING_COUNTS_LEGEND =
  'Warnings are persisted rows flagged for data quality (e.g. unknown counterparty) — ' +
  'they participate in calculations. Errors are rejected rows.';

/**
 * Actionable operator hints keyed by validation rule name. Rendered when a
 * batch carries warnings under the rule (listed or suppressed-by-volume).
 */
export const WARNING_RULE_HINTS: Record<string, string> = {
  structural_unknown_counterparty:
    'These rows reference counterparties the canonical model does not know yet. ' +
    'Ingest the counterparty master first (Sample Bank: 05_counterparties.csv), ' +
    'then re-upload this file — the flags resolve on the next ingestion.',
  maturity_not_before_as_of:
    'These rows matured on or before the as-of date. Confirm the position is ' +
    'genuinely outstanding or correct the maturity in the source and re-upload.',
  unusual_balance_change:
    'These balances moved sharply versus the prior generation. Verify the ' +
    'restatement is intended; no action is needed if the movement is real.',
};

export function warningRuleHint(rule: string): string | undefined {
  return WARNING_RULE_HINTS[rule];
}
