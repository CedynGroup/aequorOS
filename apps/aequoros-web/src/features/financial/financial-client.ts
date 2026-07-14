import {
  Configuration,
  FinancialDataApi,
  type FinancialAccountCreate,
  type FinancialAccountMutationResponse,
  type FinancialAccountUpdate,
  type FinancialBalanceCreate,
  type FinancialBalanceMutationResponse,
  type FinancialBalanceUpdate,
  type FinancialCashFlowCreate,
  type FinancialCashFlowMutationResponse,
  type FinancialCashFlowUpdate,
  type FinancialCovenantCreate,
  type FinancialCovenantMutationResponse,
  type FinancialCovenantUpdate,
  type FinancialDataWorkspaceRead,
  type FinancialInstitutionCreate,
  type FinancialInstitutionMutationResponse,
  type FinancialInstitutionUpdate,
  type FinancialObligationCreate,
  type FinancialObligationMutationResponse,
  type FinancialObligationUpdate,
  type FinancialReportingPeriodCreate,
  type FinancialReportingPeriodMutationResponse,
  type FinancialReportingPeriodUpdate,
  type FinancialValidationRunResponse,
  type FinancialWorkspaceMapRequest,
  type FinancialWorkspaceMapResponse,
  ResponseError,
} from "@aequoros/risk-service-api";

import type { TenantHeaders } from "../../lib/api";
import { apiBaseUrl } from "../../lib/constants";

export type EditableFinancialKind =
  | "institution"
  | "account"
  | "reportingPeriod"
  | "balance"
  | "cashFlow"
  | "obligation"
  | "covenant";

export type FinancialCreatePayload =
  | FinancialInstitutionCreate
  | FinancialAccountCreate
  | FinancialReportingPeriodCreate
  | FinancialBalanceCreate
  | FinancialCashFlowCreate
  | FinancialObligationCreate
  | FinancialCovenantCreate;

export type FinancialUpdatePayload =
  | FinancialInstitutionUpdate
  | FinancialAccountUpdate
  | FinancialReportingPeriodUpdate
  | FinancialBalanceUpdate
  | FinancialCashFlowUpdate
  | FinancialObligationUpdate
  | FinancialCovenantUpdate;

export type FinancialMutationResponse =
  | FinancialInstitutionMutationResponse
  | FinancialAccountMutationResponse
  | FinancialReportingPeriodMutationResponse
  | FinancialBalanceMutationResponse
  | FinancialCashFlowMutationResponse
  | FinancialObligationMutationResponse
  | FinancialCovenantMutationResponse;

export interface FinancialReviewClient {
  workspace(
    tenant: TenantHeaders,
    caseId: string,
  ): Promise<FinancialDataWorkspaceRead>;
  create(
    kind: EditableFinancialKind,
    tenant: TenantHeaders,
    caseId: string,
    payload: FinancialCreatePayload,
  ): Promise<FinancialMutationResponse>;
  update(
    kind: EditableFinancialKind,
    tenant: TenantHeaders,
    caseId: string,
    recordId: string,
    payload: FinancialUpdatePayload,
  ): Promise<FinancialMutationResponse>;
  map(
    tenant: TenantHeaders,
    caseId: string,
    payload: FinancialWorkspaceMapRequest,
  ): Promise<FinancialWorkspaceMapResponse>;
  validate(
    tenant: TenantHeaders,
    caseId: string,
  ): Promise<FinancialValidationRunResponse>;
}

function generatedFinancialApi() {
  const basePath = apiBaseUrl().replace(/\/api\/v1\/?$/, "");
  return new FinancialDataApi(new Configuration({ basePath }));
}

function requestHeaders(tenant: TenantHeaders, caseId: string) {
  return { caseId, xOrgId: tenant.orgId, xUserId: tenant.userId };
}

export const financialReviewClient: FinancialReviewClient = {
  workspace(tenant, caseId) {
    return generatedFinancialApi().getCaseFinancialWorkspace(
      requestHeaders(tenant, caseId),
    );
  },
  create(kind, tenant, caseId, payload) {
    const api = generatedFinancialApi();
    const headers = requestHeaders(tenant, caseId);
    switch (kind) {
      case "institution":
        return api.createCaseFinancialInstitution({
          ...headers,
          financialInstitutionCreate: payload as FinancialInstitutionCreate,
        });
      case "account":
        return api.createCaseFinancialAccount({
          ...headers,
          financialAccountCreate: payload as FinancialAccountCreate,
        });
      case "reportingPeriod":
        return api.createCaseFinancialReportingPeriod({
          ...headers,
          financialReportingPeriodCreate:
            payload as FinancialReportingPeriodCreate,
        });
      case "balance":
        return api.createCaseFinancialBalance({
          ...headers,
          financialBalanceCreate: payload as FinancialBalanceCreate,
        });
      case "cashFlow":
        return api.createCaseFinancialCashFlow({
          ...headers,
          financialCashFlowCreate: payload as FinancialCashFlowCreate,
        });
      case "obligation":
        return api.createCaseFinancialObligation({
          ...headers,
          financialObligationCreate: payload as FinancialObligationCreate,
        });
      case "covenant":
        return api.createCaseFinancialCovenant({
          ...headers,
          financialCovenantCreate: payload as FinancialCovenantCreate,
        });
    }
  },
  update(kind, tenant, caseId, recordId, payload) {
    const api = generatedFinancialApi();
    const headers = requestHeaders(tenant, caseId);
    switch (kind) {
      case "institution":
        return api.updateCaseFinancialInstitution({
          ...headers,
          institutionId: recordId,
          financialInstitutionUpdate: payload as FinancialInstitutionUpdate,
        });
      case "account":
        return api.updateCaseFinancialAccount({
          ...headers,
          accountId: recordId,
          financialAccountUpdate: payload as FinancialAccountUpdate,
        });
      case "reportingPeriod":
        return api.updateCaseFinancialReportingPeriod({
          ...headers,
          reportingPeriodId: recordId,
          financialReportingPeriodUpdate:
            payload as FinancialReportingPeriodUpdate,
        });
      case "balance":
        return api.updateCaseFinancialBalance({
          ...headers,
          balanceId: recordId,
          financialBalanceUpdate: payload as FinancialBalanceUpdate,
        });
      case "cashFlow":
        return api.updateCaseFinancialCashFlow({
          ...headers,
          cashFlowId: recordId,
          financialCashFlowUpdate: payload as FinancialCashFlowUpdate,
        });
      case "obligation":
        return api.updateCaseFinancialObligation({
          ...headers,
          obligationId: recordId,
          financialObligationUpdate: payload as FinancialObligationUpdate,
        });
      case "covenant":
        return api.updateCaseFinancialCovenant({
          ...headers,
          covenantId: recordId,
          financialCovenantUpdate: payload as FinancialCovenantUpdate,
        });
    }
  },
  map(tenant, caseId, payload) {
    return generatedFinancialApi().mapCaseFinancialWorkspace({
      ...requestHeaders(tenant, caseId),
      financialWorkspaceMapRequest: payload,
    });
  },
  validate(tenant, caseId) {
    return generatedFinancialApi().validateCaseFinancialData(
      requestHeaders(tenant, caseId),
    );
  },
};

export async function financialErrorMessage(error: unknown) {
  if (error instanceof ResponseError) {
    try {
      const body = (await error.response.clone().json()) as {
        error?: { message?: string };
        detail?: string | Array<{ msg?: string }>;
      };
      if (body.error?.message) return body.error.message;
      if (typeof body.detail === "string") return body.detail;
      if (Array.isArray(body.detail)) {
        return (
          body.detail
            .map((item) => item.msg)
            .filter(Boolean)
            .join(" ") || error.message
        );
      }
    } catch {
      // Fall through to the generated client's error message.
    }
  }
  return error instanceof Error
    ? error.message
    : "The financial mutation failed.";
}
