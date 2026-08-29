import type {
  BindingCreateRequestRoleBundleEnum,
  BindingRead,
  InstitutionScope,
  MemberRead,
  ModuleScope,
  SensitivityScope,
} from "@aequoros/risk-service-api";

export type GrantDraft = Readonly<{
  roleBundle: BindingCreateRequestRoleBundleEnum;
  institutionScope: InstitutionScope;
  institutionId?: string;
  institutionName: string;
  moduleScope: ModuleScope;
  sensitivityScope: SensitivityScope;
  reason: string;
}>;

export const ROLE_OPTIONS = [
  ["viewer", "Viewer"],
  ["auditor", "Auditor"],
  ["analyst", "Analyst"],
  ["approver", "Approver"],
  ["account_admin", "Organization Administrator"],
] as const;

export const MODULE_OPTIONS = [
  ["liq", "Liquidity Monitoring"],
  ["cap", "Basel Capital"],
  ["irrbb", "IRRBB"],
  ["fx", "Foreign Exchange"],
  ["ftp", "Funds Transfer Pricing"],
  ["fcst", "Forecasting"],
  ["beh", "Behavioral Models"],
  ["data", "Data Engine"],
  ["reg", "Regulatory Reporting"],
  ["risk", "Risk & Limits"],
  ["markets", "Markets"],
  ["account", "Account Administration"],
  ["audit", "Audit"],
  ["all", "All modules"],
] as const;

export const SENSITIVITY_OPTIONS = [
  ["published", "Published"],
  ["aggregated", "Aggregated"],
  ["confidential", "Confidential"],
  ["restricted", "Restricted"],
  ["all", "All sensitivity levels"],
] as const;

function optionLabel(
  options: readonly (readonly [string, string])[],
  value: string,
): string {
  return options.find(([candidate]) => candidate === value)?.[1] ?? value;
}

export function grantAuthoritySentence(
  principalName: string,
  draft: GrantDraft,
): string {
  const role = optionLabel(ROLE_OPTIONS, draft.roleBundle);
  const article = /^[aeiou]/i.test(role) ? "an" : "a";
  const moduleLabel = optionLabel(MODULE_OPTIONS, draft.moduleScope);
  const modulePhrase =
    draft.moduleScope === "all" ? "across all modules" : `in ${moduleLabel}`;
  const sensitivity = optionLabel(SENSITIVITY_OPTIONS, draft.sensitivityScope);
  const sensitivityPhrase =
    draft.sensitivityScope === "all"
      ? "covering all sensitivity levels"
      : `covering ${sensitivity} data`;
  return `${principalName} is ${article} ${role} ${modulePhrase} for ${draft.institutionName}, ${sensitivityPhrase}.`;
}

export function compactGrantFragment(grant: BindingRead): string {
  const role = optionLabel(ROLE_OPTIONS, grant.roleBundle);
  const moduleLabel = optionLabel(MODULE_OPTIONS, grant.moduleScope);
  const institution =
    grant.institutionScope === "organization"
      ? "Every institution"
      : (grant.institutionName ?? grant.institutionId ?? "Institution");
  return `${role} · ${moduleLabel} · ${institution}`;
}

export function visibleGrantFragments(
  grants: readonly BindingRead[],
): Readonly<{ fragments: readonly string[]; remaining: number }> {
  const active = grants.filter((grant) => grant.effective);
  return {
    fragments: active.slice(0, 2).map(compactGrantFragment),
    remaining: Math.max(0, active.length - 2),
  };
}

export function canAddGrantToMember(
  member: Pick<
    MemberRead,
    "authenticationMethod" | "lifecycleStatus" | "accessRequestState"
  >,
): boolean {
  return (
    member.authenticationMethod !== "service" &&
    (member.lifecycleStatus !== "deactivated" ||
      member.accessRequestState === "approval_needed")
  );
}
