/**
 * The adapter between the GENERATED IRRBB Standardised Framework contract and
 * what the framework screen can render.
 *
 * Same job, same rules and the same reasons as `icaapFilingNormalize.ts` and
 * `icaapRiskCapitalNormalize.ts` — read either header first. This file is
 * their counterpart for the framework: the run and its mandate, the six
 * prescribed scenarios, the three measure sets, the outlier verdict, the
 * disclosure grid, the currency scope, the non-maturity deposit categories,
 * the repricing ladders, the governed parameters and the data-quality tallies.
 *
 * Everything here is TOTAL: it accepts `unknown`, so a body that omits a
 * field, sends `null` where a list was declared, or is not the expected body
 * at all cannot reach the rendering code. The view types are INFERRED from
 * these functions, so a field cannot be added to a screen without a rule for
 * filling it.
 *
 * THE FAIL-CLOSED RULES, and what each one is protecting a reader from:
 *
 *   - **EVERY MONEY AND RATIO FIGURE IS `string | null`, NEVER A NUMBER AND
 *     NEVER ZERO.** A missing economic-value loss rendered as 0 is the single
 *     most dangerous thing this screen could do: zero is a real, and
 *     excellent, answer. The screen prints the absence instead;
 *   - **an unreadable mandate says neither "required" nor "not required".**
 *     `mandatory` is true only when the server said true, and `stated` is
 *     false when no sentence arrived — the screen then says the commencement
 *     rule could not be read, rather than reassuring a preparer that the
 *     interim method is still fine;
 *   - **a mandate is SETTLED only when the server says `confirmed`.** The
 *     commencement date is seeded pending (D-039); a screen that drops the
 *     qualifier presents a proposal as a rule;
 *   - **a parameter is REPRESENTATIVE or PENDING unless the payload says it is
 *     neither, and its own `statement` is carried verbatim.** The API, the PDF
 *     and the screen must not be able to disagree about whether a number is a
 *     published supervisory value;
 *   - **an assumption's `count` is `number | null`, never 0.** A representative
 *     assumption applied forty times is a different exposure from one applied
 *     once, so the tally travels with the label and an unreported tally says
 *     so (D-060 item 5);
 *   - **`outlier` is true only when the server says true**, and the verdict is
 *     rendered only when the measure AND the threshold are both present —
 *     "below the threshold" computed against a missing threshold is a pass
 *     nobody granted;
 *   - **a refused attempt keeps its ONE name.** `irrbb_sf_options_unsupported`
 *     (D-061) is carried unmapped; the sentence is chosen from it, never a
 *     zero and never a blank.
 *
 * D-024 — no regulatory number is declared here. Every threshold, shock, cap
 * and tally arrives on a payload.
 *
 * Kept free of React and of every runtime import (the type imports are elided
 * at emit) so it runs under node in `pnpm --filter @aequoros/dashboard test`.
 */

import type {
  IrrbbSfRead as WireSf,
  RegulatoryRunListRead as WireRunList,
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
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** A date or timestamp, as text. The generated client materialises `Date`. */
function asMoment(value: unknown): string | null {
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value.toISOString() : null;
  }
  return asNullableText(value);
}

/**
 * A DECIMAL FIGURE, kept as text and never invented.
 *
 * The wire sends every amount and ratio as a string so no precision is lost on
 * the way to the browser. Parsing here would throw that away and — worse —
 * would need a fallback, and the only honest fallback for a missing regulatory
 * figure is "absent". So: text or null, and the screen decides how to print
 * an absence.
 */
function asFigure(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed === "") return null;
  return Number.isFinite(Number(trimmed)) ? trimmed : null;
}

/** A whole number, or null. Never invented — see the assumption-tally rule. */
function asCount(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** True only when the server said true. Every verdict flag uses this. */
function asFlag(value: unknown): boolean {
  return value === true;
}

/** False only when the server said false. Used where absence must not relax. */
function asFlagDefaultTrue(value: unknown): boolean {
  return value !== false;
}

// ---------------------------------------------------------------------------
// The view types, inferred from the functions below
// ---------------------------------------------------------------------------

export type SfParameter = ReturnType<typeof normalizeParameter>;
export type SfMandate = ReturnType<typeof normalizeMandate>;
export type SfScenario = ReturnType<typeof normalizeScenario>;
export type SfCurrencyScenario = ReturnType<typeof normalizeCurrencyScenario>;
export type SfMeasure = ReturnType<typeof normalizeMeasure>;
export type SfMeasures = ReturnType<typeof normalizeMeasures>;
export type SfCurrencyScope = ReturnType<typeof normalizeCurrencyScope>;
export type SfTable8Row = ReturnType<typeof normalizeTable8Row>;
export type SfNmdCategory = ReturnType<typeof normalizeNmdCategory>;
export type SfBucket = ReturnType<typeof normalizeBucket>;
export type SfLadderRow = ReturnType<typeof normalizeLadderRow>;
export type SfAssumption = ReturnType<typeof normalizeAssumption>;
export type SfExclusion = ReturnType<typeof normalizeExclusion>;
export type SfDataQuality = ReturnType<typeof normalizeDataQuality>;
export type SfRun = ReturnType<typeof normalizeRunSummary>;
export type StandardisedFramework = ReturnType<typeof normalizeStandardisedFramework>;
export type SfAttempts = ReturnType<typeof normalizeAttempts>;

// ---------------------------------------------------------------------------
// Governed parameters and the mandate
// ---------------------------------------------------------------------------

/**
 * One governed input.
 *
 * `representative` and `pendingConfirmation` default TRUE: a payload that is
 * vague about the provenance of a calibration must not present it as a
 * published supervisory value. `statement` is the engine's own sentence and is
 * carried verbatim so the screen cannot disagree with the API or the PDF.
 */
function normalizeParameter(row: Record<string, unknown>) {
  return {
    code: asText(row.code),
    label: asText(row.label, asText(row.code)),
    unit: asNullableText(row.unit),
    value: asNullableText(row.value),
    confirmationStatus: asNullableText(row.confirmationStatus),
    sourceCitation: asNullableText(row.sourceCitation),
    representative: asFlagDefaultTrue(row.representative),
    pendingConfirmation: asFlagDefaultTrue(row.pendingConfirmation),
    statement: asNullableText(row.statement),
  };
}

/**
 * Whether the framework is required for this reporting date.
 *
 * `stated` is the field the screen branches on. Without it the commencement
 * rule is reported as unreadable — never as "not required", which is the
 * reassurance a preparer would act on by filing under the interim method.
 */
function normalizeMandate(value: unknown) {
  const row = asRecord(value);
  const statement = asNullableText(row.statement);
  const confirmationStatus = asNullableText(row.confirmationStatus);
  return {
    mandatory: asFlag(row.mandatory),
    mandatoryFrom: asMoment(row.mandatoryFrom),
    asOf: asMoment(row.asOf),
    confirmationStatus,
    sourceCitation: asNullableText(row.sourceCitation),
    statement,
    /** The server said something about the rule. */
    stated: statement !== null,
    /** A pending commencement date is a proposal, not a rule (D-039). */
    settled: confirmationStatus === "confirmed",
  };
}

// ---------------------------------------------------------------------------
// Scenarios and measures
// ---------------------------------------------------------------------------

function normalizeCurrencyScenario(row: Record<string, unknown>) {
  return {
    currency: asText(row.currency),
    eveBaseNative: asFigure(row.eveBaseNative),
    eveScenarioNative: asFigure(row.eveScenarioNative),
    deltaEveNative: asFigure(row.deltaEveNative),
    deltaEveReporting: asFigure(row.deltaEveReporting),
    automaticOptionAddonNative: asFigure(row.automaticOptionAddonNative),
    deltaNiiNative: asFigure(row.deltaNiiNative),
    deltaNiiReporting: asFigure(row.deltaNiiReporting),
  };
}

function normalizeScenario(row: Record<string, unknown>) {
  return {
    code: asText(row.code),
    /** Production copy from the server; the raw code is never printed. */
    label: asNullableText(row.label),
    mandatory: asFlag(row.mandatory),
    inOutlierSet: asFlag(row.inOutlierSet),
    byCurrency: mapList(row.byCurrency, normalizeCurrencyScenario),
    loss: asFigure(row.loss),
    net: asFigure(row.net),
    deltaNii: asFigure(row.deltaNii),
  };
}

function normalizeMeasure(value: unknown) {
  const row = asRecord(value);
  return {
    name: asText(row.name),
    label: asNullableText(row.label),
    scenarios: (Array.isArray(row.scenarios) ? row.scenarios : []).filter(
      (entry): entry is string => typeof entry === "string",
    ),
    measure: asFigure(row.measure),
    worstScenario: asNullableText(row.worstScenario),
    worstScenarioLabel: asNullableText(row.worstScenarioLabel),
  };
}

function normalizeMeasures(value: unknown) {
  const row = asRecord(value);
  return {
    allScenarios: normalizeMeasure(row.allScenarios),
    mandatory: normalizeMeasure(row.mandatory),
    outlierSet: normalizeMeasure(row.outlierSet),
  };
}

// ---------------------------------------------------------------------------
// Currency scope, disclosure grid, deposits, ladders
// ---------------------------------------------------------------------------

function normalizeCurrencyScope(row: Record<string, unknown>) {
  return {
    currency: asText(row.currency),
    assetsReporting: asFigure(row.assetsReporting),
    liabilitiesReporting: asFigure(row.liabilitiesReporting),
    assetSharePct: asFigure(row.assetSharePct),
    liabilitySharePct: asFigure(row.liabilitySharePct),
    sharePct: asFigure(row.sharePct),
    material: asFlag(row.material),
    fxToReporting: asFigure(row.fxToReporting),
    curveName: asNullableText(row.curveName),
    curveAsOf: asMoment(row.curveAsOf),
    curveSource: asNullableText(row.curveSource),
  };
}

function normalizeTable8Row(row: Record<string, unknown>) {
  return {
    code: asText(row.code),
    label: asNullableText(row.label),
    deltaEve: asFigure(row.deltaEve),
    deltaEveNet: asFigure(row.deltaEveNet),
    deltaNii: asFigure(row.deltaNii),
    deltaEvePrior: asFigure(row.deltaEvePrior),
    deltaNiiPrior: asFigure(row.deltaNiiPrior),
  };
}

function normalizeNmdCategory(row: Record<string, unknown>) {
  return {
    currency: asText(row.currency),
    category: asText(row.category),
    label: asNullableText(row.label),
    balance: asFigure(row.balance),
    core: asFigure(row.core),
    nonCore: asFigure(row.nonCore),
    coreCapPct: asFigure(row.coreCapPct),
    capBinding: asFlag(row.capBinding),
    averageCoreMaturityYears: asFigure(row.averageCoreMaturityYears),
    longestCoreMaturityYears: asFigure(row.longestCoreMaturityYears),
  };
}

function normalizeBucket(row: Record<string, unknown>) {
  return {
    key: asText(row.key),
    label: asNullableText(row.label),
    midpointYears: asFigure(row.midpointYears),
  };
}

function normalizeLadderRow(row: Record<string, unknown>) {
  return {
    currency: asText(row.currency),
    bucketKey: asText(row.bucketKey),
    bucketLabel: asNullableText(row.bucketLabel),
    principal: asFigure(row.principal),
    interest: asFigure(row.interest),
    net: asFigure(row.net),
  };
}

function normalizeTable7(value: unknown) {
  const row = asRecord(value);
  return {
    averageRepricingMaturityYears: asFigure(row.averageRepricingMaturityYears),
    longestRepricingMaturityYears: asFigure(row.longestRepricingMaturityYears),
  };
}

// ---------------------------------------------------------------------------
// Data quality: the assumption and exclusion tallies
// ---------------------------------------------------------------------------

/**
 * One modelling default, and HOW OFTEN it was applied.
 *
 * `count` is nullable on purpose. Defaulting an unreported tally to 0 would
 * read as "this default was never used", which is the opposite of what an
 * unreadable payload means.
 */
function normalizeAssumption(row: Record<string, unknown>) {
  return {
    marker: asText(row.marker),
    label: asNullableText(row.label),
    count: asCount(row.count),
  };
}

function normalizeExclusion(row: Record<string, unknown>) {
  return {
    marker: asText(row.marker),
    label: asNullableText(row.label),
    count: asCount(row.count),
    amountReporting: asFigure(row.amountReporting),
  };
}

function normalizeDataQuality(value: unknown) {
  const row = asRecord(value);
  return {
    instrumentCount: asCount(row.instrumentCount),
    assumptions: mapList(row.assumptions, normalizeAssumption),
    exclusions: mapList(row.exclusions, normalizeExclusion),
  };
}

// ---------------------------------------------------------------------------
// The run summary and the attempt history
// ---------------------------------------------------------------------------

function normalizeRunSummary(value: unknown) {
  const row = asRecord(value);
  const error = asRecord(row.error);
  return {
    id: asNullableText(row.id),
    status: asNullableText(row.status),
    statusLabel: asNullableText(row.statusLabel),
    createdAt: asMoment(row.createdAt),
    completedAt: asMoment(row.completedAt),
    inputHash: asNullableText(row.inputHash),
    engineVersion: asNullableText(row.engineVersion),
    /** The engine's ONE refusal name, carried unmapped (D-061). */
    errorCode: asNullableText(error.code),
    errorMessage: asNullableText(error.message),
    /**
     * The SERVER's own sentence for a refusal, when the payload carries one.
     *
     * The run registry does not — it carries the raw engine message — so this
     * is null there and the screen composes the sentence from the code, as it
     * always has. The dedicated attempts route DOES carry it, and a caller
     * that prefers it is reading the same words the ICAAP register prints
     * rather than a second copy of them.
     */
    statement: asNullableText(row.refusalStatement ?? row.statement),
  };
}

/**
 * One attempt as the dedicated attempts route states it.
 *
 * Mapped onto the same view shape as a registry row, so the screen has ONE
 * notion of "an attempt" whichever transport answered.
 */
function normalizeAttemptRow(row: Record<string, unknown>) {
  return normalizeRunSummary({
    ...row,
    id: row.runId ?? row.id,
    error: { code: row.errorCode, message: null },
  });
}

/**
 * Every attempt at this reporting date, newest first.
 *
 * The result read answers only "is there a result"; a REFUSED attempt is a
 * `failed` run and never reaches that route. Reading the run list beside it is
 * what lets the screen say "we ran it and it refused" rather than "nobody has
 * run it" — two different answers, and only one of them is actionable.
 */
function normalizeAttempts(value: unknown) {
  const row = asRecord(value);
  // TWO TRANSPORTS, ONE VIEW.
  //
  // `attempts` is the dedicated route
  // (`GET …/irr/standardised-framework/attempts`), which answers the question
  // directly and carries the server's own sentence for a refusal.
  //
  // `runs` is the generic run registry, which the screen read before that
  // route existed. `RegulatoryRunListRead` names its page `runs`, and reading
  // the wrong key fails SILENTLY — an empty list is a valid answer, so the
  // screen says "nobody has run it" when the truth is "we ran it and it
  // refused". Caught in a browser, not by a type check; the test next door
  // pins both field names.
  const dedicated = Array.isArray(row.attempts);
  const runs = dedicated
    ? mapList(row.attempts, normalizeAttemptRow)
    : mapList(row.runs, normalizeRunSummary);
  const refused = runs.find(
    (run) => run.status === "failed" && run.errorCode !== null,
  );
  const served = asRecord(row.latest);
  const latest = dedicated && served.runId !== undefined
    ? normalizeAttemptRow(served)
    : runs.length > 0
      ? runs[0]
      : null;
  const servedRefusal = asRecord(row.refusal);
  return {
    runs,
    latest,
    /** The newest refusal, if the framework was tried and would not measure. */
    refusal:
      dedicated && servedRefusal.runId !== undefined
        ? normalizeAttemptRow(servedRefusal)
        : (refused ?? null),
    // Fail-closed on both sides: a payload that says nothing was attempted but
    // carries attempts is still an attempt, and silence is never a pass.
    attempted: row.attempted === true || runs.length > 0 || latest !== null,
    /** True only when the server says a bindable result exists. */
    hasResult: row.hasResult === true,
    /** What the history means, in the server's words. Null on the registry. */
    statement: asNullableText(row.statement),
  };
}

// ---------------------------------------------------------------------------
// The whole framework view
// ---------------------------------------------------------------------------

function normalizeStandardisedFramework(value: unknown) {
  const row = asRecord(value);
  const measure = normalizeMeasures(row.measures);
  const pctTier1 = asFigure(row.pctTier1);
  const threshold = asFigure(row.outlierThresholdPct);
  return {
    run: normalizeRunSummary(row.run),
    asOf: asMoment(row.asOf),
    reportingCurrency: asNullableText(row.reportingCurrency),
    mandate: normalizeMandate(row.mandate),
    parameters: mapList(row.parameters, normalizeParameter),
    buckets: mapList(row.buckets, normalizeBucket),
    currencies: mapList(row.currencies, normalizeCurrencyScope),
    excludedCurrencies: (Array.isArray(row.excludedCurrencies)
      ? row.excludedCurrencies
      : []
    ).filter((entry): entry is string => typeof entry === "string"),
    scenarios: mapList(row.scenarios, normalizeScenario),
    measures: measure,
    tier1: asFigure(row.tier1),
    pctTier1,
    outlier: asFlag(row.outlier),
    outlierThresholdPct: threshold,
    /**
     * The verdict is only assessable when BOTH sides arrived. A ratio compared
     * with a missing threshold is a pass nobody granted — the same fail-open
     * the platform's floor guard exists to prevent.
     */
    outlierAssessable: pctTier1 !== null && threshold !== null,
    table8: mapList(row.table8, normalizeTable8Row),
    table7Quantitative: normalizeTable7(row.table7Quantitative),
    nmdCategories: mapList(row.nmdCategories, normalizeNmdCategory),
    ladders: mapList(row.ladders, normalizeLadderRow),
    dataQuality: normalizeDataQuality(row.dataQuality),
    statements: (Array.isArray(row.statements) ? row.statements : []).filter(
      (entry): entry is string => typeof entry === "string" && entry !== "",
    ),
    /**
     * Why the automatic-option add-on is what it is. Silence would read
     * identically as "no options" and as "options not modelled" (DV-010), so
     * an absent sentence is reported as absent rather than skipped.
     */
    automaticOptionStatement: asNullableText(row.automaticOptionStatement),
    postShockFloorStatement: asNullableText(row.postShockFloorStatement),
  };
}

export {
  normalizeAssumption,
  normalizeAttempts,
  normalizeCurrencyScope,
  normalizeDataQuality,
  normalizeMandate,
  normalizeMeasures,
  normalizeParameter,
  normalizeRunSummary,
  normalizeScenario,
  normalizeStandardisedFramework,
  normalizeTable8Row,
};

/** Compile-time proof the adapter reads the contract it claims to. */
export type SfWireBody = WireSf;
export type SfRunListWireBody = WireRunList;
