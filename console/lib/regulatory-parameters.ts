/**
 * Pure decision logic behind /admin/regulatory-parameters.
 *
 * Kept free of React and of any runtime import (the only import is a TYPE, so
 * it is elided at emit) so it can be compiled and run under node by
 * `pnpm --filter @aequoros/console test` — the console's whole test surface.
 * Everything here is a pure function of its arguments.
 *
 * Four rules from the control-plane brief are enforced HERE rather than in the
 * view, because that is where they can be tested:
 *
 * 1. **Decimals stay strings.** A regulatory figure is never parsed into a JS
 *    `number` on the read or the write path — `Number('0.1')` is already lossy
 *    and a CAR floor is a filing input. `isDecimalValue` validates by SHAPE
 *    (regex), never by coercion.
 * 2. **Fail closed.** A value that is absent renders as an explicit "Not set",
 *    never as `0` and never as a blank that reads like zero.
 * 3. **Four-eyes is legible before it is enforced.** `approvalEligibility`
 *    tells the operator they proposed the row *before* they click approve. It
 *    is a courtesy, never the control: the server re-decides on every call
 *    (`app/operator/services/regulatory_parameters.py::approve` 422s when the
 *    approver is the proposer). When the viewer's identity is unknown we say
 *    so and let the server answer — we never assume eligibility.
 * 4. **Jurisdiction is data.** No country, currency, or regulator name appears
 *    in any label below; they are read off the record.
 */

import type { RegulatoryParameter } from './api';

// ---------------------------------------------------------------------------
// Display vocabulary — production copy, never a raw enum token
// ---------------------------------------------------------------------------

/** Structurally identical to the Chip primitive's `Tone`, without importing JSX. */
export type ToneName = 'ok' | 'warn' | 'crit' | 'accent' | 'neutral';

/**
 * The scope axis a parameter keys on (backend `SCOPE_TYPES`). `institution_class`
 * keys on the coarse class, `institution_type` on the specific licence code.
 */
export function scopeTypeLabel(scopeType: RegulatoryParameter['scope_type']): string {
  return scopeType === 'institution_class' ? 'Institution class' : 'Licence type';
}

export function confirmationLabel(
  status: RegulatoryParameter['confirmation_status'],
): { label: string; tone: ToneName; detail: string } {
  return status === 'confirmed'
    ? {
        label: 'Confirmed',
        tone: 'ok',
        detail: 'Verified against the cited regulation.',
      }
    : {
        label: 'Not confirmed',
        tone: 'warn',
        detail:
          'A documented working value. It is used by the calculations, but it has ' +
          'not yet been verified against the published regulation.',
      };
}

// ---------------------------------------------------------------------------
// Lifecycle — where one generation sits on its effective-dated chain
// ---------------------------------------------------------------------------

export type LifecycleKey = 'awaiting_approval' | 'scheduled' | 'in_force' | 'superseded';

export interface Lifecycle {
  key: LifecycleKey;
  label: string;
  tone: ToneName;
  detail: string;
}

/**
 * Which generation the engines are actually reading, as of `todayIso`.
 *
 * Mirrors the server's supersession discipline: approving a successor closes
 * the predecessor with `effective_to = successor.effective_from`, so a
 * generation covers the half-open interval [effective_from, effective_to) and
 * one whose `effective_to` has ARRIVED is already superseded. ISO dates compare
 * correctly as strings, so no Date parsing (and no timezone) is involved.
 */
export function lifecycleOf(row: RegulatoryParameter, todayIso: string): Lifecycle {
  if (row.status === 'draft') {
    return {
      key: 'awaiting_approval',
      label: 'Awaiting approval',
      tone: 'accent',
      detail:
        'Proposed but not yet approved. No calculation reads this value until a ' +
        'second operator approves it.',
    };
  }
  if (row.effective_from > todayIso) {
    return {
      key: 'scheduled',
      label: 'Scheduled',
      tone: 'neutral',
      detail: `Approved. Takes effect on ${row.effective_from}.`,
    };
  }
  if (row.effective_to !== null && row.effective_to <= todayIso) {
    return {
      key: 'superseded',
      label: 'Superseded',
      tone: 'neutral',
      detail: `Replaced by a later generation on ${row.effective_to}.`,
    };
  }
  return {
    key: 'in_force',
    label: 'In force',
    tone: 'ok',
    detail:
      row.effective_to === null
        ? 'The value the calculations are reading now.'
        : `The value the calculations are reading now, until ${row.effective_to}.`,
  };
}

// ---------------------------------------------------------------------------
// Values — strings in, strings out
// ---------------------------------------------------------------------------

/**
 * Accepts only a plain non-negative decimal literal.
 *
 * Deliberately NOT `Number.isFinite(Number(v))`, which accepts `0x1f`, `1e3`,
 * `Infinity`, and whitespace, and which silently rounds anything past ~17
 * significant digits. The backend column is `Numeric(18, 6)` with `ge=0`, so a
 * negative or exponent-notation entry is a server 422 — catching it here keeps
 * the operator out of a pointless round trip without ever coercing the value.
 */
export function isDecimalValue(raw: string): boolean {
  return /^\d+(\.\d+)?$/.test(raw.trim());
}

export interface DisplayValue {
  /** Ready to render. Never `0` as a stand-in for absence. */
  text: string;
  /** The unit, when there is a value to attach it to. */
  unit: string | null;
  /** False when the record carries no value at all — render it as a warning. */
  isSet: boolean;
}

/**
 * Fail-closed value rendering. An absent value is stated as "Not set"; it is
 * never rendered as `0`, and never as an empty cell that reads like zero. The
 * numeric value is passed through as the STRING the API sent.
 */
export function displayValue(row: RegulatoryParameter): DisplayValue {
  if (row.value_numeric !== null) {
    return { text: row.value_numeric, unit: row.unit, isSet: true };
  }
  if (row.value_json !== null) {
    return { text: 'Structured value', unit: null, isSet: true };
  }
  return { text: 'Not set', unit: null, isSet: false };
}

// ---------------------------------------------------------------------------
// Four-eyes
// ---------------------------------------------------------------------------

export type EligibilityState =
  | 'eligible'
  | 'blocked_own_proposal'
  | 'viewer_unknown'
  | 'not_a_draft';

export interface ApprovalEligibility {
  state: EligibilityState;
  /** Whether the UI should let the operator attempt the call at all. */
  canAttempt: boolean;
  /** Shown next to (or instead of) the approve control. */
  message: string;
}

/** Matches the server's comparison: case-insensitive, whitespace-trimmed email. */
function sameOperator(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/**
 * Can THIS viewer approve THIS row?
 *
 * The answer is advisory. Dual control lives in
 * `app/operator/services/regulatory_parameters.py::approve`, which re-checks the
 * proposer against the authenticated caller on every request and 422s — this
 * function only decides what to tell the operator beforehand, so that being the
 * proposer is visible on the row rather than discovered through a server error.
 *
 * `viewerEmail` is null under the local dev-token session, which carries no
 * identity. That case reports `viewer_unknown`: we still allow the attempt (the
 * server is the authority) but we do not claim the operator is eligible.
 */
export function approvalEligibility(
  row: RegulatoryParameter,
  viewerEmail: string | null,
): ApprovalEligibility {
  if (row.status !== 'draft') {
    return {
      state: 'not_a_draft',
      canAttempt: false,
      message: 'Already approved. Propose a new generation to change the value.',
    };
  }
  if (viewerEmail === null || viewerEmail.trim() === '') {
    return {
      state: 'viewer_unknown',
      canAttempt: true,
      message:
        'Signed in without an identity, so this cannot be checked here. Approval ' +
        'is refused if you are the operator who proposed this value.',
    };
  }
  if (sameOperator(viewerEmail, row.proposed_by)) {
    return {
      state: 'blocked_own_proposal',
      canAttempt: false,
      message:
        'You proposed this value, so you cannot approve it. A second operator ' +
        'must review and approve it.',
    };
  }
  return {
    state: 'eligible',
    canAttempt: true,
    message: `Proposed by ${row.proposed_by}. Approving makes this the governing value.`,
  };
}

// ---------------------------------------------------------------------------
// History — the effective-dated chain for one parameter + scope
// ---------------------------------------------------------------------------

/**
 * A parameter's identity for history purposes — the server's own resolution key
 * (`scope_type`, `scope_key`, `param_code`, `jurisdiction_code`), which is also
 * what `propose` uniqueness and `approve` supersession match on.
 */
export function chainKey(
  row: Pick<
    RegulatoryParameter,
    'param_code' | 'scope_type' | 'scope_key' | 'jurisdiction_code'
  >,
): string {
  return [row.param_code, row.scope_type, row.scope_key, row.jurisdiction_code].join(' ');
}

export interface ParameterChain {
  key: string;
  param_code: string;
  scope_type: RegulatoryParameter['scope_type'];
  scope_key: string;
  jurisdiction_code: string;
  /** Newest effective date first — the supersession order, most recent on top. */
  generations: RegulatoryParameter[];
  /** The generation the engines read as of the given date, if any. */
  inForce: RegulatoryParameter | null;
  /** Drafts on this chain waiting for a second operator. */
  awaitingApproval: number;
}

/**
 * Group flat rows into effective-dated chains, newest generation first.
 *
 * Ordering is by `effective_from` descending, tie-broken by `created_at`
 * descending so two generations sharing a date still show the later proposal on
 * top. Sorting is on ISO strings — no Date, no locale, no timezone.
 */
export function buildChains(
  rows: readonly RegulatoryParameter[],
  todayIso: string,
): ParameterChain[] {
  const byKey = new Map<string, RegulatoryParameter[]>();
  for (const row of rows) {
    const key = chainKey(row);
    const bucket = byKey.get(key);
    if (bucket) bucket.push(row);
    else byKey.set(key, [row]);
  }

  const chains: ParameterChain[] = [];
  for (const [key, group] of byKey) {
    const generations = [...group].sort((a, b) => {
      if (a.effective_from !== b.effective_from) {
        return a.effective_from < b.effective_from ? 1 : -1;
      }
      if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1;
      return 0;
    });
    const head = generations[0];
    chains.push({
      key,
      param_code: head.param_code,
      scope_type: head.scope_type,
      scope_key: head.scope_key,
      jurisdiction_code: head.jurisdiction_code,
      generations,
      inForce:
        generations.find((g) => lifecycleOf(g, todayIso).key === 'in_force') ?? null,
      awaitingApproval: generations.filter((g) => g.status === 'draft').length,
    });
  }

  chains.sort(
    (a, b) =>
      a.param_code.localeCompare(b.param_code) ||
      a.scope_key.localeCompare(b.scope_key) ||
      a.jurisdiction_code.localeCompare(b.jurisdiction_code),
  );
  return chains;
}

// ---------------------------------------------------------------------------
// Propose-form validation
// ---------------------------------------------------------------------------

export interface ProposeForm {
  scope_type: RegulatoryParameter['scope_type'];
  scope_key: string;
  param_code: string;
  jurisdiction_code: string;
  value_numeric: string;
  unit: string;
  source_citation: string;
  confirmation_status: RegulatoryParameter['confirmation_status'];
  effective_from: string;
  change_rationale: string;
}

export type ProposeFormErrors = Partial<Record<keyof ProposeForm, string>>;

/**
 * Client-side mirror of `RegulatoryParameterProposeRequest` — same required
 * fields, same lengths, same non-negative value, same "not in the past"
 * effective date the service enforces. It exists to give the operator the
 * message next to the field instead of as a 422; the schema remains the
 * authority.
 */
export function validateProposal(form: ProposeForm, todayIso: string): ProposeFormErrors {
  const errs: ProposeFormErrors = {};

  if (!form.scope_key.trim()) errs.scope_key = 'Required.';
  else if (form.scope_key.trim().length > 40) errs.scope_key = 'Use 40 characters or fewer.';

  if (!form.param_code.trim()) errs.param_code = 'Required.';
  else if (form.param_code.trim().length > 64) errs.param_code = 'Use 64 characters or fewer.';

  if (!form.jurisdiction_code.trim()) {
    errs.jurisdiction_code =
      'Required. A value without a jurisdiction would be applied to the wrong country.';
  } else if (form.jurisdiction_code.trim().length > 8) {
    errs.jurisdiction_code = 'Use 8 characters or fewer.';
  }

  const value = form.value_numeric.trim();
  if (!value) errs.value_numeric = 'Required.';
  else if (!isDecimalValue(value)) {
    errs.value_numeric =
      'Enter digits with an optional decimal point. Symbols, negative numbers, ' +
      'and scientific notation are not accepted.';
  }

  if (!form.unit.trim()) errs.unit = 'Required — state what the number is measured in.';
  else if (form.unit.trim().length > 24) errs.unit = 'Use 24 characters or fewer.';

  if (!form.source_citation.trim()) {
    errs.source_citation = 'Required. Name the regulation this value comes from.';
  } else if (form.source_citation.trim().length > 240) {
    errs.source_citation = 'Use 240 characters or fewer.';
  }

  if (!form.effective_from) errs.effective_from = 'Required.';
  else if (form.effective_from < todayIso) {
    errs.effective_from =
      'Cannot be in the past. Back-dating would change the value that filings ' +
      'already submitted were calculated on.';
  }

  if (!form.change_rationale.trim()) {
    errs.change_rationale = 'Required. Record why this value is being set or changed.';
  } else if (form.change_rationale.trim().length > 500) {
    errs.change_rationale = 'Use 500 characters or fewer.';
  }

  return errs;
}

/** True when the form has no blocking error. */
export function isProposalValid(errors: ProposeFormErrors): boolean {
  return Object.keys(errors).length === 0;
}
