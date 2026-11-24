/**
 * The development identity provider's synthetic staff (SPEC Part 4), used by
 * the portal's mock mode so sign-in works without any provider. Fictional
 * people only.
 */

import type { Role } from "../api/types";

export type SyntheticStaff = {
  subject: string;
  name: string;
  email: string;
  groups: string[];
};

export const SYNTHETIC_STAFF: readonly SyntheticStaff[] = [
  { subject: "dev-staff-kofi-owusu", name: "Kofi Owusu", email: "kofi.owusu@staff.example", groups: ["srg-senior-supervisors"] },
  { subject: "dev-staff-akosua-boateng", name: "Akosua Boateng", email: "akosua.boateng@staff.example", groups: ["srg-supervisors"] },
  { subject: "dev-staff-esi-asante", name: "Esi Asante", email: "esi.asante@staff.example", groups: ["srg-returns-policy"] },
  { subject: "dev-staff-michael-adjei", name: "Michael Adjei", email: "michael.adjei@staff.example", groups: ["srg-returns-policy", "srg-access-approvers"] },
  { subject: "dev-staff-internal-audit", name: "Internal Audit", email: "internal.audit@staff.example", groups: ["srg-internal-audit"] },
];

/** Identity group → role, as ARCHITECTURE.md "BoG staff" defines it. */
export const GROUP_ROLES: Readonly<Record<string, Role>> = {
  "srg-supervisors": "supervisor",
  "srg-senior-supervisors": "senior_supervisor",
  "srg-returns-policy": "returns_policy",
  "srg-access-approvers": "access_approver",
  "srg-internal-audit": "auditor",
};

export function rolesForGroups(groups: readonly string[]): Role[] {
  const roles: Role[] = [];
  for (const group of groups) {
    const role = GROUP_ROLES[group];
    if (role && !roles.includes(role)) roles.push(role);
  }
  return roles;
}

export function findSyntheticStaff(subject: string): SyntheticStaff | undefined {
  return SYNTHETIC_STAFF.find((staff) => staff.subject === subject);
}
