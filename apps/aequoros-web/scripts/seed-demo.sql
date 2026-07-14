-- Repeatable AequorOS web demo seed.
-- Usage:
--   RISK_DEMO_DATABASE_URL=postgresql://risk_service_app:risk_service_app@localhost:15432/risk_service \
--     pnpm --filter @aequoros/aequoros-web seed:demo

\set org_id '11111111-1111-4111-8111-111111111111'
\set user_id 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
\set org_2 '22222222-2222-4222-8222-222222222222'
\set user_2 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
\set case_1 '90000000-0000-4000-8000-000000000001'
\set case_2 '90000000-0000-4000-8000-000000000002'
\set case_3 '90000000-0000-4000-8000-000000000003'

insert into organizations (id, name, created_at, updated_at)
values
  (:'org_id', 'AequorOS Demo Organization', now(), now()),
  (:'org_2', 'AequorOS Isolated Tenant', now(), now())
on conflict (id) do update set name = excluded.name, updated_at = now();

insert into users (id, organization_id, email, display_name, is_active, created_at, updated_at)
values
  (:'user_id', :'org_id', 'demo.user.one@example.test', 'Demo User One', true, now(), now()),
  (:'user_2', :'org_2', 'demo.user.two@example.test', 'Demo User Two', true, now(), now())
on conflict (id) do update
set organization_id = excluded.organization_id,
    email = excluded.email,
    display_name = excluded.display_name,
    is_active = true,
    updated_at = now();

delete from risk_case_decisions where case_id in (:'case_1', :'case_2', :'case_3');
delete from risk_findings where case_id in (:'case_1', :'case_2', :'case_3');

insert into risk_cases (
  id,
  organization_id,
  title,
  case_type,
  subject_type,
  subject_name,
  description,
  status,
  assigned_to_user_id,
  assigned_at,
  risk_score,
  risk_level,
  scored_at,
  scoring_version,
  decision,
  decided_at,
  metadata,
  created_by,
  created_at,
  updated_at
) values
(
  :'case_1',
  :'org_id',
  'Covenant review - Northstar Foods',
  'financial_statement_review',
  'borrower',
  'Northstar Foods',
  'Review of Q1 liquidity package, revolving credit usage, and covenant compliance ahead of renewal committee.',
  'completed',
  :'user_id',
  now() - interval '3 days',
  82,
  'high',
  now() - interval '3 hours',
  'demo-risk-v1',
  'approved',
  now() - interval '45 minutes',
  '{"industry":"food distribution","portfolio":"middle-market lending"}',
  :'user_id',
  now() - interval '3 days',
  now() - interval '45 minutes'
),
(
  :'case_2',
  :'org_id',
  'Quarterly liquidity packet - Mariner Trust',
  'financial_statement_review',
  'borrower',
  'Mariner Trust',
  'Follow-up review for liquidity covenant support and updated management commentary.',
  'in_review',
  :'user_id',
  now() - interval '2 days',
  57,
  'medium',
  now() - interval '2 hours',
  'demo-risk-v1',
  'needs_more_info',
  now() - interval '2 hours',
  '{"portfolio":"wealth credit"}',
  :'user_id',
  now() - interval '2 days',
  now() - interval '2 hours'
),
(
  :'case_3',
  :'org_id',
  'Exception review - Cedar Lending',
  'financial_statement_review',
  'borrower',
  'Cedar Lending',
  'Escalated exception review for collateral concentration and rapidly changing cash position.',
  'draft',
  :'user_id',
  now(),
  94,
  'critical',
  now() - interval '20 minutes',
  'demo-risk-v1',
  'escalated',
  now() - interval '10 minutes',
  '{"portfolio":"special assets"}',
  :'user_id',
  now() - interval '1 day',
  now()
)
on conflict (id) do update
set organization_id = excluded.organization_id,
    title = excluded.title,
    case_type = excluded.case_type,
    subject_type = excluded.subject_type,
    subject_name = excluded.subject_name,
    description = excluded.description,
    status = excluded.status,
    assigned_to_user_id = excluded.assigned_to_user_id,
    assigned_at = excluded.assigned_at,
    risk_score = excluded.risk_score,
    risk_level = excluded.risk_level,
    scored_at = excluded.scored_at,
    scoring_version = excluded.scoring_version,
    decision = excluded.decision,
    decided_at = excluded.decided_at,
    metadata = excluded.metadata,
    created_by = excluded.created_by,
    updated_at = excluded.updated_at;

insert into financial_balances (
  id, organization_id, case_id, dedupe_key, balance_type, amount, currency,
  as_of_date, metadata, created_at, updated_at
) values
(
  '94000000-0000-4000-8000-000000000001', :'org_id', :'case_1',
  'demo:forecast:cash', 'cash', 1250000, 'USD', DATE '2026-06-30',
  '{"source":"demo forecast seed"}', now(), now()
),
(
  '94000000-0000-4000-8000-000000000002', :'org_id', :'case_1',
  'demo:forecast:receivables', 'receivables', 2750000, 'USD', DATE '2026-06-30',
  '{"source":"demo forecast seed"}', now(), now()
),
(
  '94000000-0000-4000-8000-000000000003', :'org_id', :'case_1',
  'demo:forecast:payables', 'payables', 900000, 'USD', DATE '2026-06-30',
  '{"source":"demo forecast seed"}', now(), now()
)
on conflict (dedupe_key, organization_id, case_id) do update
set balance_type = excluded.balance_type,
    amount = excluded.amount,
    currency = excluded.currency,
    as_of_date = excluded.as_of_date,
    metadata = excluded.metadata,
    updated_at = now();

insert into financial_cash_flows (
  id, organization_id, case_id, dedupe_key, cash_flow_date, amount, currency,
  direction, category, metadata, created_at, updated_at
) values
(
  '95000000-0000-4000-8000-000000000001', :'org_id', :'case_1',
  'demo:forecast:operating-inflow', DATE '2026-06-30', 1800000, 'USD',
  'inflow', 'operating receipts', '{"source":"demo forecast seed"}', now(), now()
),
(
  '95000000-0000-4000-8000-000000000002', :'org_id', :'case_1',
  'demo:forecast:operating-outflow', DATE '2026-06-30', 1350000, 'USD',
  'outflow', 'operating expenses', '{"source":"demo forecast seed"}', now(), now()
)
on conflict (dedupe_key, organization_id, case_id) do update
set cash_flow_date = excluded.cash_flow_date,
    amount = excluded.amount,
    currency = excluded.currency,
    direction = excluded.direction,
    category = excluded.category,
    metadata = excluded.metadata,
    updated_at = now();

insert into financial_obligations (
  id, organization_id, case_id, dedupe_key, obligation_type, facility_type,
  principal_amount, outstanding_amount, currency, status, details, created_at, updated_at
) values
(
  '96000000-0000-4000-8000-000000000001', :'org_id', :'case_1',
  'demo:forecast:revolver', 'credit_facility', 'revolver', 2000000, 1000000,
  'USD', 'active', '{"source":"demo forecast seed"}', now(), now()
)
on conflict (dedupe_key, organization_id, case_id) do update
set obligation_type = excluded.obligation_type,
    facility_type = excluded.facility_type,
    principal_amount = excluded.principal_amount,
    outstanding_amount = excluded.outstanding_amount,
    currency = excluded.currency,
    status = excluded.status,
    details = excluded.details,
    updated_at = now();

insert into risk_case_decisions (
  id,
  organization_id,
  case_id,
  decision,
  previous_decision,
  reason,
  decided_by,
  created_at
) values
(
  '91000000-0000-4000-8000-000000000001',
  :'org_id',
  :'case_1',
  'needs_more_info',
  null,
  'Initial package missing borrowing base support for two large customer concentrations.',
  :'user_id',
  now() - interval '1 day'
),
(
  '91000000-0000-4000-8000-000000000002',
  :'org_id',
  :'case_1',
  'approved',
  'needs_more_info',
  'Supplemental AR aging and cash reconciliation received; remaining issue accepted for covenant monitoring.',
  :'user_id',
  now() - interval '45 minutes'
);

insert into risk_findings (
  id,
  organization_id,
  case_id,
  risk_type,
  title,
  summary,
  rationale,
  severity,
  status,
  disposition_reason,
  source,
  rule_id,
  score_impact,
  details,
  created_at,
  updated_at
) values
(
  '92000000-0000-4000-8000-000000000001',
  :'org_id',
  :'case_1',
  'liquidity_gap',
  'Cash conversion cycle widened',
  'Receivables increased faster than revenue and reduced short-term liquidity headroom.',
  'AR days moved from 42 to 58 while revolver utilization remained elevated.',
  'high',
  'accepted',
  'Accepted for covenant monitoring after updated support was received.',
  'manual',
  'liquidity_cash_conversion',
  24,
  '{"metric":"AR days","prior":42,"current":58}',
  now() - interval '1 day',
  now() - interval '45 minutes'
),
(
  '92000000-0000-4000-8000-000000000002',
  :'org_id',
  :'case_1',
  'documentation_gap',
  'Borrowing base support updated',
  'Initial support was stale; refreshed schedule received before approval.',
  'The final decision references the updated AR aging package.',
  'medium',
  'resolved',
  'Updated support received.',
  'manual',
  'borrowing_base_support',
  8,
  '{"document":"AR aging package","status":"received"}',
  now() - interval '1 day',
  now() - interval '45 minutes'
),
(
  '92000000-0000-4000-8000-000000000003',
  :'org_id',
  :'case_2',
  'documentation_gap',
  'Management commentary missing',
  'Reviewer needs current-quarter liquidity commentary before final decision.',
  null,
  'medium',
  'open',
  null,
  'manual',
  'required_documents',
  12,
  '{"missing":"management commentary"}',
  now() - interval '2 hours',
  now() - interval '2 hours'
);
