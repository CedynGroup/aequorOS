import { GrantReasonCategory } from "@aequoros/risk-service-api";
import {
  MODULE_OPTIONS,
  ROLE_OPTIONS,
  SENSITIVITY_OPTIONS,
  type GrantDraft,
} from "./grants";

type StoredGrantDraft = Readonly<{
  draft: GrantDraft;
  accessRequestId?: string;
}>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDraft(value: unknown): value is GrantDraft {
  if (!isRecord(value) || !isRecord(value.dataScope)) return false;
  return (
    ROLE_OPTIONS.some(([role]) => role === value.roleBundle) &&
    (value.institutionScope === "organization" ||
      value.institutionScope === "institution") &&
    (value.institutionId === undefined ||
      typeof value.institutionId === "string") &&
    MODULE_OPTIONS.some(([module]) => module === value.moduleScope) &&
    SENSITIVITY_OPTIONS.some(([scope]) => scope === value.sensitivityScope) &&
    (value.dataScope.kind === "all" ||
      value.dataScope.kind === "branch" ||
      value.dataScope.kind === "region") &&
    Array.isArray(value.dataScope.values) &&
    value.dataScope.values.every((item: unknown) => typeof item === "string") &&
    Object.values(GrantReasonCategory).some(
      (category) => category === value.reasonCategory,
    ) &&
    typeof value.reasonDetail === "string" &&
    typeof value.reference === "string" &&
    typeof value.validUntil === "string"
  );
}

export function grantDraftStorageKey(
  organizationId: string,
  actorId: string,
  memberId: string,
): string {
  return `aequoros:grant-draft:${organizationId}:${actorId}:${memberId}`;
}

export function readGrantDraft(
  key: string | undefined,
): StoredGrantDraft | null {
  if (!key || typeof window === "undefined") return null;
  try {
    const value: unknown = JSON.parse(
      window.sessionStorage.getItem(key) ?? "null",
    );
    if (!isRecord(value) || !isDraft(value.draft)) return null;
    if (
      value.accessRequestId !== undefined &&
      typeof value.accessRequestId !== "string"
    )
      return null;
    return { draft: value.draft, accessRequestId: value.accessRequestId };
  } catch {
    return null;
  }
}

export function storeGrantDraft(
  key: string | undefined,
  value: StoredGrantDraft | null,
): void {
  if (!key || typeof window === "undefined") return;
  try {
    if (value) window.sessionStorage.setItem(key, JSON.stringify(value));
    else window.sessionStorage.removeItem(key);
  } catch {
    return;
  }
}
