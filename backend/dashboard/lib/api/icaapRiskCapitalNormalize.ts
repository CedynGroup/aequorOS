/**
 * The adapter between the GENERATED ICAAP contract and what a screen can render.
 *
 * Two problems it solves, and they are different problems.
 *
 * 1. THE CRASH. The risks tab threw `Cannot read properties of undefined
 *    (reading 'length')` on `register.parameters.length` during the founder's
 *    walkthrough. A type is a promise about a payload, not a property of one:
 *    the generated models declare most fields OPTIONAL (`parameters?`,
 *    `violations?`, `staleReasons?`, every amount as `string | null`), which is
 *    honest about the wire and useless to a component that has to render
 *    something. Everything here is total — it accepts `unknown` — so a body
 *    that omits a field, sends `null` where a list was declared, or is not the
 *    expected body at all cannot reach the rendering code.
 *
 * 2. THE VOCABULARY. The wire calls a metric's name `label`, an item's id `id`,
 *    a revision `currentRevisionNo`, and a direction `floor`/`ceiling`. The
 *    screens speak in `title`, `itemId`, `revisionNo`, `higher_is_safer`. One
 *    translation, in one file, keeps that mapping out of thirteen components —
 *    and keeps the generated models as the single source of the wire's truth.
 *
 * THE FAIL-CLOSED RULES these encode, which the generated types cannot:
 *   - an absent list is EMPTY; an absent object is NULL;
 *   - an absent figure STAYS ABSENT. Nothing here invents a number, so a
 *     missing amount is `null` and renders "Not modelled", never 0 (which would
 *     assert a measured result), and a missing threshold is `null` so the
 *     matrix caption says so rather than printing a bound nobody set (D-024).
 *     This is why the view types below are not the generated ones: the
 *     generated `materialMinScore` is a required `number`, and there is no
 *     honest number to put there when the control plane governs none;
 *   - an unknown RAG is "none" and NEVER green;
 *   - an unknown consistency verdict is "cannot be compared", never "agrees";
 *   - an action flag the server did not send is false, so no control is offered
 *     that the server did not allow.
 *
 * The view types are INFERRED from these functions (`ReturnType<…>`), so the
 * adapter is the schema: a field cannot be added to the view without a rule for
 * filling it.
 *
 * Kept free of React and of every runtime import — the type imports are elided
 * at emit — so it runs under node in `pnpm --filter @aequoros/dashboard test`.
 */

import type {
  IcaapAllocationRead as WireAllocation,
  IcaapAppetiteRead as WireAppetite,
  IcaapAuditReviewListRead as WireAuditReviews,
  IcaapChallengeListRead as WireChallenges,
  IcaapDataBlockListRead as WireDataBlocks,
  IcaapParameterUseListRead as WireParameterUses,
  IcaapPillar2RegisterRead as WirePillar2Register,
  IcaapPillar2RevisionListRead as WirePillar2Revisions,
  IcaapReconciliationRead as WireReconciliation,
  IcaapRiskRegisterRead as WireRiskRegister,
  IcaapSupervisoryAddonListRead as WireSupervisoryAddons,
  IcaapTable5Read as WireTable5,
  IcaapTriggerEvaluationRead as WireTriggers,
} from "@aequoros/risk-service-api";

// ---------------------------------------------------------------------------
// Primitives. Every one is total.
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Map a list of records, dropping entries that are not objects. */
function mapList<T>(value: unknown, map: (row: Record<string, unknown>) => T): T[] {
  return (Array.isArray(value) ? value : [])
    .filter(
      (row): row is Record<string, unknown> =>
        typeof row === "object" && row !== null && !Array.isArray(row),
    )
    .map(map);
}

/** A string, or the fallback. An absent label is never `undefined` on screen. */
function asText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value !== "" ? value : fallback;
}

/**
 * A figure, kept as the string the backend sent, or null.
 *
 * Numbers are stringified rather than parsed: the wire form is a Decimal string
 * and the fail-closed formatters accept either, so nothing is rounded here.
 */
function asFigure(value: unknown): string | null {
  if (typeof value === "string") return value === "" ? null : value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function asCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** A required tally. Absent reads as 0 items — a count, never a measurement. */
function asTally(value: unknown): number {
  return asCount(value) ?? 0;
}

function asFlag(value: unknown): boolean {
  return value === true;
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

function asTextList(value: unknown): string[] {
  return (Array.isArray(value) ? value : []).filter(
    (entry): entry is string => typeof entry === "string",
  );
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

/**
 * An opaque object the wire types as `dict[str, Any]` — a computation's
 * working, a revision's snapshot — flattened into printable label/value rows.
 *
 * Nothing here pretends to know what a key means: the keys are the engine's own
 * and are shown as it wrote them. Returns null when there is no object at all,
 * so a screen can say "not recorded" rather than render an empty table.
 */
function flattenDetail(value: unknown): { label: string; value: string | null }[] | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return Object.entries(value as Record<string, unknown>).map(([label, entry]) => ({
    label,
    value:
      entry === null || entry === undefined
        ? null
        : typeof entry === "object"
          ? JSON.stringify(entry)
          : String(entry),
  }));
}

// ---------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------

function normalizeParameterUse(row: Record<string, unknown>) {
  return {
    paramCode: asText(row.paramCode),
    value: asFigure(row.value),
    valueJson:
      typeof row.valueJson === "object" && row.valueJson !== null
        ? (row.valueJson as Record<string, unknown>)
        : null,
    unit: asNullableText(row.unit),
    /** Unknown is "pending": an unconfirmed value must never read as confirmed. */
    confirmationStatus: row.confirmationStatus === "confirmed" ? "confirmed" : "pending",
    representative: asFlag(row.representative),
    /** False when the control plane governs no value for this code at all. */
    resolved: row.resolved !== false,
    sourceCitation: asNullableText(row.sourceCitation),
    effectiveFrom: asMoment(row.effectiveFrom),
    parameterId: asNullableText(row.parameterId),
    /** What this cycle uses the value FOR, in the server's own words. */
    roles: asTextList(row.roles),
  };
}

export type IcaapParameterUse = ReturnType<typeof normalizeParameterUse>;

function normalizeParameters(value: unknown): IcaapParameterUse[] {
  return mapList(value, normalizeParameterUse);
}

const CONSISTENCY_STATUSES = [
  "consistent",
  "inconsistent",
  "not_comparable",
  "both_absent",
] as const;

function normalizeConsistency(row: Record<string, unknown>) {
  return {
    comparisonKey: asText(row.comparisonKey),
    basis: asText(row.basis),
    comparator: asText(row.comparator),
    row: asText(row.row),
    icaap: asFigure(row.icaap),
    other: asFigure(row.other),
    relativeDiffPct: asFigure(row.relativeDiffPct),
    // An unknown verdict is "cannot be compared" — never "agrees".
    status: oneOf(row.status, CONSISTENCY_STATUSES, "not_comparable"),
    explanation: asNullableText(row.explanation),
    explanationCurrent: row.explanationCurrent !== false,
  };
}

export type IcaapConsistency = ReturnType<typeof normalizeConsistency>;

// ---------------------------------------------------------------------------
// Risk register
// ---------------------------------------------------------------------------

const VERDICTS = ["unassessed", "material", "not_material"] as const;

function normalizeMatrix(value: unknown) {
  const matrix = asRecord(value);
  const bandLabels = new Map(
    mapList(matrix.bands, (row) => [asText(row.key), asText(row.label, asText(row.key))] as const),
  );
  const level = (row: Record<string, unknown>) => ({
    score: asTally(row.score),
    key: asText(row.key),
    label: asText(row.label, asText(row.key)),
  });
  return {
    likelihoodLevels: mapList(matrix.likelihoodLevels, level),
    impactLevels: mapList(matrix.impactLevels, level),
    bands: mapList(matrix.bands, (row) => ({
      key: asText(row.key),
      label: asText(row.label, asText(row.key)),
      minScore: asTally(row.minScore),
      maxScore: asTally(row.maxScore),
    })),
    // NULLABLE on purpose, unlike the wire's required `number`: an ungoverned
    // threshold must reach the caption as absence (D-024).
    materialMinScore: asCount(matrix.materialMinScore),
    materialMinImpact: asCount(matrix.materialMinImpact),
    cells: mapList(matrix.cells, (row) => ({
      likelihood: asTally(row.likelihood),
      impact: asTally(row.impact),
      score: asTally(row.score),
      ratingKey: asText(row.ratingKey),
      material: asFlag(row.material),
    })).map((cell) => ({
      ...cell,
      // The BAND's own label, resolved once here. A component that looked it up
      // per cell would be re-deriving framework data in the render path.
      ratingLabel:
        bandLabels.get(cell.ratingKey) ?? (cell.ratingKey === "" ? null : cell.ratingKey),
    })),
    thresholdsDigest: asNullableText(matrix.thresholdsDigest),
    parameters: normalizeParameters(matrix.parameters),
  };
}

export type IcaapMaterialityMatrix = ReturnType<typeof normalizeMatrix>;

export function normalizeRiskRegister(value: WireRiskRegister | unknown) {
  const body = asRecord(value);
  const matrix = normalizeMatrix(body.matrix);
  const summary = asRecord(body.summary);

  /** The band's own label for a rating key. Never a locally invented name. */
  const ratingLabel = (key: string): string | null =>
    matrix.bands.find((band) => band.key === key)?.label ?? null;

  return {
    cycleId: asText(body.cycleId),
    matrix,
    risks: mapList(body.risks, (row) => {
      const key = asText(row.ratingKey);
      const verdict = oneOf(row.verdict, VERDICTS, "unassessed");
      return {
        riskKey: asText(row.riskKey),
        categoryKey: asText(row.categoryKey),
        title: asText(row.title, asText(row.riskKey)),
        isCustom: asFlag(row.isCustom),
        /** False until the bank has saved an assessment for this category. */
        stored: asFlag(row.stored),
        description: asNullableText(row.description),
        likelihoodScore: asCount(row.likelihoodScore),
        impactScore: asCount(row.impactScore),
        controlsSummary: asNullableText(row.controlsSummary),
        materialityScore: asCount(row.materialityScore),
        ratingKey: key === "" ? null : key,
        ratingLabel: key === "" ? null : ratingLabel(key),
        // "unassessed" is the wire's third verdict; the screens render absence.
        matrixVerdict:
          oneOf(row.matrixVerdict, VERDICTS, "unassessed") === "unassessed"
            ? null
            : oneOf(row.matrixVerdict, VERDICTS, "unassessed"),
        verdict: verdict === "unassessed" ? null : verdict,
        verdictSource: row.verdictSource === "override" ? "override" : "matrix",
        overrideReason: asNullableText(row.overrideReason),
        materialityRationale: asNullableText(row.materialityRationale),
        ownerFunction: asNullableText(row.ownerFunction),
        rowRev: asTally(row.rowRev),
        // Absent means "we were not told the thresholds moved", which must not
        // raise a warning the server did not raise.
        thresholdsCurrent: row.thresholdsCurrent !== false,
        retiredAt: asMoment(row.retiredAt),
        components: mapList(row.components, (component) => ({
          componentKey: asText(component.componentKey),
          itemId: asNullableText(component.itemId),
          itemKey: asNullableText(component.itemKey),
          method: asNullableText(component.method),
          methodStatus: asNullableText(component.methodStatus),
          baselineAmount: asFigure(component.baselineAmount),
          stressedAmount: asFigure(component.stressedAmount),
          allowedMethods: asTextList(component.allowedMethods),
        })),
        updatedAt: asMoment(row.updatedAt),
      };
    }),
    summary: {
      categoryCount: asTally(summary.categoryCount),
      assessedRiskCount: asTally(summary.assessedRiskCount),
      unassessedRiskCount: asTally(summary.unassessedRiskCount),
      materialRiskCount: asTally(summary.materialRiskCount),
    },
    /**
     * The register's provenance is the MATRIX's provenance — the wire carries
     * the parameter uses there, not at the top level. Reading it off the top
     * level is what threw during the walkthrough.
     */
    parameters: matrix.parameters,
  };
}

export type IcaapRiskRegister = ReturnType<typeof normalizeRiskRegister>;
export type IcaapRisk = IcaapRiskRegister["risks"][number];

// ---------------------------------------------------------------------------
// Appetite
// ---------------------------------------------------------------------------

export type AppetiteDirection = "higher_is_safer" | "lower_is_safer";

/**
 * The wire says `floor` / `ceiling`; the screens and the ordering mirror speak
 * in which side is safer. A floor is a level you must stay ABOVE.
 */
function direction(value: unknown): AppetiteDirection {
  return value === "ceiling" ? "lower_is_safer" : "higher_is_safer";
}

const UNITS = ["percent", "ratio", "amount", "count", "multiplier", "years"] as const;
const RAGS = ["green", "amber", "red"] as const;
const TRENDS = ["improving", "deteriorating", "stable"] as const;

export function normalizeAppetite(value: WireAppetite | unknown) {
  const body = asRecord(value);
  const summary = asRecord(body.summary);
  return {
    cycleId: asText(body.cycleId),
    catalogue: mapList(body.catalogue, (row) => ({
      metricKey: asText(row.key),
      title: asText(row.label, asText(row.key)),
      unit: oneOf(row.unit, UNITS, "percent"),
      direction: direction(row.direction),
      riskKey: asNullableText(row.defaultRiskKey),
      regulatoryParamCode: asNullableText(row.regulatoryParamCode),
    })),
    metrics: mapList(body.metrics, (row) => {
      const hasEvaluation =
        typeof row.evaluation === "object" && row.evaluation !== null;
      const evaluation = asRecord(row.evaluation);
      const hasReference =
        typeof row.regulatoryReference === "object" && row.regulatoryReference !== null;
      const reference = asRecord(row.regulatoryReference);
      return {
        metricId: asText(row.id),
        metricKey: asText(row.metricKey),
        title: asText(row.label, asText(row.metricKey)),
        riskKey: asNullableText(row.riskKey),
        unit: oneOf(row.unit, UNITS, "percent"),
        direction: direction(row.direction),
        measureKind: row.measureKind === "qualitative" ? "qualitative" : "quantitative",
        appetite: asFigure(row.appetiteValue),
        tolerance: asFigure(row.toleranceValue),
        capacity: asFigure(row.capacityValue),
        qualitativeStatement: asNullableText(row.qualitativeStatement),
        boardRegisterValue: asFigure(row.boardRegisterValue),
        boardRegisterStricter: asFlag(row.boardRegisterStricter),
        rowRev: asTally(row.rowRev),
        evaluation: hasEvaluation
          ? {
              status: asText(evaluation.status),
              // An unknown rag is "none" — NOT green. A fabricated green chip
              // reads as a compliance affirmation of something unmeasured.
              rag: oneOf(evaluation.rag, RAGS, "none" as const),
              currentValue: asFigure(evaluation.currentValue),
              utilisationPct: asFigure(evaluation.utilisationPct),
              headroomToAppetite: asFigure(evaluation.headroomToAppetite),
              headroomToTolerance: asFigure(evaluation.headroomToTolerance),
              headroomToCapacity: asFigure(evaluation.headroomToCapacity),
              headroomToRegulatory: asFigure(evaluation.headroomToRegulatory),
              trend: oneOf(evaluation.trend, TRENDS, "unknown" as const),
            }
          : null,
        regulatoryReference: hasReference
          ? {
              paramCode: asText(reference.paramCode),
              value: asFigure(reference.value),
              direction: direction(reference.direction),
              confirmationStatus:
                reference.confirmationStatus === "confirmed" ? "confirmed" : "pending",
              representative: asFlag(reference.representative),
              sourceCitation: asNullableText(reference.sourceCitation),
              effectiveFrom: asMoment(reference.effectiveFrom),
            }
          : null,
        /**
         * D-036. Absent reference OR the evaluation saying so: with no governed
         * value, the capacity check reads "not assessed against a regulatory
         * floor". The conservative reading of both signals.
         */
        referenceMissing:
          asFlag(row.referenceMissing) ||
          asFlag(evaluation.regulatoryReferenceAbsent) ||
          !hasReference,
        violations: asTextList(row.violations),
        updatedAt: asMoment(row.updatedAt),
      };
    }),
    summary: {
      metricCount: asTally(summary.metricCount),
      amberCount: asTally(summary.amberCount),
      breachCount: asTally(summary.breachCount),
      qualitativeCount: asTally(summary.qualitativeCount),
    },
    parameters: normalizeParameters(body.parameters),
  };
}

export type IcaapAppetite = ReturnType<typeof normalizeAppetite>;
export type IcaapAppetiteMetric = IcaapAppetite["metrics"][number];
export type IcaapAppetiteMetricDef = IcaapAppetite["catalogue"][number];

// ---------------------------------------------------------------------------
// Pillar 2
// ---------------------------------------------------------------------------

const P2_STATUSES = [
  "not_computed",
  "computed",
  "interim_non_sf",
  "incomplete",
  "not_computable",
  "not_capitalised",
] as const;
const P2_BASES = [
  "pct_total_rwa",
  "pct_credit_rwa",
  "pct_pillar1_credit_capital",
  "absolute",
] as const;
const P2_SOURCES = [
  "icaap_method",
  "capital_plan",
  "stress_overlay",
  "supervisory",
  "judgemental",
] as const;

function normalizePillar2Item(row: Record<string, unknown>) {
  return {
    itemId: asText(row.id),
    itemKey: asText(row.itemKey),
    componentKey: asText(row.componentKey),
    categoryKey: asText(row.categoryKey),
    riskKey: asText(row.riskKey),
    title: asText(row.componentKey, asText(row.itemKey)),
    method: asText(row.method),
    methodLabel: asText(row.methodLabel, asText(row.method)),
    methodStatus: oneOf(row.methodStatus, P2_STATUSES, "not_computed"),
    source: oneOf(row.source, P2_SOURCES, "icaap_method"),
    inputMode:
      row.inputMode === "manual_with_evidence" ? "manual_with_evidence" : "bound_blocks",
    basis: oneOf(row.basis, P2_BASES, "absolute"),
    basisValue: asFigure(row.basisValue),
    baselineAmount: asFigure(row.baselineAmount),
    stressedAmount: asFigure(row.stressedAmount),
    currency: asText(row.currency),
    revisionNo: asTally(row.currentRevisionNo),
    approvedRevisionNo: asCount(row.approvedRevisionNo),
    inputsDigest: asNullableText(row.inputsDigest),
    scenarioDefinition: asNullableText(row.scenarioDefinition),
    rationale: asNullableText(row.rationale),
    evidenceAttachmentIds: asTextList(row.evidenceAttachmentIds),
    approvalCurrent: asFlag(row.approvalCurrent),
    approvalNote: asNullableText(row.approvalNote),
    approvedBy: asNullableText(row.approvedBy),
    approvedAt: asMoment(row.approvedAt),
    stale: asFlag(row.stale),
    staleReasons: asTextList(row.staleReasons),
    /**
     * The engine's own working. The wire types it as an opaque object, so it is
     * flattened here into printable label/value rows — the drawer shows what the
     * engine recorded without this file pretending to know what any key means.
     */
    computation: flattenDetail(row.computation),
    parameters: normalizeParameters(row.parameters),
    pendingParameters: asTextList(row.pendingParameters),
    representativeParameters: asTextList(row.representativeParameters),
    /** Why the amount could not be produced; the server's own sentence. */
    statusDetail: asNullableText(row.statusDetail),
    zeroAmountJustification: asNullableText(row.zeroAmountJustification),
    // Actions default to UNAVAILABLE: a control the server did not allow must
    // not be offered, even though the server re-decides anyway.
    editable: asFlag(row.editable),
    approvable: asFlag(row.approvable),
    updatedAt: asMoment(row.updatedAt),
  };
}

function normalizeFindings(value: unknown) {
  return mapList(value, (row) => ({
    code: asText(row.code),
    ref: asText(row.ref),
    params: Object.fromEntries(
      Object.entries(asRecord(row.params)).map(([key, entry]) => [key, asText(entry)]),
    ),
  }));
}

export function normalizePillar2Register(value: WirePillar2Register | unknown) {
  const body = asRecord(value);
  return {
    cycleId: asText(body.cycleId),
    currency: asText(body.currency),
    items: mapList(body.items, normalizePillar2Item),
    components: mapList(body.components, (row) => ({
      componentKey: asText(row.componentKey),
      categoryKey: asText(row.categoryKey),
      riskKey: asText(row.riskKey),
      itemId: asNullableText(row.itemId),
      allowedMethods: asTextList(row.allowedMethods),
      deferredMethods: asTextList(row.deferredMethods),
    })),
    consistency: mapList(body.consistency, normalizeConsistency),
    findings: normalizeFindings(body.findings),
    /** Absent means not allowed: a benefit must be granted, never assumed. */
    diversificationAllowed: asFlag(body.diversificationAllowed),
    parameters: normalizeParameters(body.parameters),
  };
}

export type IcaapPillar2Register = ReturnType<typeof normalizePillar2Register>;
export type IcaapPillar2Item = IcaapPillar2Register["items"][number];

export function normalizeTable5(value: WireTable5 | unknown) {
  const body = asRecord(value);
  return {
    cycleId: asText(body.cycleId),
    currency: asText(body.currency),
    unit: asText(body.unit),
    /** False when the grid cannot be produced at all; the reason comes with it. */
    available: asFlag(body.available),
    unavailableReason: asNullableText(body.unavailableReason),
    columns: mapList(body.columns, (row) => ({
      key: asText(row.key),
      label: asText(row.label, asText(row.key)),
      basis: asText(row.basis),
    })),
    rows: mapList(body.rows, (row) => ({
      key: asText(row.key),
      group: asText(row.group),
      label: asText(row.label, asText(row.key)),
      partial: asFlag(row.partial),
      // The wire sends cells as a LIST of {column, value}; the grid reads them
      // by column key, so they are indexed here rather than in the component.
      cells: Object.fromEntries(
        mapList(row.cells, (cell) => [asText(cell.column), asFigure(cell.value)] as const),
      ) as Record<string, string | null>,
    })),
    registerTotalBaseline: asFigure(body.registerTotalBaseline),
    registerTotalStressed: asFigure(body.registerTotalStressed),
    notes: asTextList(body.notes),
    partialRows: asTextList(body.partialRows),
    findings: normalizeFindings(body.findings),
  };
}

export type IcaapTable5 = ReturnType<typeof normalizeTable5>;

// ---------------------------------------------------------------------------
// Reconciliation
// ---------------------------------------------------------------------------

const TIERS = ["cet1", "at1", "tier2", "deduction", "other"] as const;

export function normalizeReconciliation(value: WireReconciliation | unknown) {
  const body = asRecord(value);
  const requirement = asRecord(body.requirement);
  const requirementTotals = asRecord(requirement.totals);
  const resources = asRecord(body.resources);
  const resourcesTotals = asRecord(resources.totals);
  return {
    cycleId: asText(body.cycleId),
    currency: asText(body.currency),
    requirement: {
      lines: mapList(requirement.lines, (row) => ({
        lineKey: asText(row.lineKey),
        lineGroup: asText(row.lineGroup),
        label: asText(row.label, asText(row.lineKey)),
        position: asTally(row.position),
        regulatoryAmount: asFigure(row.regulatoryAmount),
        internalAmount: asFigure(row.internalAmount),
        supervisoryAmount: asFigure(row.supervisoryAmount),
        difference: asFigure(row.difference),
        explanation: asNullableText(row.explanation),
        explanationRequired: asFlag(row.explanationRequired),
        explanationCurrent: row.explanationCurrent !== false,
      })),
      totals: {
        totalRegulatoryRequirement: asFigure(requirementTotals.totalRegulatoryRequirement),
        totalInternalRequirement: asFigure(requirementTotals.totalInternalRequirement),
        difference: asFigure(requirementTotals.difference),
        explanationRequired: asFlag(requirementTotals.explanationRequired),
      },
      computedAt: asMoment(requirement.computedAt),
      computedBy: asNullableText(requirement.computedBy),
      stale: asFlag(requirement.stale),
    },
    resources: {
      lines: mapList(resources.lines, (row) => ({
        lineId: asText(row.id),
        lineKey: asText(row.lineKey),
        label: asText(row.label, asText(row.lineKey)),
        tier: oneOf(row.tier, TIERS, "other"),
        position: asTally(row.position),
        regulatoryAmount: asFigure(row.regulatoryAmount),
        internalAmount: asFigure(row.internalAmount),
        recognisedAmount: asFigure(row.recognisedAmount),
        aboveCapAmount: asFigure(row.aboveCapAmount),
        /** Absent means NOT eligible: recognition must be stated, not assumed. */
        regulatoryEligible: asFlag(row.regulatoryEligible),
        explanation: asNullableText(row.explanation),
        explanationRequired: asFlag(row.explanationRequired),
        origin:
          row.origin === "regulatory_component" ? "regulatory_component" : "manual",
        rowRev: asTally(row.rowRev),
      })),
      totals: {
        regulatoryTotalCapital: asFigure(resourcesTotals.regulatoryTotalCapital),
        recognisedRegulatoryCapital: asFigure(
          resourcesTotals.recognisedRegulatoryCapital,
        ),
        availableInternalCapital: asFigure(resourcesTotals.availableInternalCapital),
        internalCapitalSurplus: asFigure(resourcesTotals.internalCapitalSurplus),
        /** Computed by the SERVER. The screen never divides two numbers itself. */
        internalCapitalCoveragePct: asFigure(resourcesTotals.internalCapitalCoveragePct),
        matchesRegulatoryTotal:
          typeof resourcesTotals.matchesRegulatoryTotal === "boolean"
            ? resourcesTotals.matchesRegulatoryTotal
            : null,
      },
      caps: normalizeParameters(resources.caps),
    },
    controls: mapList(body.controls, normalizeConsistency),
    controlExplanations: mapList(body.controlExplanations, (row) => ({
      controlCode: asText(row.controlCode),
      comparisonKey: asText(row.comparisonKey),
      explanation: asText(row.explanation),
      explainedBy: asText(row.explainedBy),
      explainedAt: asMoment(row.explainedAt),
      current: asFlag(row.current),
    })),
    parameters: normalizeParameters(body.parameters),
  };
}

export type IcaapReconciliation = ReturnType<typeof normalizeReconciliation>;
export type IcaapRequirementLine = IcaapReconciliation["requirement"]["lines"][number];
export type IcaapResourcesLine = IcaapReconciliation["resources"]["lines"][number];

// ---------------------------------------------------------------------------
// Review and challenge
// ---------------------------------------------------------------------------

const REVIEW_STATUSES = ["draft", "finalised", "superseded"] as const;
const OPINIONS = [
  "satisfactory",
  "satisfactory_with_findings",
  "needs_improvement",
  "unsatisfactory",
] as const;
const SEVERITIES = ["low", "medium", "high", "critical"] as const;

export function normalizeAuditReviews(value: WireAuditReviews | unknown) {
  const body = asRecord(value);
  return {
    cycleId: asText(body.cycleId),
    latestReviewDate: asMoment(body.latestReviewDate),
    latestReviewOpinion: asNullableText(body.latestReviewOpinion),
    openFindingsCount: asTally(body.openFindingsCount),
    reviews: mapList(body.reviews, (row) => ({
      reviewId: asText(row.id),
      status: oneOf(row.status, REVIEW_STATUSES, "draft"),
      reviewKind: asText(row.reviewKind),
      reviewerFunction: asText(row.reviewerFunction),
      recordedBy: asText(row.recordedBy),
      performedOn: asMoment(row.performedOn),
      performedFrom: asMoment(row.performedFrom),
      periodCovered: asNullableText(row.periodCovered),
      scope: asNullableText(row.scope),
      independenceStatement: asNullableText(row.independenceStatement),
      frequencyStatement: asNullableText(row.frequencyStatement),
      overallOpinion: oneOf(row.overallOpinion, OPINIONS, "needs_improvement"),
      openFindingsCount: asTally(row.openFindingsCount),
      findings: mapList(row.findings, (finding) => ({
        ref: asText(finding.ref),
        finding: asText(finding.finding),
        severity: oneOf(finding.severity, SEVERITIES, "medium"),
        status: asNullableText(finding.status),
        managementResponse: asNullableText(finding.managementResponse),
        targetDate: asMoment(finding.targetDate),
      })),
      reportAttachmentId: asNullableText(row.reportAttachmentId),
      rowRev: asTally(row.rowRev),
      finalisedAt: asMoment(row.finalisedAt),
      supersededAt: asMoment(row.supersededAt),
    })),
  };
}

export type IcaapAuditReviews = ReturnType<typeof normalizeAuditReviews>;
export type IcaapAuditReview = IcaapAuditReviews["reviews"][number];

const FORUMS = [
  "board",
  "board_risk_committee",
  "board_audit_committee",
  "senior_management",
  "chief_risk_officer",
  "internal_audit",
  "other",
] as const;

/** The forums the wire recognises. Not a local invention: it is the enum. */
export const ICAAP_CHALLENGE_FORUMS = FORUMS;

export function normalizeChallenges(value: WireChallenges | unknown) {
  const body = asRecord(value);
  return {
    cycleId: asText(body.cycleId),
    challengeCount: asTally(body.challengeCount),
    openChallengeCount: asTally(body.openChallengeCount),
    boardChallengeCount: asTally(body.boardChallengeCount),
    challenges: mapList(body.challenges, (row) => {
      const responses = mapList(row.responses, (response) => ({
        responseId: asText(response.id),
        responseNo: asTally(response.responseNo),
        respondedBy: asText(response.respondedBy),
        responderFunction: asText(response.responderFunction),
        outcome: asText(response.outcome),
        responseText: asText(response.responseText),
        createdAt: asMoment(response.createdAt),
      }));
      return {
        challengeId: asText(row.id),
        challengeNo: asTally(row.challengeNo),
        round: asTally(row.round),
        forum: oneOf(row.raisedIn, FORUMS, "other"),
        raisedOn: asMoment(row.raisedOn),
        raisedByName: asText(row.raisedByName),
        recordedBy: asText(row.recordedBy),
        severity: oneOf(row.severity, SEVERITIES, "medium"),
        challengeText: asText(row.challengeText),
        targetKind: asText(row.targetKind),
        targetRef: asNullableText(row.targetRef),
        meetingReference: asNullableText(row.meetingReference),
        responses,
        // "Open" is derived only when the server did not say. An unanswered
        // challenge showing as answered would hide a governance gap.
        open: typeof row.open === "boolean" ? row.open : responses.length === 0,
      };
    }),
  };
}

export type IcaapChallenges = ReturnType<typeof normalizeChallenges>;
export type IcaapChallenge = IcaapChallenges["challenges"][number];

// ---------------------------------------------------------------------------
// Capital allocation
// ---------------------------------------------------------------------------

const UNIT_KINDS = ["business_line", "legal_entity", "risk_type"] as const;
const DRIVER_KINDS = ["rwa_share", "exposure_share", "manual_pct"] as const;

export type IcaapAllocationUnitKind = (typeof UNIT_KINDS)[number];
export type IcaapAllocationDriverKind = (typeof DRIVER_KINDS)[number];

export function normalizeAllocation(value: WireAllocation | unknown) {
  const body = asRecord(value);
  return {
    cycleId: asText(body.cycleId),
    currency: asText(body.currency),
    /**
     * Absent means the grid cannot be shown at all. Defaulting to "available"
     * would put an empty driver grid in front of a preparer and invite them to
     * allocate a requirement that has not been computed.
     */
    available: asFlag(body.available),
    unavailableReason: asNullableText(body.unavailableReason),
    /** The optimistic token a save sends back. Absent = nothing saved yet. */
    digest: asNullableText(body.digest),
    totalAllocated: asFigure(body.totalAllocated),
    units: mapList(body.units, (row) => ({
      unitKey: asText(row.unitKey),
      unitLabel: asText(row.unitLabel, asText(row.unitKey)),
      unitKind: oneOf(row.unitKind, UNIT_KINDS, "business_line"),
      totalAllocated: asFigure(row.totalAllocated),
    })),
    lines: mapList(body.lines, (row) => ({
      lineKey: asText(row.lineKey),
      lineGroup: asText(row.lineGroup),
      label: asText(row.label, asText(row.lineKey)),
      position: asTally(row.position),
      table5Row: asNullableText(row.table5Row),
      internalAmount: asFigure(row.internalAmount),
      regulatoryAmount: asFigure(row.regulatoryAmount),
      difference: asFigure(row.difference),
      explanationRequired: asFlag(row.explanationRequired),
    })),
    cells: mapList(body.cells, (row) => ({
      unitKey: asText(row.unitKey),
      riskLineKey: asText(row.riskLineKey),
      /**
       * An unrecognised driver reads as the bank's own percentage — the option
       * that claims the LEAST: that somebody stated the share, not that the
       * platform derived it from risk-weighted assets or exposure.
       */
      driverKind: oneOf(row.driverKind, DRIVER_KINDS, "manual_pct"),
      driverValue: asFigure(row.driverValue),
      allocatedAmount: asFigure(row.allocatedAmount),
    })),
  };
}

export type IcaapAllocation = ReturnType<typeof normalizeAllocation>;
export type IcaapAllocationUnit = IcaapAllocation["units"][number];
export type IcaapAllocationLine = IcaapAllocation["lines"][number];
export type IcaapAllocationCell = IcaapAllocation["cells"][number];

// ---------------------------------------------------------------------------
// Capital-plan triggers
// ---------------------------------------------------------------------------

const TRIGGER_STATUSES = [
  "clear",
  "early_warning",
  "action",
  "regulatory_breach",
  "not_evaluable",
] as const;

export type IcaapTriggerStatus = (typeof TRIGGER_STATUSES)[number];

export function normalizeCapitalTriggers(value: WireTriggers | unknown) {
  const body = asRecord(value);
  const unavailable =
    typeof body.unavailable === "object" && body.unavailable !== null
      ? asRecord(body.unavailable)
      : null;
  return {
    cycleId: asText(body.cycleId),
    planVersion: asCount(body.planVersion),
    triggerCount: asTally(body.triggerCount),
    triggersBreachedNow: asTally(body.triggersBreachedNow),
    firstActionYear: asCount(body.firstActionYear),
    /** The evaluation ran. When it did not, the server says why, in words. */
    available: unavailable === null,
    unavailableReason: unavailable === null ? null : asNullableText(unavailable.message),
    results: mapList(body.results, (row) => ({
      metricCode: asText(row.metricCode),
      metricKey: asNullableText(row.metricKey),
      direction: direction(row.direction),
      earlyWarningLevel: asFigure(row.earlyWarningLevel),
      actionLevel: asFigure(row.actionLevel),
      currentValue: asFigure(row.currentValue),
      /**
       * An unrecognised status is "cannot be evaluated", NEVER "clear". A
       * trigger shown as clear is a statement that the bank is above the level
       * it promised the Board it would act at.
       */
      currentStatus: oneOf(row.currentStatus, TRIGGER_STATUSES, "not_evaluable"),
      points: mapList(row.points, (point) => ({
        scenarioCode: asText(point.scenarioCode),
        year: asTally(point.year),
        periodEnd: asMoment(point.periodEnd),
        value: asFigure(point.value),
        status: oneOf(point.status, TRIGGER_STATUSES, "not_evaluable"),
        floorPct: asFigure(point.floorPct),
      })),
      /** scenario → level → the first year it is crossed. */
      firstCrossing: Object.fromEntries(
        Object.entries(asRecord(row.firstCrossing)).map(([scenario, levels]) => [
          scenario,
          Object.fromEntries(
            Object.entries(asRecord(levels))
              .map(([level, year]) => [level, asCount(year)] as const)
              .filter((entry): entry is readonly [string, number] => entry[1] !== null),
          ) as Record<string, number>,
        ]),
      ) as Record<string, Record<string, number>>,
      findings: asTextList(row.findings),
    })),
    findings: asTextList(body.findings),
    /** The governed floors each year was measured against (D-024). */
    floors: normalizeParameters(body.floors),
  };
}

export type IcaapCapitalTriggers = ReturnType<typeof normalizeCapitalTriggers>;
export type IcaapTriggerResult = IcaapCapitalTriggers["results"][number];
export type IcaapTriggerPoint = IcaapTriggerResult["points"][number];

// ---------------------------------------------------------------------------
// Pillar 2 item revisions
// ---------------------------------------------------------------------------

const REVISION_KINDS = ["created", "edited", "computed", "retired"] as const;

export type IcaapRevisionKind = (typeof REVISION_KINDS)[number] | "recorded";

export function normalizePillar2Revisions(value: WirePillar2Revisions | unknown) {
  const body = asRecord(value);
  return {
    itemId: asText(body.itemId),
    revisions: mapList(body.revisions, (row) => ({
      revisionId: asText(row.id),
      revisionNo: asTally(row.revisionNo),
      /** An unmapped change reads as "recorded": something happened, unnamed. */
      changeKind: oneOf<IcaapRevisionKind>(
        row.changeKind,
        [...REVISION_KINDS, "recorded"],
        "recorded",
      ),
      round: asTally(row.round),
      note: asNullableText(row.note),
      inputsDigest: asNullableText(row.inputsDigest),
      snapshotSha256: asNullableText(row.snapshotSha256),
      createdBy: asNullableText(row.createdBy),
      createdAt: asMoment(row.createdAt),
      /** The engine's working, as it recorded it. Null when none was kept. */
      computation: flattenDetail(row.computation),
      /** What the row looked like at this revision. Null when not recorded. */
      snapshot: flattenDetail(row.snapshot),
    })),
  };
}

export type IcaapPillar2Revisions = ReturnType<typeof normalizePillar2Revisions>;
export type IcaapPillar2Revision = IcaapPillar2Revisions["revisions"][number];

// ---------------------------------------------------------------------------
// Governed parameter register
// ---------------------------------------------------------------------------

export function normalizeParameterRegister(value: WireParameterUses | unknown) {
  const body = asRecord(value);
  return {
    asOf: asMoment(body.asOf),
    parameters: normalizeParameters(body.parameters),
    /** Codes the control plane governs no value for at this date (D-024 §4). */
    missing: asTextList(body.missing),
  };
}

export type IcaapParameterRegister = ReturnType<typeof normalizeParameterRegister>;

// ---------------------------------------------------------------------------
// Supervisory add-ons
// ---------------------------------------------------------------------------

const ADDON_STATUSES = ["draft", "active", "superseded", "withdrawn"] as const;

export type IcaapAddonStatus = (typeof ADDON_STATUSES)[number];

export function normalizeSupervisoryAddons(value: WireSupervisoryAddons | unknown) {
  const body = asRecord(value);
  return {
    bankId: asText(body.bankId),
    asOf: asMoment(body.asOf),
    currency: asText(body.currency),
    /**
     * An add-on imposed by the supervisor is never published (¶82 / D-023), and
     * absence of the flag must not be read as permission to publish it.
     */
    neverPublic: body.neverPublic !== false,
    totalAmountAtAsOf: asFigure(body.totalAmountAtAsOf),
    addons: mapList(body.addons, (row) => ({
      addonId: asText(row.id),
      /**
       * An unrecognised status reads as a DRAFT: a draft is not in force, so
       * the conservative reading never shows an unknown row as binding capital.
       */
      status: oneOf(row.status, ADDON_STATUSES, "draft"),
      letterReference: asText(row.letterReference),
      letterDate: asMoment(row.letterDate),
      effectiveFrom: asMoment(row.effectiveFrom),
      effectiveTo: asMoment(row.effectiveTo),
      appliesToBasis: asText(row.appliesToBasis),
      table5Row: asNullableText(row.table5Row),
      componentKey: asNullableText(row.componentKey),
      basis: oneOf(row.basis, P2_BASES, "absolute"),
      basisValue: asFigure(row.basisValue),
      currency: asText(row.currency),
      description: asNullableText(row.description),
      letterOriginalFilename: asText(row.letterOriginalFilename),
      letterMediaType: asNullableText(row.letterMediaType),
      letterByteSize: asCount(row.letterByteSize),
      letterSha256: asNullableText(row.letterSha256),
      supersedesAddonId: asNullableText(row.supersedesAddonId),
      supersededByAddonId: asNullableText(row.supersededByAddonId),
      createdBy: asNullableText(row.createdBy),
      createdAt: asMoment(row.createdAt),
      confirmedBy: asNullableText(row.confirmedBy),
      confirmedAt: asMoment(row.confirmedAt),
      withdrawnAt: asMoment(row.withdrawnAt),
      withdrawalReason: asNullableText(row.withdrawalReason),
      /** Converted at the requested date, or absent when the basis cannot be. */
      amountAtAsOf: asFigure(row.amountAtAsOf),
    })),
  };
}

export type IcaapSupervisoryAddons = ReturnType<typeof normalizeSupervisoryAddons>;
export type IcaapSupervisoryAddon = IcaapSupervisoryAddons["addons"][number];

// ---------------------------------------------------------------------------
// Stress and capital-plan evidence (the blocks the cycle is bound to)
// ---------------------------------------------------------------------------

/**
 * The block types the stress and capital-plan view reads, in reading order.
 *
 * The tab is READ-ONLY over the cycle's own bound evidence. It deliberately
 * shows what the ICAAP rests on rather than the latest run in the platform:
 * an ICAAP bound to an earlier attested run must not be illustrated with
 * figures it does not contain.
 */
export const ICAAP_STRESS_BLOCK_TYPES = [
  "appendix_ii",
  "stress_narratives",
  "management_actions",
  "reverse_stress",
  "capital_plan",
] as const;

const BLOCK_STATUSES = [
  "unbound",
  "fresh",
  "stale",
  "as_of_mismatch",
  "source_withdrawn",
  "source_missing",
  "pinned",
] as const;

/**
 * Is this payload the enterprise-stress run's own Appendix II, complete enough
 * for the stress module's table component to render it?
 *
 * The check is structural, not a rewrite: `AppendixIITables` spreads
 * `table1_summary.pre_adverse` and indexes `impact_of_adverse`, so a payload
 * missing them would throw. A payload that does not pass reads as "the detail
 * is not on this binding" — never as a partially drawn regulatory table.
 */
function appendixIsRenderable(value: unknown): boolean {
  const appendix = asRecord(value);
  const summary = asRecord(appendix.table1_summary);
  return (
    Array.isArray(summary.pre_adverse) &&
    Array.isArray(summary.post_adverse) &&
    Array.isArray(summary.impact_of_adverse) &&
    typeof summary.current === "object" &&
    summary.current !== null &&
    Array.isArray(appendix.table2_capital) &&
    Array.isArray(appendix.table3_profit_and_loss) &&
    Array.isArray(appendix.table4_financial_position) &&
    Array.isArray(asRecord(appendix.table5_rwa).rows) &&
    Array.isArray(asRecord(appendix.table6_risk_drivers).rows)
  );
}

export function normalizeStressEvidence(value: WireDataBlocks | unknown) {
  const body = asRecord(value);
  const order = new Map(
    ICAAP_STRESS_BLOCK_TYPES.map((type, index) => [type as string, index] as const),
  );
  const blocks = mapList(body.blocks, (row) => {
    const binding = asRecord(row.currentBinding);
    const payload = asRecord(binding.payload);
    const raw = asRecord(payload.raw);
    const sourceRef = asRecord(binding.sourceRef);
    const spec = asRecord(row.spec);
    const appendix = raw.raw_appendix_ii;
    return {
      blockId: asText(row.id),
      blockType: asText(row.blockType),
      title: asText(row.title, asText(spec.title, asText(row.blockType))),
      status: oneOf(row.status, BLOCK_STATUSES, "unbound"),
      statusDetail: asNullableText(row.statusDetail),
      pinReason: asNullableText(row.pinReason),
      /** True only when a binding exists; absence is "not linked yet". */
      bound: typeof row.currentBinding === "object" && row.currentBinding !== null,
      sourceLabel: asNullableText(payload.source_label),
      sourceAsOf: asMoment(binding.sourceAsOf),
      sourceKind: asNullableText(binding.sourceKind),
      boundAt: asMoment(binding.createdAt),
      /** The attested run this binding rests on, when it rests on one. */
      runId: asNullableText(sourceRef.run_id),
      signoffId: asNullableText(sourceRef.signoff_id),
      facts: Object.entries(asRecord(binding.facts))
        .map(([key, fact]) => {
          const entry = asRecord(fact);
          return {
            key,
            label: asText(entry.label, key),
            value: asNullableText(entry.value),
            kind: asText(entry.kind),
            currency: asNullableText(entry.currency),
          };
        })
        .sort((left, right) => left.key.localeCompare(right.key)),
      /**
       * The run's own Appendix II, passed through untouched when it is complete
       * — the stress module owns that shape and reshaping it here would create
       * a second opinion about what the regulator's tables say.
       */
      appendix: appendixIsRenderable(appendix) ? asRecord(appendix) : null,
    };
  })
    .filter((block) => order.has(block.blockType))
    .sort(
      (left, right) =>
        (order.get(left.blockType) ?? 0) - (order.get(right.blockType) ?? 0),
    );
  return {
    blocks,
    /** The attested run every stress figure on this tab comes from, if any. */
    runId:
      blocks.find((block) => block.blockType === "appendix_ii")?.runId ?? null,
    appendix:
      blocks.find((block) => block.blockType === "appendix_ii")?.appendix ?? null,
  };
}

export type IcaapStressEvidence = ReturnType<typeof normalizeStressEvidence>;
export type IcaapStressEvidenceBlock = IcaapStressEvidence["blocks"][number];
