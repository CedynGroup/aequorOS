/**
 * Choosing WHICH institution an inspection session examines.
 *
 * `GET /operator/v1/tenants` returns one row per bank, so an organization with
 * several institutions appears several times in the tenant board. The inspector
 * form therefore keys its selector on the (organization, bank) PAIR: an
 * organization id alone cannot say which institution the session is bound to, and
 * binding to whichever row happened to come first would leave a sibling bank's
 * filed registers unreachable through the console.
 *
 * `GET /tenants/{org}` is deliberately not used for this: it answers with the
 * organization's earliest-created bank.
 */

import type { OperatorTenant } from "./api";

/** The selector value for one (organization, bank) pair. */
export function examinedOptionOf(
  organizationId: string,
  bankId: string | null,
): string {
  return `${organizationId}|${bankId ?? ""}`;
}

/** The pair behind a selector value; a malformed value reads as "choose one". */
export function splitExaminedOption(option: string): {
  organizationId: string;
  bankId: string;
} {
  const [organizationId = "", bankId = ""] = option.split("|");
  return { organizationId, bankId };
}

/**
 * Every institution of one organization that an inspection session can examine.
 *
 * A row with no bank is an organization the provisioning saga has not finished;
 * there is no institution to bind to, so the form reports that instead of
 * offering a choice.
 */
export function examinableBanks(
  tenants: readonly OperatorTenant[],
  organizationId: string,
): OperatorTenant[] {
  return tenants.filter(
    (tenant) =>
      tenant.organization_id === organizationId && tenant.bank_id !== null,
  );
}
