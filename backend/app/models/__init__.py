from app.ai.models import AiCommentarySettings
from app.attestation.models import (
    AdoptedSignatureAppearance,
    AttestationSignature,
    PackageSignaturePlacement,
    PackageSignatureRecipient,
    RegulatoryArtifactVersion,
    ReturnSignaturePlacement,
    ReturnSigningPolicy,
    SignerIdentity,
    SignerKey,
    SigningAuthorization,
)

# ``AiCommentaryDraft`` is imported for its SIDE EFFECT as much as for the name:
# without it ``ai_commentary_drafts`` is absent from ``Base.metadata`` for any
# caller that imports ``app.models`` rather than the whole app, so
# ``Base.metadata.create_all`` silently builds a schema without it. That is the
# AGENTS.md hazard — anything built with ``create_all`` runs no migration, so it
# must contain what a migration would have written — and it was invisible to the
# hermetic pytest suite, which imports the app and therefore registers the model
# before ``create_all`` runs. It surfaced as ``POST /bi/ask`` answering 500 with
# ``no such table: ai_commentary_drafts`` on the Playwright schema.
from app.bi.models.commentary import AiCommentaryDraft
from app.bi.models.content import (
    BiDashboard,
    BiDashboardShare,
    BiDashboardVersion,
    BiMeasure,
)
from app.bi.models.marts import (
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimGlAccount,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactGlBranchMonthly,
    BiFactGlMonthly,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
    BiMartBuild,
    BiQueryLog,
)
from app.bi.models.notifications import (
    BiAlert,
    BiAlertEvent,
    BiSubscription,
    BiSubscriptionDelivery,
)
from app.case.models.calculation import (
    CalculationForecastPeriod,
    CalculationRun,
    LiquidityAnalysisResult,
)
from app.case.models.capital import CapitalIndicator, CapitalProjection, CapitalProjectionFinding
from app.case.models.financial import (
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialManualEditHistory,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialReportingPeriod,
    FinancialSourceRow,
    FinancialValidationIssue,
)
from app.case.models.risk import (
    Document,
    DocumentChunk,
    DocumentExtraction,
    RiskAssessment,
    RiskAssessmentRun,
    RiskCase,
    RiskCaseDecision,
    RiskFinding,
    RiskFindingEvidence,
    RiskScore,
    StoredObject,
)
from app.case.models.scenario import RiskScenario, ScenarioAssumption, ScenarioAssumptionHistory
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope, RetainedBankKey
from app.data_engine.models.canonical import (
    CanonicalCounterparty,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalGlAccount,
    CanonicalLoanEvent,
    CanonicalMarketIndex,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
)
from app.data_engine.models.canonical_withdrawal import CanonicalWithdrawal
from app.data_engine.models.database_connection import DatabaseDirectConnection
from app.data_engine.models.ingestion import (
    IngestionBatch,
    LineageRecord,
    MappingConfigRecord,
    TranslationFailure,
)
from app.data_engine.models.reconciliation import ReconciliationException
from app.data_engine.models.system_of_record import SystemOfRecordDeclaration
from app.data_engine.models.temenos import TemenosConnection
from app.forecasting.models import ForecastAssumptionVersion
from app.icaap.models.ai import IcaapAiSuggestion, IcaapAiSuggestionDecision
from app.icaap.models.risk_capital import (
    BankSupervisoryAddon,
    IcaapAppetiteMetric,
    IcaapAuditReview,
    IcaapCapitalAllocation,
    IcaapChallenge,
    IcaapChallengeResponse,
    IcaapControlExplanation,
    IcaapPillar2Item,
    IcaapPillar2ItemRevision,
    IcaapRequirementReconciliationLine,
    IcaapResourcesReconciliationLine,
    IcaapRiskAssessment,
)
from app.icaap.models.workspace import (
    IcaapAttachment,
    IcaapAttachmentWithdrawal,
    IcaapBlockBinding,
    IcaapCycle,
    IcaapCycleStage,
    IcaapDataBlock,
    IcaapDisclosure,
    IcaapSection,
    IcaapSectionVersion,
    IcaapStageDecision,
    IcaapWorkflowTemplate,
)
from app.identity.models.authorization import (
    AuthorizationAccessRequest,
    AuthorizationBinding,
    OrganizationOwnerAssignment,
)
from app.identity.models.bank import Bank
from app.identity.models.institution_profile import (
    BankLicense,
    BankNameHistory,
    BankProduct,
    InstitutionProfile,
    Outlet,
    RelatedParty,
    RelatedPartyRole,
    Shareholding,
)
from app.identity.models.integration_key import IntegrationKey
from app.identity.models.refresh_token import RefreshToken
from app.identity.models.sso_connection import SsoConnection
from app.identity.models.user import User
from app.liquidity.models.liquidity_cfp import (
    CfpActivationEvent,
    ContingencyFundingPlan,
    LiquidityEwiIndicator,
)
from app.live.models.facts import FinancialFactRow
from app.live.models.live import (
    CurrentFinancialFact,
    LiveFinding,
    LiveMetric,
    LiveMetricSnapshot,
    WorkerHeartbeat,
)
from app.live.models.regulatory import BankFinancialFact, BankReportingPeriod
from app.live.models.regulatory_run import (
    RegulatoryLineItem,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.models.audit_event import AuditEvent
from app.models.capital_plan import CapitalPlan, IlaapSnapshot
from app.models.desk_operating_environment import DeskOperatingEnvironmentAssessment
from app.models.entitlements import MarketDataEntitlement
from app.models.job import Job
from app.models.market_data import (
    MarketDataConnection,
    MarketDataOverlay,
    MarketDataQuotaUsage,
)
from app.models.market_data_sources import MarketDataSourcePreference
from app.models.market_desk import (
    DeskDetermination,
    DeskMethodology,
    DeskObservation,
    DeskPublication,
    DeskSourceCapture,
)
from app.models.market_desk_curves import DeskCurveDefinition
from app.models.organization import Organization
from app.notifications.models import Notification
from app.operator.models import (
    OperatorAuditLog,
    OperatorInspectorSession,
    OperatorUser,
    TenantStorage,
)
from app.policy.models.institution_type import InstitutionType
from app.policy.models.jurisdiction import Jurisdiction
from app.policy.models.parameter_register import (
    ParamCapitalThreshold,
    ParamConcentrationLimit,
    ParamCreditThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
    ParamLcrRunoffRate,
    ParamLiquidityHaircut,
    ParamLiquidityThreshold,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
)
from app.policy.models.regulatory_parameter import RegulatoryParameter
from app.rating.models import ImpliedRatingRun
from app.reporting.models.filing_workflow import (
    FilingWorkflowTemplate,
    PackageStageDecision,
    PackageWorkflowStage,
)
from app.reporting.models.packages import (
    RegulatoryChannelConfig,
    RegulatoryPackage,
    RegulatoryPackageApproval,
    RegulatoryPackageArtifact,
    RegulatoryPackageAttachment,
    RegulatoryPackageAttachmentWithdrawal,
    RegulatoryReportingSettings,
    RegulatoryResubmissionRequest,
    RegulatorySubmissionEvent,
)
from app.stress.models.scenario_workbench import SavedScenarioAnalysis, StressScenario
from app.stress.models.stress import (
    EnterpriseStressSignoff,
    MacroScenario,
    MacroScenarioPath,
    ManagementActionItem,
    ManagementActionPlan,
)

__all__ = [
    "BankEncryptionKey",
    "RetainedBankKey",
    "ObjectKeyEnvelope",
    "AdoptedSignatureAppearance",
    "AiCommentarySettings",
    "AttestationSignature",
    "AuthorizationBinding",
    "AuthorizationAccessRequest",
    "AuditEvent",
    "Bank",
    "BankFinancialFact",
    "BankSupervisoryAddon",
    "BiAggPositionDaily",
    "BiAlert",
    "BiAlertEvent",
    "AiCommentaryDraft",
    "BiDashboard",
    "BiDashboardShare",
    "BiDashboardVersion",
    "BiDimBranch",
    "BiDimCounterparty",
    "BiDimDate",
    "BiDimGlAccount",
    "BiDimProduct",
    "BiFactEngineMetric",
    "BiFactGlBranchMonthly",
    "BiFactGlMonthly",
    "BiFactLoanEvent",
    "BiFactPositionDaily",
    "BiFactPositionEom",
    "BiMartBuild",
    "BiMeasure",
    "BiQueryLog",
    "BiSubscription",
    "BiSubscriptionDelivery",
    "FinancialFactRow",
    "ForecastAssumptionVersion",
    "IcaapAiSuggestion",
    "IcaapAiSuggestionDecision",
    "IcaapAppetiteMetric",
    "IcaapAttachment",
    "IcaapAttachmentWithdrawal",
    "IcaapAuditReview",
    "IcaapBlockBinding",
    "IcaapCapitalAllocation",
    "IcaapChallenge",
    "IcaapChallengeResponse",
    "IcaapControlExplanation",
    "IcaapCycle",
    "IcaapCycleStage",
    "IcaapDataBlock",
    "IcaapDisclosure",
    "IcaapPillar2Item",
    "IcaapPillar2ItemRevision",
    "IcaapRequirementReconciliationLine",
    "IcaapResourcesReconciliationLine",
    "IcaapRiskAssessment",
    "IcaapSection",
    "IcaapSectionVersion",
    "IcaapStageDecision",
    "IcaapWorkflowTemplate",
    "BankLicense",
    "BankNameHistory",
    "BankProduct",
    "BankReportingPeriod",
    "CanonicalCounterparty",
    "CanonicalCounterpartyRating",
    "CanonicalFxRate",
    "CanonicalGlAccount",
    "CanonicalLoanEvent",
    "CanonicalMarketIndex",
    "CanonicalPosition",
    "CanonicalWithdrawal",
    "CanonicalPositionSnapshot",
    "CanonicalProduct",
    "CanonicalReferenceRow",
    "CanonicalYieldCurve",
    "CanonicalYieldCurvePoint",
    "CapitalIndicator",
    "CapitalProjection",
    "CapitalProjectionFinding",
    "CurrentFinancialFact",
    "CapitalPlan",
    "IlaapSnapshot",
    "CalculationForecastPeriod",
    "CalculationRun",
    "LiquidityAnalysisResult",
    "Document",
    "DocumentChunk",
    "DocumentExtraction",
    "FinancialAccount",
    "FinancialBalance",
    "FinancialCashFlow",
    "FinancialCovenant",
    "FinancialInstitution",
    "FinancialManualEditHistory",
    "FinancialObligation",
    "FinancialRecordSourceLink",
    "FinancialReportingPeriod",
    "FinancialSourceRow",
    "FinancialValidationIssue",
    "IngestionBatch",
    "InstitutionProfile",
    "InstitutionType",
    "RegulatoryParameter",
    "Job",
    "Jurisdiction",
    "LineageRecord",
    "LiveFinding",
    "LiveMetric",
    "LiveMetricSnapshot",
    "WorkerHeartbeat",
    "MappingConfigRecord",
    "DatabaseDirectConnection",
    "MarketDataConnection",
    "MarketDataEntitlement",
    "MarketDataOverlay",
    "MarketDataQuotaUsage",
    "MarketDataSourcePreference",
    "DeskCurveDefinition",
    "DeskDetermination",
    "DeskOperatingEnvironmentAssessment",
    "DeskMethodology",
    "DeskObservation",
    "DeskPublication",
    "DeskSourceCapture",
    "CfpActivationEvent",
    "ContingencyFundingPlan",
    "LiquidityEwiIndicator",
    "Notification",
    "IntegrationKey",
    "ImpliedRatingRun",
    "OperatorAuditLog",
    "OperatorInspectorSession",
    "OperatorUser",
    "Organization",
    "OrganizationOwnerAssignment",
    "TenantStorage",
    "PackageSignaturePlacement",
    "PackageSignatureRecipient",
    "RegulatoryArtifactVersion",
    "ReturnSignaturePlacement",
    "ReturnSigningPolicy",
    "SignerIdentity",
    "SignerKey",
    "SigningAuthorization",
    "Outlet",
    "TemenosConnection",
    "TranslationFailure",
    "ParamCapitalThreshold",
    "ParamConcentrationLimit",
    "ParamCreditThreshold",
    "ParamCrmHaircut",
    "ParamEclAssumption",
    "ParamLiquidityHaircut",
    "ParamLiquidityThreshold",
    "ParamLcrRunoffRate",
    "ParamNsfrWeight",
    "ParamRiskWeight",
    "ParamStressShock",
    "RegulatoryChannelConfig",
    "ReconciliationException",
    "SystemOfRecordDeclaration",
    "RefreshToken",
    "RegulatoryLineItem",
    "RegulatoryMetricResult",
    "RegulatoryPackage",
    "FilingWorkflowTemplate",
    "PackageStageDecision",
    "PackageWorkflowStage",
    "RegulatoryPackageApproval",
    "RegulatoryPackageArtifact",
    "RegulatoryPackageAttachment",
    "RegulatoryPackageAttachmentWithdrawal",
    "RegulatoryReportingSettings",
    "RegulatoryResubmissionRequest",
    "RegulatoryRun",
    "RegulatorySubmissionEvent",
    "RegulatoryValidation",
    "EnterpriseStressSignoff",
    "MacroScenario",
    "MacroScenarioPath",
    "ManagementActionItem",
    "ManagementActionPlan",
    "SavedScenarioAnalysis",
    "StressScenario",
    "RelatedParty",
    "RelatedPartyRole",
    "RiskAssessment",
    "RiskAssessmentRun",
    "RiskCase",
    "RiskCaseDecision",
    "RiskFinding",
    "RiskFindingEvidence",
    "RiskScore",
    "RiskScenario",
    "ScenarioAssumption",
    "ScenarioAssumptionHistory",
    "Shareholding",
    "SsoConnection",
    "StoredObject",
    "User",
]
