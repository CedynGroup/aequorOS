/** Data push credentials are the only integration keys available for issuance. */
import type {
  IntegrationKeyIssueRequest,
  IntegrationKeyRead,
} from "@aequoros/risk-service-api";

export function purposeLabel(purpose: IntegrationKeyRead["purpose"]): string {
  return purpose === "writer" ? "Data push key" : "No purpose recorded";
}

/** Unsupported and unscoped credentials require rotation. */
export function keyAuthorizesNothing(
  key: Pick<IntegrationKeyRead, "bankId" | "purpose">,
): boolean {
  return !key.bankId || key.purpose !== "writer";
}

export type IntegrationKeyDraft = Readonly<{
  bankId: string;
  label: string;
}>;

/** State the complete writer request; no authority comes from a UI choice. */
export function integrationKeyIssueRequest(
  draft: IntegrationKeyDraft,
): IntegrationKeyIssueRequest {
  return {
    bankId: draft.bankId,
    label: draft.label,
    purpose: "writer",
    dataScopeKind: "all",
    dataScopeValues: [],
  };
}
