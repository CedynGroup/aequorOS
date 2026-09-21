/**
 * The adapter between the GENERATED ICAAP FILING contract and what the review
 * and filing screens can render.
 *
 * Same job, same rules and the same reasons as
 * `icaapRiskCapitalNormalize.ts` — read its header first. This file is its
 * counterpart for P3: the stage timeline, the freeze preflight, the filing
 * panel, the package's attached documents, the bank's review-chain template
 * and the ¶82 disclosure.
 *
 * Everything here is TOTAL: it accepts `unknown`, so a body that omits a
 * field, sends `null` where a list was declared, or is not the expected body
 * at all cannot reach the rendering code. The view types are INFERRED from
 * these functions, so a field cannot be added to a screen without a rule for
 * filling it.
 *
 * THE FAIL-CLOSED RULES, and what each one is protecting a reader from:
 *
 *   - an absent list is EMPTY, an absent object is NULL;
 *   - **an unknown or absent `canSubmit`/`canDecide`/`canReturn`/`canFreeze`
 *     is FALSE.** A control offered to somebody the server will refuse is a
 *     maker-checker violation the reader only learns about after pressing it;
 *   - **`submittable` is FALSE until the server says otherwise**, and a
 *     `ready` preflight is `false` by the same rule. "This report can be
 *     filed" is the single most consequential sentence in the workspace;
 *   - **an unrecognised preflight severity is `blocking`** — the strictest,
 *     never `info`. A refusal nobody has taught this screen to read must not
 *     render as a note;
 *   - **a signature slot is REQUIRED unless the server says it is optional,
 *     and `signedBy` absent means UNSIGNED.** Neither may be inferred;
 *   - **an attachment requirement is UNSATISFIED unless the server says it is
 *     satisfied**, and `applies` defaults to true: a document requirement the
 *     payload is vague about is one the reader must still meet;
 *   - **a disclosure section is NOT selected and NOT selectable by default.**
 *     The ¶82 disclosure is published on the bank's website; absence of a
 *     flag must never read as permission to publish;
 *   - a stage decision `stillStands` defaults to FALSE, so a decision a later
 *     send-back reopened is never drawn as if it still counts.
 *
 * D-024 — no regulatory number is declared here. Every count, minimum and
 * deadline arrives on a payload.
 *
 * Kept free of React and of every runtime import (the type imports are elided
 * at emit) so it runs under node in `pnpm --filter @aequoros/dashboard test`.
 */

import type {
  IcaapDisclosureRead as WireDisclosure,
  IcaapFilingRead as WireFiling,
  IcaapPreflightRead as WirePreflight,
  IcaapStagesRead as WireStages,
  IcaapWorkflowTemplateListRead as WireWorkflowTemplates,
  PackageAttachmentListRead as WirePackageAttachments,
  RegulatoryArtifactRead as WireArtifact,
} from "@aequoros/risk-service-api";

// ---------------------------------------------------------------------------
// Primitives. Every one is total.
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function mapList<T>(
  value: unknown,
  map: (row: Record<string, unknown>) => T,
): T[] {
  return (Array.isArray(value) ? value : [])
    .filter(
      (row): row is Record<string, unknown> =>
        typeof row === "object" && row !== null && !Array.isArray(row),
    )
    .map(map);
}

function asText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value !== "" ? value : fallback;
}

function asNullableText(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** A date or timestamp, as text. The generated client materialises `Date`. */
function asMoment(value: unknown): string | null {
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value.toISOString() : null;
  }
  return asNullableText(value);
}

/** A whole number, or null. Never invented. */
function asCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** A required tally. Absent reads as 0 ITEMS — a count, never a measurement. */
function asTally(value: unknown): number {
  return asCount(value) ?? 0;
}

/** True only when the server said true. Every capability flag uses this. */
function asFlag(value: unknown): boolean {
  return value === true;
}

/** False only when the server said false. Used where absence must not relax. */
function asFlagDefaultTrue(value: unknown): boolean {
  return value !== false;
}

function asTextList(value: unknown): string[] {
  return (Array.isArray(value) ? value : []).filter(
    (entry): entry is string => typeof entry === "string",
  );
}

/** One of a closed set, or the fail-closed fallback. */
function asOneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T,
): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

// ---------------------------------------------------------------------------
// The closed vocabularies, mirrored from the API's own Literals
// ---------------------------------------------------------------------------

export const ICAAP_STAGE_KINDS = [
  "prepare",
  "review",
  "approve",
  "attest",
] as const;
export type IcaapStageKind = (typeof ICAAP_STAGE_KINDS)[number];

export const ICAAP_STAGE_STATES = [
  "pending",
  "current",
  "done",
  "returned",
] as const;
export type IcaapStageState = (typeof ICAAP_STAGE_STATES)[number];

export const ICAAP_SEVERITIES = ["blocking", "warning", "info"] as const;
export type IcaapSeverity = (typeof ICAAP_SEVERITIES)[number];

export const ICAAP_SCOPES = [
  "cycle",
  "section",
  "requirement",
  "block",
  "attachment",
] as const;
export type IcaapScope = (typeof ICAAP_SCOPES)[number];

export const ICAAP_TEMPLATE_STATUSES = [
  "draft",
  "pending_approval",
  "approved",
  "published",
  "rejected",
  "superseded",
] as const;
export type IcaapTemplateStatus = (typeof ICAAP_TEMPLATE_STATUSES)[number];

export const ICAAP_DISCLOSURE_STATUSES = [
  "draft",
  "pending_approval",
  "approved",
  "published",
  "rejected",
  "superseded",
] as const;
export type IcaapDisclosureStatus =
  (typeof ICAAP_DISCLOSURE_STATUSES)[number];

export const ICAAP_CHAIN_SOURCES = [
  "framework_default",
  "bank_template",
] as const;
export type IcaapChainSource = (typeof ICAAP_CHAIN_SOURCES)[number];

// ---------------------------------------------------------------------------
// The review chain
// ---------------------------------------------------------------------------

function stageDecision(row: Record<string, unknown>) {
  return {
    id: asText(row.id),
    stageSeq: asCount(row.stageSeq),
    stageKey: asText(row.stageKey),
    round: asCount(row.round),
    /**
     * The server's own word for what was decided. Rendered through
     * `decisionCopy`, never printed raw: `reviewed` is not a sentence a Head of
     * Risk should meet on a regulatory screen.
     */
    decision: asText(row.decision),
    returnToSeq: asCount(row.returnToSeq),
    reviewDigest: asNullableText(row.reviewDigest),
    packageId: asNullableText(row.packageId),
    comment: asNullableText(row.comment),
    decidedBy: asText(row.decidedBy),
    decidedByName: asText(row.decidedByName),
    officerTitle: asNullableText(row.officerTitle),
    createdAt: asMoment(row.createdAt),
    // Fail closed: a decision the payload is vague about is one a later
    // send-back may have reopened, and drawing it as current would tell a
    // reviewer the stage is settled when it is not.
    stillStands: asFlag(row.stillStands),
  };
}

function stage(row: Record<string, unknown>) {
  return {
    seq: asTally(row.seq),
    stageKey: asText(row.stageKey),
    title: asText(row.title),
    kind: asOneOf(row.decisionKind, ICAAP_STAGE_KINDS, "review"),
    officerTitles: asTextList(row.officerTitles),
    freezeOnApprove: asFlag(row.freezeOnApprove),
    state: asOneOf(row.state, ICAAP_STAGE_STATES, "pending"),
    decisions: mapList(row.decisions, stageDecision),
  };
}

export function normalizeStages(value: unknown) {
  const source = asRecord(value) as Partial<WireStages> &
    Record<string, unknown>;
  const viewer = asRecord(source.viewer);
  return {
    cycleId: asNullableText(source.cycleId),
    status: asText(source.status),
    round: asCount(source.round),
    currentStageSeq: asCount(source.currentStageSeq),
    awaitingFreeze: asFlag(source.awaitingFreeze),
    source: asOneOf(source.source, ICAAP_CHAIN_SOURCES, "framework_default"),
    reviewDigest: asNullableText(source.reviewDigest),
    stages: mapList(source.stages, stage),
    /**
     * What THIS caller may do. Every flag fails closed, so a payload the
     * screen cannot read offers no control at all — the server re-decides
     * every one of these anyway, and an offered-then-refused control is how a
     * preparer learns about separation of duties the hard way.
     */
    viewer: {
      canSubmit: asFlag(viewer.canSubmit),
      canDecide: asFlag(viewer.canDecide),
      canReturn: asFlag(viewer.canReturn),
      canFreeze: asFlag(viewer.canFreeze),
      blockedReason: asNullableText(viewer.blockedReason),
    },
  };
}
export type IcaapStages = ReturnType<typeof normalizeStages>;
export type IcaapStage = IcaapStages["stages"][number];
export type IcaapStageDecision = IcaapStage["decisions"][number];

// ---------------------------------------------------------------------------
// Preflight and blockers
// ---------------------------------------------------------------------------

function preflightItem(row: Record<string, unknown>) {
  return {
    code: asText(row.code),
    // The strictest reading wins. A severity this build has never seen is a
    // refusal it cannot classify, and a refusal drawn as a note is worse than
    // one drawn too loudly.
    severity: asOneOf(row.severity, ICAAP_SEVERITIES, "blocking"),
    scope: asOneOf(row.scope, ICAAP_SCOPES, "cycle"),
    ref: asNullableText(row.ref),
    /** The server's own sentence. Never replaced, only ever fallen back on. */
    message: asText(row.message),
  };
}

export function normalizePreflight(value: unknown) {
  const source = asRecord(value) as Partial<WirePreflight> &
    Record<string, unknown>;
  return {
    cycleId: asNullableText(source.cycleId),
    // Fail closed: nothing is ready until the server says it is.
    ready: asFlag(source.ready),
    reviewDigest: asNullableText(source.reviewDigest),
    items: mapList(source.items, preflightItem),
  };
}
export type IcaapPreflight = ReturnType<typeof normalizePreflight>;
export type IcaapPreflightItem = IcaapPreflight["items"][number];

// ---------------------------------------------------------------------------
// The filing view
// ---------------------------------------------------------------------------

function packageSummary(value: unknown) {
  const row = asRecord(value);
  if (Object.keys(row).length === 0) return null;
  const id = asNullableText(row.id);
  if (id === null) return null;
  return {
    id,
    returnCode: asText(row.returnCode),
    returnFamily: asText(row.returnFamily),
    reportingDate: asMoment(row.reportingDate),
    basis: asText(row.basis),
    status: asText(row.status),
    version: asCount(row.version),
    contentDigest: asNullableText(row.contentDigest),
    generatedAt: asMoment(row.generatedAt),
  };
}

function filingSlot(row: Record<string, unknown>) {
  return {
    role: asText(row.role),
    // A slot the payload is vague about is one the reader must still fill.
    required: asFlagDefaultTrue(row.required),
    /** The officer line the framework prints beside the signature. */
    line: asNullableText(row.line),
    /** What this officer is attesting to, in the framework's own words. */
    statement: asNullableText(row.statement),
    signedBy: asNullableText(row.signedBy),
    signedAt: asMoment(row.signedAt),
    /**
     * The ROLE that must sign first — the server's word, e.g. `approver`.
     * Rendered through `roleLabel`, never printed raw.
     */
    blockedBy: asNullableText(row.blockedBy),
  };
}

function filingAttachment(row: Record<string, unknown>) {
  return {
    kind: asText(row.kind),
    title: asText(row.title),
    gate: asText(row.gate),
    minCount: asTally(row.minCount),
    present: asTally(row.present),
    applies: asFlagDefaultTrue(row.applies),
    satisfied: asFlag(row.satisfied),
  };
}

export function normalizeFiling(value: unknown) {
  const source = asRecord(value) as Partial<WireFiling> &
    Record<string, unknown>;
  return {
    cycleId: asNullableText(source.cycleId),
    // The generator names the field `_package`, because `package` is reserved.
    // Both spellings are read so the view does not depend on which one a
    // future regeneration emits.
    package: packageSummary(source._package ?? source.package),
    attestationState: asText(source.attestationState),
    slots: mapList(source.slots, filingSlot),
    attachments: mapList(source.attachments, filingAttachment),
    // The most consequential default in this file.
    submittable: asFlag(source.submittable),
    blockers: mapList(source.blockers, preflightItem),
  };
}
export type IcaapFiling = ReturnType<typeof normalizeFiling>;
export type IcaapFilingSlot = IcaapFiling["slots"][number];
export type IcaapFilingAttachment = IcaapFiling["attachments"][number];
export type IcaapPackageSummary = NonNullable<IcaapFiling["package"]>;

// ---------------------------------------------------------------------------
// Documents filed WITH the return
// ---------------------------------------------------------------------------

function packageAttachment(row: Record<string, unknown>) {
  return {
    id: asText(row.id),
    kind: asText(row.kind),
    title: asText(row.title),
    gate: asText(row.gate),
    source: asText(row.source),
    originalFilename: asText(row.originalFilename),
    mediaType: asText(row.mediaType),
    byteSize: asCount(row.byteSize),
    sha256: asNullableText(row.sha256),
    attachedBy: asText(row.attachedBy),
    createdAt: asMoment(row.createdAt),
    packageVersion: asCount(row.packageVersion),
    attributes: asRecord(row.attributes),
    withdrawn: asFlag(row.withdrawn),
    withdrawnAt: asMoment(row.withdrawnAt),
    withdrawalReason: asNullableText(row.withdrawalReason),
  };
}

function attachmentRequirement(row: Record<string, unknown>) {
  return {
    kind: asText(row.kind),
    title: asText(row.title),
    gate: asText(row.gate),
    requiredCount: asTally(row.requiredCount),
    activeCount: asTally(row.activeCount),
    // Fail closed: a requirement is outstanding until the server says it is met.
    satisfied: asFlag(row.satisfied),
    /**
     * `family` — the regime itself asks for this document, and relaxing the
     * signing policy does NOT relax it (D-031/M11) — or `signing_policy`,
     * which the bank set for itself. The screen says which, because the two
     * are answerable by different people.
     */
    origin: asOneOf(
      row.origin,
      ["family", "signing_policy"] as const,
      "family",
    ),
  };
}

export function normalizePackageAttachments(value: unknown) {
  const source = asRecord(value) as Partial<WirePackageAttachments> &
    Record<string, unknown>;
  return {
    packageId: asNullableText(source.packageId),
    attachments: mapList(source.attachments, packageAttachment),
    requirements: mapList(source.requirements, attachmentRequirement),
  };
}
export type PackageAttachments = ReturnType<typeof normalizePackageAttachments>;
export type PackageAttachment = PackageAttachments["attachments"][number];
export type PackageAttachmentRequirement =
  PackageAttachments["requirements"][number];

// ---------------------------------------------------------------------------
// The files a frozen package carries
// ---------------------------------------------------------------------------

function artifact(row: Record<string, unknown>) {
  return {
    id: asText(row.id),
    packageId: asText(row.packageId),
    kind: asText(row.kind),
    /** The stored object's path. The download helper names the file from it. */
    objectPath: asText(row.objectPath),
    checksum: asNullableText(row.checksumSha256),
    sizeBytes: asCount(row.sizeBytes),
    createdAt: asMoment(row.createdAt),
  };
}

export function normalizeArtifacts(value: unknown) {
  return mapList(
    Array.isArray(value) ? value : asRecord(value).artifacts,
    artifact,
  );
}
export type PackageArtifact = ReturnType<typeof normalizeArtifacts>[number];

function artifactVersion(row: Record<string, unknown>) {
  const signature = asRecord(row.signedBy);
  return {
    id: asText(row.id),
    kind: asText(row.kind),
    objectPath: asText(row.objectPath),
    /** The revision the regulator receives. Absent must never read as filed. */
    isFiled: asFlag(row.isFiled),
    isLatest: asFlag(row.isLatest),
    /** Null on the base export; the officer whose signature covers these bytes. */
    signedByName: asNullableText(signature.signerDisplayName),
    signedByTitle: asNullableText(signature.officerTitle),
    signedAt: asMoment(signature.signedAt),
    checksum: asNullableText(row.checksumSha256),
    sizeBytes: asCount(row.sizeBytes),
    createdAt: asMoment(row.createdAt),
  };
}

export function normalizeArtifactVersions(value: unknown) {
  return mapList(
    Array.isArray(value) ? value : asRecord(value).versions,
    artifactVersion,
  );
}
export type PackageArtifactVersion = ReturnType<
  typeof normalizeArtifactVersions
>[number];

// ---------------------------------------------------------------------------
// The bank's review chain (settings)
// ---------------------------------------------------------------------------

function stageInput(row: Record<string, unknown>) {
  return {
    seq: asTally(row.seq),
    stageKey: asText(row.stageKey),
    title: asText(row.title),
    kind: asOneOf(row.decisionKind, ICAAP_STAGE_KINDS, "review"),
    officerTitles: asTextList(row.officerTitles),
    freezeOnApprove: asFlag(row.freezeOnApprove),
  };
}

function workflowTemplate(row: Record<string, unknown>) {
  return {
    id: asText(row.id),
    version: asCount(row.version),
    status: asOneOf(row.status, ICAAP_TEMPLATE_STATUSES, "draft"),
    stages: mapList(row.stages, stageInput),
    frameworkCode: asNullableText(row.frameworkCode),
    frameworkVersion: asNullableText(row.frameworkVersion),
    reason: asText(row.reason),
    proposedBy: asText(row.proposedBy),
    submittedAt: asMoment(row.submittedAt),
    decidedBy: asNullableText(row.decidedBy),
    decidedAt: asMoment(row.decidedAt),
    decisionReason: asNullableText(row.decisionReason),
    supersededAt: asMoment(row.supersededAt),
    createdAt: asMoment(row.createdAt),
  };
}

export function normalizeWorkflowTemplates(value: unknown) {
  const source = asRecord(value) as Partial<WireWorkflowTemplates> &
    Record<string, unknown>;
  return {
    templates: mapList(source.templates, workflowTemplate),
    /** The chain a cycle submitted TODAY would pin, whatever its source. */
    effectiveStages: mapList(source.effectiveStages, stageInput),
    effectiveSource: asOneOf(
      source.effectiveSource,
      ICAAP_CHAIN_SOURCES,
      "framework_default",
    ),
  };
}
export type IcaapWorkflowTemplates = ReturnType<
  typeof normalizeWorkflowTemplates
>;
export type IcaapWorkflowTemplate = IcaapWorkflowTemplates["templates"][number];
export type IcaapStageInput = IcaapWorkflowTemplates["effectiveStages"][number];

// ---------------------------------------------------------------------------
// The ¶82 public disclosure
// ---------------------------------------------------------------------------

function disclosureSection(row: Record<string, unknown>) {
  return {
    key: asText(row.key),
    title: asText(row.title),
    // Both fail closed. Publication is irreversible in the way that matters:
    // once a figure is on the bank's website, withdrawing it does not unpublish
    // it. Absence of a flag is never permission.
    selected: asFlag(row.selected),
    selectable: asFlag(row.selectable),
  };
}

function withheld(row: Record<string, unknown>) {
  return {
    sectionKey: asText(row.sectionKey),
    blockKey: asText(row.blockKey),
    blockType: asText(row.blockType),
    nodeType: asText(row.nodeType),
    factKey: asNullableText(row.factKey),
  };
}

export function normalizeDisclosure(value: unknown) {
  const source = asRecord(value) as Partial<WireDisclosure> &
    Record<string, unknown>;
  return {
    id: asNullableText(source.id),
    cycleId: asNullableText(source.cycleId),
    status: asOneOf(source.status, ICAAP_DISCLOSURE_STATUSES, "draft"),
    sourcePackageId: asNullableText(source.sourcePackageId),
    packageId: asNullableText(source.packageId),
    // Fail closed, as P2's allocation does: an unfinished payload never
    // invites a preparer to publish.
    available: asFlag(source.available),
    /** The server's own sentence for why not. Shown verbatim. */
    unavailableReason: asNullableText(source.unavailableReason),
    sections: mapList(source.sections, disclosureSection),
    withheld: mapList(source.withheld, withheld),
    proposedBy: asNullableText(source.proposedBy),
    proposedAt: asMoment(source.proposedAt),
    decidedBy: asNullableText(source.decidedBy),
    decidedAt: asMoment(source.decidedAt),
    decisionReason: asNullableText(source.decisionReason),
    publishedUrl: asNullableText(source.publishedUrl),
    publishedOn: asMoment(source.publishedOn),
  };
}
export type IcaapDisclosure = ReturnType<typeof normalizeDisclosure>;
export type IcaapDisclosureSection = IcaapDisclosure["sections"][number];
export type IcaapDisclosureWithheld = IcaapDisclosure["withheld"][number];

/** Kept so a reader of the imports can see which wire models this mirrors. */
export type { WireArtifact };
