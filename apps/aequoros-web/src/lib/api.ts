import {
  type AssumptionCreate,
  AssumptionCreateToJSON,
  type AssumptionReview,
  AssumptionReviewToJSON,
  type AssumptionUpdate,
  AssumptionUpdateToJSON,
  type CaseBulkActionRead,
  CaseBulkActionReadFromJSON,
  type CaseDecisionCreate,
  type CaseDecisionRead,
  CaseDecisionReadFromJSON,
  type CaseListRead,
  CaseListReadFromJSON,
  type CaseRead,
  CaseReadFromJSON,
  type CalculationRerunCreate,
  CalculationRerunCreateToJSON,
  type CalculationRunCreate,
  CalculationRunCreateToJSON,
  type CalculationRunListRead,
  CalculationRunListReadFromJSON,
  type CalculationRunRead,
  CalculationRunReadFromJSON,
  type CapitalComparisonRead,
  CapitalComparisonReadFromJSON,
  type CapitalProjectionCreate,
  CapitalProjectionCreateToJSON,
  type CapitalProjectionListRead,
  CapitalProjectionListReadFromJSON,
  type CapitalProjectionRead,
  CapitalProjectionReadFromJSON,
  type CapitalSummaryRead,
  CapitalSummaryReadFromJSON,
  type CaseSort,
  type CaseStatus,
  type CaseTaxonomyRead,
  CaseTaxonomyReadFromJSON,
  type CompleteUploadResponse,
  CompleteUploadResponseFromJSON,
  type DownloadUrlResponse,
  DownloadUrlResponseFromJSON,
  type DocumentRead,
  DocumentReadFromJSON,
  type ErrorResponse,
  ErrorResponseFromJSON,
  type FinancialDataWorkspaceRead,
  FinancialDataWorkspaceReadFromJSON,
  type FindingCreate,
  type FindingRead,
  FindingReadFromJSON,
  type FindingUpdate,
  type Payload,
  type ParseResponse,
  ParseResponseFromJSON,
  type ParseStatusResponse,
  ParseStatusResponseFromJSON,
  type RiskLevel,
  type RiskReportPayload,
  RiskReportPayloadFromJSON,
  type ScenarioArchive,
  ScenarioArchiveToJSON,
  type ScenarioCopy,
  ScenarioCopyToJSON,
  type ScenarioCreate,
  ScenarioCreateToJSON,
  type ScenarioInitialize,
  ScenarioInitializeToJSON,
  type ScenarioMutationResponse,
  ScenarioMutationResponseFromJSON,
  type ScenarioUpdate,
  ScenarioUpdateToJSON,
  type ScenarioValidationRead,
  ScenarioValidationReadFromJSON,
  type ScenarioWorkspaceRead,
  ScenarioWorkspaceReadFromJSON,
  type UploadRequest,
  type UploadRequestResponse,
  UploadRequestResponseFromJSON,
} from "@aequoros/risk-service-api";

import { apiBaseUrl } from "./constants";

export type TenantHeaders = {
  orgId: string;
  userId: string;
};

export type ApiError = {
  statusCode: number;
  code: string;
  message: string;
  response?: ErrorResponse;
};

export type CaseQuery = {
  includeArchived: boolean;
  status?: CaseStatus;
  riskLevel?: RiskLevel;
  q?: string;
  sort?: CaseSort;
  limit: number;
  offset: number;
};

type Decoder<T> = (json: unknown) => T;

async function parseFailure(response: Response): Promise<ApiError> {
  const fallback = {
    statusCode: response.status,
    code: "api_error",
    message: response.statusText || "Request failed.",
  };

  try {
    const json = await response.json();
    const envelope = ErrorResponseFromJSON(json);
    return {
      statusCode: response.status,
      code: envelope.error.code,
      message: envelope.error.message,
      response: envelope,
    };
  } catch {
    return fallback;
  }
}

export async function apiJson<T>(
  path: string,
  tenant: TenantHeaders,
  decoder: Decoder<T>,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Org-Id": tenant.orgId,
      "X-User-Id": tenant.userId,
      ...init.headers,
    },
  });

  if (!response.ok) {
    throw await parseFailure(response);
  }

  return decoder(await response.json());
}

export async function apiText(
  path: string,
  tenant: TenantHeaders,
  accept: "text/html" | "text/plain" = "text/html",
): Promise<string> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    headers: {
      Accept: accept,
      "X-Org-Id": tenant.orgId,
      "X-User-Id": tenant.userId,
    },
  });

  if (!response.ok) {
    throw await parseFailure(response);
  }

  return response.text();
}

function toQuery(
  params: Record<string, string | number | boolean | undefined>,
) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

export const riskApi = {
  listCases(tenant: TenantHeaders, query: CaseQuery) {
    return apiJson<CaseListRead>(
      `/cases${toQuery({
        include_archived: query.includeArchived,
        status: query.status,
        risk_level: query.riskLevel,
        q: query.q,
        sort: query.sort,
        limit: query.limit,
        offset: query.offset,
      })}`,
      tenant,
      CaseListReadFromJSON,
    );
  },
  getCase(tenant: TenantHeaders, caseId: string) {
    return apiJson<CaseRead>(`/cases/${caseId}`, tenant, CaseReadFromJSON);
  },
  caseTaxonomy(tenant: TenantHeaders) {
    return apiJson<CaseTaxonomyRead>(
      "/taxonomies/cases",
      tenant,
      CaseTaxonomyReadFromJSON,
    );
  },
  bulkCases(tenant: TenantHeaders, payload: Payload) {
    return apiJson<CaseBulkActionRead>(
      "/cases/bulk-actions",
      tenant,
      CaseBulkActionReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify(bulkPayloadToJson(payload)),
      },
    );
  },
  financialWorkspace(tenant: TenantHeaders, caseId: string) {
    return apiJson<FinancialDataWorkspaceRead>(
      `/cases/${caseId}/financial-workspace`,
      tenant,
      FinancialDataWorkspaceReadFromJSON,
    );
  },
  calculationRuns(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId?: string,
    limit = 25,
    offset = 0,
    activeScenariosOnly = false,
  ) {
    return apiJson<CalculationRunListRead>(
      `/cases/${caseId}/calculation-runs${toQuery({
        scenario_id: scenarioId,
        limit,
        offset,
        active_scenarios_only: activeScenariosOnly || undefined,
      })}`,
      tenant,
      CalculationRunListReadFromJSON,
    );
  },
  calculationRun(tenant: TenantHeaders, caseId: string, runId: string) {
    return apiJson<CalculationRunRead>(
      `/cases/${caseId}/calculation-runs/${runId}`,
      tenant,
      CalculationRunReadFromJSON,
    );
  },
  startCalculation(
    tenant: TenantHeaders,
    caseId: string,
    payload: CalculationRunCreate,
  ) {
    return apiJson<CalculationRunRead>(
      `/cases/${caseId}/calculation-runs`,
      tenant,
      CalculationRunReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify(CalculationRunCreateToJSON(payload)),
      },
    );
  },
  rerunCalculation(
    tenant: TenantHeaders,
    caseId: string,
    runId: string,
    payload: CalculationRerunCreate = {},
  ) {
    return apiJson<CalculationRunRead>(
      `/cases/${caseId}/calculation-runs/${runId}/rerun`,
      tenant,
      CalculationRunReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify(CalculationRerunCreateToJSON(payload)),
      },
    );
  },
  capitalSummary(tenant: TenantHeaders, caseId: string, scenarioId?: string) {
    return apiJson<CapitalSummaryRead>(
      `/cases/${caseId}/capital-summary${toQuery({ scenario_id: scenarioId })}`,
      tenant,
      CapitalSummaryReadFromJSON,
    );
  },
  capitalProjections(
    tenant: TenantHeaders,
    caseId: string,
    limit = 25,
    offset = 0,
  ) {
    return apiJson<CapitalProjectionListRead>(
      `/cases/${caseId}/capital-projections${toQuery({ limit, offset })}`,
      tenant,
      CapitalProjectionListReadFromJSON,
    );
  },
  capitalProjection(
    tenant: TenantHeaders,
    caseId: string,
    projectionId: string,
  ) {
    return apiJson<CapitalProjectionRead>(
      `/cases/${caseId}/capital-projections/${projectionId}`,
      tenant,
      CapitalProjectionReadFromJSON,
    );
  },
  capitalComparison(tenant: TenantHeaders, caseId: string) {
    return apiJson<CapitalComparisonRead>(
      `/cases/${caseId}/capital-comparison`,
      tenant,
      CapitalComparisonReadFromJSON,
    );
  },
  createCapitalProjection(
    tenant: TenantHeaders,
    caseId: string,
    payload: CapitalProjectionCreate,
  ) {
    return apiJson<CapitalProjectionRead>(
      `/cases/${caseId}/capital-projections`,
      tenant,
      CapitalProjectionReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify(CapitalProjectionCreateToJSON(payload)),
      },
    );
  },
  scenarios(tenant: TenantHeaders, caseId: string, includeArchived = false) {
    return apiJson<ScenarioWorkspaceRead>(
      `/cases/${caseId}/scenarios${toQuery({ include_archived: includeArchived })}`,
      tenant,
      ScenarioWorkspaceReadFromJSON,
    );
  },
  scenarioValidation(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
  ) {
    return apiJson<ScenarioValidationRead>(
      `/cases/${caseId}/scenarios/${scenarioId}/validation`,
      tenant,
      ScenarioValidationReadFromJSON,
    );
  },
  initializeScenarios(
    tenant: TenantHeaders,
    caseId: string,
    payload: ScenarioInitialize,
  ) {
    return apiJson<ScenarioWorkspaceRead>(
      `/cases/${caseId}/scenarios/initialize`,
      tenant,
      ScenarioWorkspaceReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify(ScenarioInitializeToJSON(payload)),
      },
    );
  },
  createScenario(
    tenant: TenantHeaders,
    caseId: string,
    payload: ScenarioCreate,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios`,
      tenant,
      ScenarioMutationResponseFromJSON,
      { method: "POST", body: JSON.stringify(ScenarioCreateToJSON(payload)) },
    );
  },
  updateScenario(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
    payload: ScenarioUpdate,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios/${scenarioId}`,
      tenant,
      ScenarioMutationResponseFromJSON,
      { method: "PATCH", body: JSON.stringify(ScenarioUpdateToJSON(payload)) },
    );
  },
  copyScenario(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
    payload: ScenarioCopy,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios/${scenarioId}/copy`,
      tenant,
      ScenarioMutationResponseFromJSON,
      { method: "POST", body: JSON.stringify(ScenarioCopyToJSON(payload)) },
    );
  },
  archiveScenario(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
    payload: ScenarioArchive,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios/${scenarioId}/archive`,
      tenant,
      ScenarioMutationResponseFromJSON,
      { method: "POST", body: JSON.stringify(ScenarioArchiveToJSON(payload)) },
    );
  },
  createAssumption(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
    payload: AssumptionCreate,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios/${scenarioId}/assumptions`,
      tenant,
      ScenarioMutationResponseFromJSON,
      { method: "POST", body: JSON.stringify(AssumptionCreateToJSON(payload)) },
    );
  },
  updateAssumption(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
    assumptionId: string,
    payload: AssumptionUpdate,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios/${scenarioId}/assumptions/${assumptionId}`,
      tenant,
      ScenarioMutationResponseFromJSON,
      {
        method: "PATCH",
        body: JSON.stringify(AssumptionUpdateToJSON(payload)),
      },
    );
  },
  reviewAssumption(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId: string,
    assumptionId: string,
    payload: AssumptionReview,
  ) {
    return apiJson<ScenarioMutationResponse>(
      `/cases/${caseId}/scenarios/${scenarioId}/assumptions/${assumptionId}/review`,
      tenant,
      ScenarioMutationResponseFromJSON,
      { method: "POST", body: JSON.stringify(AssumptionReviewToJSON(payload)) },
    );
  },
  reportJson(tenant: TenantHeaders, caseId: string) {
    return apiJson<RiskReportPayload>(
      `/cases/${caseId}/report`,
      tenant,
      RiskReportPayloadFromJSON,
      { headers: { Accept: "application/json" } },
    );
  },
  reportHtml(tenant: TenantHeaders, caseId: string) {
    return apiText(`/cases/${caseId}/report`, tenant, "text/html");
  },
  decisions(tenant: TenantHeaders, caseId: string) {
    return apiJson<CaseDecisionRead[]>(
      `/cases/${caseId}/decisions`,
      tenant,
      (json) => (json as unknown[]).map(CaseDecisionReadFromJSON),
    );
  },
  createDecision(
    tenant: TenantHeaders,
    caseId: string,
    payload: CaseDecisionCreate,
  ) {
    return apiJson<CaseDecisionRead>(
      `/cases/${caseId}/decisions`,
      tenant,
      CaseDecisionReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify({
          decision: payload.decision,
          reason: payload.reason,
        }),
      },
    );
  },
  documents(tenant: TenantHeaders, caseId: string) {
    return apiJson<DocumentRead[]>(
      `/cases/${caseId}/documents`,
      tenant,
      (json) => (json as unknown[]).map(DocumentReadFromJSON),
    );
  },
  requestUpload(tenant: TenantHeaders, payload: UploadRequest) {
    return apiJson<UploadRequestResponse>(
      "/documents/upload-request",
      tenant,
      UploadRequestResponseFromJSON,
      {
        method: "POST",
        body: JSON.stringify({
          case_id: payload.caseId,
          filename: payload.filename,
          content_type: payload.contentType,
          byte_size: payload.byteSize,
          sha256: payload.sha256 || null,
        }),
      },
    );
  },
  completeUpload(tenant: TenantHeaders, documentId: string) {
    return apiJson<CompleteUploadResponse>(
      `/documents/${documentId}/complete-upload`,
      tenant,
      CompleteUploadResponseFromJSON,
      { method: "POST" },
    );
  },
  downloadUrl(tenant: TenantHeaders, documentId: string) {
    return apiJson<DownloadUrlResponse>(
      `/documents/${documentId}/download-url`,
      tenant,
      DownloadUrlResponseFromJSON,
    );
  },
  parseDocument(tenant: TenantHeaders, documentId: string) {
    return apiJson<ParseResponse>(
      `/documents/${documentId}/parse`,
      tenant,
      ParseResponseFromJSON,
      { method: "POST" },
    );
  },
  parseStatus(tenant: TenantHeaders, documentId: string) {
    return apiJson<ParseStatusResponse>(
      `/documents/${documentId}/parse-status`,
      tenant,
      ParseStatusResponseFromJSON,
    );
  },
  findings(tenant: TenantHeaders, caseId: string) {
    return apiJson<FindingRead[]>(`/cases/${caseId}/findings`, tenant, (json) =>
      (json as unknown[]).map(FindingReadFromJSON),
    );
  },
  createFinding(tenant: TenantHeaders, caseId: string, payload: FindingCreate) {
    return apiJson<FindingRead>(
      `/cases/${caseId}/findings`,
      tenant,
      FindingReadFromJSON,
      {
        method: "POST",
        body: JSON.stringify({
          risk_type: payload.riskType,
          title: payload.title,
          summary: payload.summary,
          severity: payload.severity,
          rationale: payload.rationale,
          likelihood: payload.likelihood,
          impact: payload.impact,
          confidence: payload.confidence,
          details: payload.details,
        }),
      },
    );
  },
  updateFinding(
    tenant: TenantHeaders,
    findingId: string,
    payload: FindingUpdate,
  ) {
    return apiJson<FindingRead>(
      `/findings/${findingId}`,
      tenant,
      FindingReadFromJSON,
      {
        method: "PATCH",
        body: JSON.stringify({
          status: payload.status,
          disposition_reason: payload.dispositionReason,
        }),
      },
    );
  },
};

function bulkPayloadToJson(payload: Payload) {
  if (payload.action === "assign") {
    return {
      action: "assign",
      case_ids: payload.caseIds,
      assigned_to_user_id: payload.assignedToUserId,
    };
  }
  if (payload.action === "update_status") {
    return {
      action: "update_status",
      case_ids: payload.caseIds,
      status: payload.status,
    };
  }
  return {
    action: payload.action,
    case_ids: payload.caseIds,
  };
}

export function isApiError(error: unknown): error is ApiError {
  return (
    typeof error === "object" &&
    error !== null &&
    "statusCode" in error &&
    "code" in error &&
    "message" in error
  );
}
