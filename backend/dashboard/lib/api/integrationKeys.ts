/**
 * Integration keys: what a bank administrator issues, in the words they use.
 *
 * A key is issued for ONE purpose, and the two purposes are disjoint
 * authorities on the server (`app/schemas/integration_keys.py`): a data push
 * key carries `ingest` and may read nothing; an analytics feed key carries
 * `view` and may write nothing. The feed's authorization
 * (`app/services/bi/feeds/authorization.py`) asks for `view`, so a key issued
 * with the server's DEFAULT purpose (`writer`) is refused by every feed pull.
 *
 * That is exactly what happened (audit A360-2 H4): the dashboard posted only
 * `{bank_id, label}`, the server defaulted the purpose to `writer`, and the
 * Power BI guide told the bank to ask its administrator for "an analytics feed
 * key" the application offered no way to issue. This module is the fix's
 * spine: the purpose is a choice the administrator makes in production copy,
 * and the request is built HERE, as the generated request type, so the
 * compiler checks its shape and `integrationKeys.test.ts` pins that every
 * field the generated serializer emits is one this builder states — a field
 * the contract carries and the caller leaves unset is decided by the server's
 * column default, which for a purpose column means the WRONG credential with a
 * success dialog.
 *
 * Pure and dependency-free (type-only imports), so plain Node can prove it.
 */

import type {
  DataScope,
  IntegrationKeyIssueRequest,
  IntegrationKeyIssueRequestPurposeEnum,
  IntegrationKeyRead,
} from "@aequoros/risk-service-api";

/** The two things a key can be for. Wire values; never shown to a person. */
export type IntegrationKeyPurpose = IntegrationKeyIssueRequestPurposeEnum;

export const DATA_PUSH_PURPOSE: IntegrationKeyPurpose = "writer";
export const ANALYTICS_FEED_PURPOSE: IntegrationKeyPurpose = "reader";

/**
 * Analytics feed keys cover every module and require whole-institution scope.
 * State it explicitly rather than relying on the server's default.
 */
export const WHOLE_INSTITUTION_SCOPE: DataScope = "all";

export type IntegrationKeyPurposeOption = Readonly<{
  value: IntegrationKeyPurpose;
  /** The noun an administrator picks. */
  label: string;
  /** One sentence on what the key may do, and the sentence on what it may not. */
  description: string;
  /** The placeholder for the key's label, in the purpose's own vocabulary. */
  labelPlaceholder: string;
}>;

/**
 * Production copy for the choice. The order is the order on screen: the push
 * key first, because it is what the page this control sits on is about.
 */
export const INTEGRATION_KEY_PURPOSES: readonly IntegrationKeyPurposeOption[] =
  [
    {
      value: DATA_PUSH_PURPOSE,
      label: "Data push key",
      description:
        "Lets your middleware open a batch, stage records and commit them to the Data Engine. It cannot read anything back.",
      labelPlaceholder: "Key label — e.g. Core banking nightly push",
    },
    {
      value: ANALYTICS_FEED_PURPOSE,
      label: "Analytics feed key",
      description:
        "Lets a report server such as Power BI pull the curated analytics feed for this institution. It cannot push data.",
      labelPlaceholder: "Key label — e.g. Power BI gateway",
    },
  ];

/** What an administrator holds, named the way it was issued. */
export function purposeLabel(
  purpose: IntegrationKeyRead["purpose"] | IntegrationKeyPurpose | undefined,
): string {
  const option = INTEGRATION_KEY_PURPOSES.find(
    (candidate) => candidate.value === purpose,
  );
  if (option) return option.label;
  // A pre-purpose legacy row: the server records no machine binding for it, so
  // it authorizes nothing. Say so instead of printing a blank or a wire word.
  return "No purpose recorded";
}

/** Whether a listed key authorizes anything at all. */
export function keyAuthorizesNothing(
  key: Pick<IntegrationKeyRead, "bankId" | "purpose">,
): boolean {
  return !key.bankId || key.purpose === null || key.purpose === undefined;
}

export type IntegrationKeyDraft = Readonly<{
  bankId: string;
  label: string;
  purpose: IntegrationKeyPurpose;
}>;

/**
 * The request the dashboard posts. EVERY field the generated request contract
 * carries is stated here — none is left to the server's default — and the test
 * beside this module fails the moment the contract grows a field this builder
 * does not decide.
 */
export function integrationKeyIssueRequest(
  draft: IntegrationKeyDraft,
): IntegrationKeyIssueRequest {
  return {
    bankId: draft.bankId,
    label: draft.label,
    purpose: draft.purpose,
    dataScopeKind: WHOLE_INSTITUTION_SCOPE,
    dataScopeValues: [],
  };
}
