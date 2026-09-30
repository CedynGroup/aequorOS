/**
 * The authority sentence an Org Owner composes, as scalar data.
 *
 * Everything here is PURE: option vocabularies, the request bodies the composer
 * posts, the validation that mirrors the server's own, the cache identity of
 * the branch directory, and the production copy that describes a grant's
 * coverage in the Members list. Nothing in this module fetches — the generated
 * operations in `@aequoros/risk-service-api` are the transport, called from
 * `lib/api/grantAdministration.ts` and the composer itself.
 *
 * Phase 4 added the fifth dimension — WHICH SLICE of an institution's book the
 * sentence admits (`docs/bi.md` §Phase 4). It is still one indivisible
 * statement: a branch list narrows one binding, it never fans out into several.
 */

import type {
  AccessRequestApprove,
  BindingCreateRequest,
  BindingCreateRequestRoleBundleEnum,
  BindingPreviewRequest,
  BindingRead,
  InstitutionScope,
  MemberRead,
  ModuleScope,
  SensitivityScope,
  GrantReasonCategory,
  SsoAccessRequestApprove,
} from "@aequoros/risk-service-api";
import { scopedQueryKey, type QueryAuthorityScope } from "./queryPolicy";

/**
 * Which slice of one institution's book a grant admits.
 *
 * Mirrors `authorization_bindings.data_scope_kind`, added by migration
 * `202609270073_authorization_data_scopes.py`. `all` is the only kind whose
 * value list is empty; the database CHECK refuses every other combination, so
 * this type must never be able to express one.
 */
export type GrantDataScopeKind = "all" | "branch" | "region";

export type GrantDataScope = Readonly<{
  kind: GrantDataScopeKind;
  /** Branch codes, or declared region names. Empty ONLY when `kind` is `all`. */
  values: readonly string[];
}>;

/** The default, and the widest: everything the institution has. */
export const WHOLE_INSTITUTION_BOOK: GrantDataScope = Object.freeze({
  kind: "all",
  values: Object.freeze([]) as readonly string[],
});

/**
 * The wire field names, taken from the migration that created the columns.
 *
 * `grantRequirements.parity.test.ts` reads all THREE places these names have to
 * agree and fails if any drifts: the migration that created the columns, the
 * request contract that accepts them, and the generated serializer that has to
 * emit them. That third check is the permanent form of the tripwire the interim
 * transport used to be — a client regenerated against a schema that renamed or
 * dropped either column would silently stop sending it, and the server default
 * would then widen the grant to the whole book.
 */
export const DATA_SCOPE_KIND_FIELD = "data_scope_kind";
export const DATA_SCOPE_VALUES_FIELD = "data_scope_values";

/**
 * The server's own cap on how many branches or regions one sentence may name
 * (`ScopedGrantInput.data_scope_values`, `max_length=500`). Mirrored so the
 * composer says so rather than posting a request it knows will be refused.
 */
export const MAX_DATA_SCOPE_VALUES = 500;

export type GrantDraft = Readonly<{
  roleBundle: BindingCreateRequestRoleBundleEnum;
  institutionScope: InstitutionScope;
  institutionId?: string;
  moduleScope: ModuleScope;
  sensitivityScope: SensitivityScope;
  /** Which part of the institution's book. Never optional: the widest scope is
   *  a decision, and a missing one would be indistinguishable from it. */
  dataScope: GrantDataScope;
  reasonCategory: GrantReasonCategory;
  reasonDetail: string;
  reference: string;
  validUntil: string;
}>;

export const ROLE_OPTIONS = [
  ["viewer", "Viewer"],
  ["auditor", "Auditor"],
  ["analyst", "Analyst"],
  ["approver", "Approver"],
  // The only bundle that transmits to the regulator. It carries VIEW and
  // SUBMIT and deliberately NOT approve: before 2026-09-20 approving and
  // filing shared one permission, so whoever approved a return could also send
  // it to the regulator alone. Granting one identity both Approver and
  // Validator reinstates that, and the server's assignment-time SoD decision
  // blocks it until the stage engine's per-object condition lands.
  ["validator", "Validator"],
  ["account_admin", "Organization Administrator"],
] as const;

export const MODULE_OPTIONS = [
  ["liq", "Liquidity Monitoring"],
  ["cap", "Basel Capital"],
  ["credit", "Credit"],
  ["irrbb", "IRRBB"],
  ["fx", "Foreign Exchange"],
  ["ftp", "Funds Transfer Pricing"],
  ["fcst", "Forecasting"],
  ["beh", "Behavioral Models"],
  ["data", "Data Engine"],
  ["reg", "Regulatory Reporting"],
  ["risk", "Risk & Limits"],
  ["markets", "Markets"],
  ["institution", "Institution Profile"],
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

/**
 * The three choices, in production copy.
 *
 * No enum ever reaches the screen: an Org Owner is choosing between "the whole
 * book", "some branches" and "some regions", which is a sentence, not a
 * vocabulary.
 */
export const BOOK_COVERAGE_OPTIONS = [
  ["all", "The institution's whole book"],
  ["branch", "Only the branches I choose"],
  ["region", "Only the regions I choose"],
] as const satisfies readonly (readonly [GrantDataScopeKind, string])[];

function optionLabel(
  options: readonly (readonly [string, string])[],
  value: string,
): string {
  return options.find(([candidate]) => candidate === value)?.[1] ?? value;
}

/**
 * Trim, drop blanks, de-duplicate, order — the mirror of the server's
 * `normalise_data_scope_values`, so one grant has one spelling on the wire and
 * a locally derived label reads in the same order the server's does. Ordinal
 * sort, not locale, and CASE IS PRESERVED: a branch code is the core banking
 * system's own token and folding it would collide two different branches.
 */
function cleanValues(values: readonly string[]): readonly string[] {
  return [
    ...new Set(values.map((value) => value.trim()).filter((value) => value)),
  ].sort();
}

/**
 * The scope this draft may actually state.
 *
 * Two narrowings happen here and nowhere else, so no caller can skip them:
 *
 * 1. **Organization-wide coverage cannot name branches.** One sentence reaching
 *    several institutions would have to mean a different branch list in each,
 *    and a code belonging to one bank means nothing in its sibling. The server
 *    refuses that combination at its boundary — "Selected branches or regions
 *    belong to one institution" — and the composer must therefore never compose
 *    it: the DATABASE permits the shape, so only these two layers stand between
 *    a stored row and a reader resolving its codes against whichever
 *    institution it happened to be querying.
 * 2. **`all` carries no values.** The database CHECK refuses `all` with a list,
 *    because a list nobody honours today is a list a future reader might start
 *    honouring — silently narrowing a grant nobody meant to narrow.
 */
export function statedDataScope(
  draft: Pick<GrantDraft, "institutionScope" | "dataScope">,
): GrantDataScope {
  if (draft.institutionScope !== "institution") return WHOLE_INSTITUTION_BOOK;
  if (draft.dataScope.kind === "all") return WHOLE_INSTITUTION_BOOK;
  return {
    kind: draft.dataScope.kind,
    values: cleanValues(draft.dataScope.values),
  };
}

/**
 * Why this coverage cannot be granted yet, or null when it is complete.
 *
 * The mirror of the CHECK constraint's second half, and of the server
 * validator in front of it ("Select at least one branch or region…"): a
 * narrowing kind with an empty list is unstorable on purpose, because the
 * obvious way to write the reader (`if values: inject a filter`) would serve
 * such a principal the WHOLE book. Said here so the Owner fixes it in the
 * composer rather than meeting a 422 on the review step.
 */
export function grantScopeRefusal(
  draft: Pick<GrantDraft, "institutionScope" | "dataScope">,
): string | null {
  const scope = statedDataScope(draft);
  if (scope.kind === "all") return null;
  if (scope.values.length === 0) {
    return scope.kind === "branch"
      ? "Choose at least one branch, or give this grant the institution's whole book."
      : "Choose at least one region, or give this grant the institution's whole book.";
  }
  if (scope.values.length > MAX_DATA_SCOPE_VALUES) {
    const noun = scope.kind === "branch" ? "branches" : "regions";
    return (
      `One grant can name at most ${MAX_DATA_SCOPE_VALUES} ${noun}. ` +
      `Give this grant the institution's whole book instead.`
    );
  }
  return null;
}

/**
 * The scalar scope every grant request carries, as the generated request model.
 *
 * WHY THIS IS A MODEL AND NOT AN OBJECT LITERAL. Phase 4 added the coverage
 * columns to a route the client had already been generated against, and
 * `BindingCreateRequestToJSON` hand-enumerates its keys with no spread — so for
 * as long as the package was stale, posting through the generated operation
 * dropped the pair in the browser, the column default (`all`) applied, and an
 * Org Owner who chose two branches granted the whole book with a success
 * dialog. The interim answer was a hand-written transport
 * (`components/settings/grantTransport.ts`, deleted with this change). The
 * permanent one is this: the client is regenerated, the composer posts through
 * `authorizationApi` / `authApi` again, and the shape of what it sends is
 * checked by the COMPILER against the generated contract rather than by a
 * second hand-written one nobody diffs.
 *
 * The data-scope pair is left UNSET when the coverage is the whole book. The
 * generated serializer emits an unset field as `undefined` and `JSON.stringify`
 * drops it, so both columns are absent from the request and the server default
 * applies — which says exactly what sending `all` would.
 *
 * A NARROWING kind is sent even when nothing has been chosen yet, and that is
 * the other half of the same decision. `grantScopeRefusal` stops the composer
 * long before this point, but if some future caller ignored it, sending
 * `branch` with an empty list is REFUSED by the database CHECK — while omitting
 * the pair would have quietly granted the whole book. The unstorable shape is
 * the safe one.
 */
type GrantScopeFields = Pick<
  BindingCreateRequest,
  | "dataScopeKind"
  | "dataScopeValues"
  | "institutionId"
  | "institutionScope"
  | "moduleScope"
  | "reasonCategory"
  | "reasonDetail"
  | "reference"
  | "roleBundle"
  | "sensitivityScope"
  | "validUntil"
>;

function scopeFields(draft: GrantDraft): GrantScopeFields {
  const scope = statedDataScope(draft);
  const narrowed = scope.kind !== "all";
  return {
    institutionScope: draft.institutionScope,
    // Sent ONLY for an institution target: the server's own validator forbids
    // an id on an organization-wide grant, and `undefined` is dropped from the
    // request rather than posted as null.
    institutionId:
      draft.institutionScope === "institution" && draft.institutionId
        ? draft.institutionId
        : undefined,
    moduleScope: draft.moduleScope,
    reasonCategory: draft.reasonCategory,
    reasonDetail: draft.reasonDetail.trim(),
    reference: draft.reference.trim() || undefined,
    roleBundle: draft.roleBundle,
    sensitivityScope: draft.sensitivityScope,
    // A `datetime-local` value, sent as the instant it names in the browser.
    validUntil: draft.validUntil
      ? new Date(draft.validUntil).toISOString()
      : undefined,
    dataScopeKind: narrowed ? scope.kind : undefined,
    dataScopeValues: narrowed ? [...scope.values] : undefined,
  };
}

export function grantPreviewRequest(
  draft: GrantDraft,
  principalUserId: string,
): BindingPreviewRequest {
  return { ...scopeFields(draft), principalUserId };
}

export function grantCreateRequest(
  draft: GrantDraft,
  principalUserId: string,
  expectedAuthoritySentence: string,
): BindingCreateRequest {
  return { ...scopeFields(draft), principalUserId, expectedAuthoritySentence };
}

/** The SSO approval request: the same sentence, with the user in the path. */
export function ssoApprovalRequest(
  draft: GrantDraft,
  expectedAuthoritySentence: string,
): SsoAccessRequestApprove {
  return { ...scopeFields(draft), expectedAuthoritySentence };
}

/** Approving a permission request: the same sentence, with the request in the path. */
export function accessRequestApprovalRequest(
  draft: GrantDraft,
  expectedAuthoritySentence: string,
): AccessRequestApprove {
  return { ...scopeFields(draft), expectedAuthoritySentence };
}

/** Everything that decides which grant the preview describes. */
export function grantPreviewFingerprint(
  draft: GrantDraft,
  principalUserId: string,
): string {
  const scope = statedDataScope(draft);
  return [
    principalUserId,
    draft.roleBundle,
    draft.institutionScope,
    draft.institutionScope === "institution" ? (draft.institutionId ?? "") : "",
    draft.moduleScope,
    draft.sensitivityScope,
    scope.kind,
    scope.values.join(","),
    // The sentence states the expiry, and the reason decides whether the
    // server will preview the grant at all.
    draft.reasonCategory,
    draft.reasonDetail.trim(),
    draft.validUntil,
  ].join("|");
}

// --- the branch directory ----------------------------------------------------

export type InstitutionBranch = Readonly<{
  code: string;
  name: string;
  /** The bank's DECLARED region, or null. Never inferred from anything else. */
  region: string | null;
}>;

export type BranchDirectory = Readonly<{
  institutionId: string;
  branches: readonly InstitutionBranch[];
  /** Declared regions only. Empty when the register declares none. */
  regions: readonly string[];
}>;

/**
 * The branch directory could not be understood.
 *
 * Thrown rather than returned as an empty directory, because "this institution
 * has no branch register" and "this response is not a branch register" are
 * different facts and only one of them is the bank's. Reporting the second as
 * the first would tell an Org Owner their bank had supplied nothing.
 */
export class BranchDirectoryShapeError extends Error {
  constructor(detail: string) {
    super(`The branch list could not be read (${detail}).`);
    this.name = "BranchDirectoryShapeError";
  }
}

function field(source: unknown, key: string): unknown {
  if (source && typeof source === "object") {
    return (source as Record<string, unknown>)[key];
  }
  return undefined;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * `GET /organization/institutions/{institution_id}/branches`, parsed.
 *
 * The response is `BranchDirectoryRead`: `institution_id`, `branches` of
 * `{code, name, region}` and the declared `regions`. The register's own field
 * aliases (`unit_id` / `name` against the canonical `business_unit_id` /
 * `business_unit_name`) are resolved SERVER-side through
 * `business_units.normalise_row`, so this parser deliberately accepts only the
 * route's field names: a row it cannot read is a protocol failure, not a branch
 * to skip quietly. `grantRequirements.parity.test.ts` reads the response model
 * and fails if these three names drift.
 *
 * The caller now hands over the generated operation's own result, whose type
 * says every field is present — and this still validates it, because
 * `BranchDirectoryReadFromJSON` casts rather than checks: a response missing a
 * branch code type-checks as a `string` that is `undefined` at runtime. The
 * argument stays `unknown` so that fact cannot be forgotten. Both spellings of
 * the read names are the same word, so the regenerated client changed nothing
 * here.
 */
export function parseBranchDirectory(
  raw: unknown,
  institutionId: string,
): BranchDirectory {
  const rows = field(raw, "branches");
  if (!Array.isArray(rows)) {
    throw new BranchDirectoryShapeError("no branch list in the response");
  }
  const branches: InstitutionBranch[] = rows.map((row, index) => {
    const code = text(field(row, "code"));
    if (!code) {
      throw new BranchDirectoryShapeError(`branch ${index + 1} has no code`);
    }
    const region = text(field(row, "region"));
    return {
      code,
      name: text(field(row, "name")) || code,
      region: region || null,
    };
  });
  const declared = field(raw, "regions");
  const regions = Array.isArray(declared)
    ? cleanValues(declared.filter((value) => typeof value === "string"))
    : cleanValues(
        branches.flatMap((branch) => (branch.region ? [branch.region] : [])),
      );
  return { institutionId, branches, regions };
}

/**
 * The branch directory's cache identity.
 *
 * Per organization, per signed-in actor, per authorization generation (both
 * carried by `scope.authorityId`) and per institution. The institution is the
 * dimension a settings key has never needed before and the one that matters
 * most here: two institutions of one organization are one RLS tenant, so a key
 * without it would hand a sibling bank's branch list to the composer and the
 * Owner would grant codes that mean nothing there.
 */
export const INSTITUTION_BRANCHES_PREFIX = "settings-institution-branches";

export function institutionBranchesKey(
  scope: QueryAuthorityScope,
  institutionId: string | null | undefined,
) {
  return scopedQueryKey(
    INSTITUTION_BRANCHES_PREFIX,
    scope,
    institutionId ?? null,
  );
}

// --- what the composer may offer --------------------------------------------

export type BookCoverageAvailability = Readonly<
  | { status: "organization_wide"; reason: string }
  | { status: "loading" }
  | { status: "unavailable"; reason: string }
  | { status: "no_register"; reason: string }
  | { status: "branches_only"; reason: string; directory: BranchDirectory }
  | { status: "ready"; directory: BranchDirectory }
>;

export const ORGANIZATION_WIDE_COVERAGE_NOTE =
  "An organization-wide grant reaches institutions whose branch lists differ, " +
  "so branches cannot be named here. It covers each institution's whole book.";

export const COVERAGE_UNREADABLE_NOTE =
  "This institution's branch list could not be loaded, so coverage cannot be " +
  "narrowed right now. Granting now gives the institution's whole book.";

export const NO_BRANCH_REGISTER_NOTE =
  "This institution has not supplied a branch register, so its book cannot be " +
  "divided. This grant covers the whole institution.";

export const NO_REGIONS_DECLARED_NOTE =
  "This institution's branch register declares no regions, so coverage can " +
  "only be narrowed branch by branch.";

/**
 * What the scope control may offer, from the state of the branch request.
 *
 * `failed` is checked BEFORE emptiness, and that order is the whole point: a
 * request that did not answer must never be reported as a bank that supplied
 * nothing, and must never quietly leave the widest coverage looking like the
 * only sensible choice. The widest coverage stays selectable — it is what every
 * grant meant before this feature existed — but the reason it is the only one
 * on offer is stated.
 */
export function bookCoverageAvailability(input: {
  institutionScope: InstitutionScope;
  directory: BranchDirectory | null;
  failed: boolean;
  loading: boolean;
}): BookCoverageAvailability {
  if (input.institutionScope !== "institution") {
    return {
      status: "organization_wide",
      reason: ORGANIZATION_WIDE_COVERAGE_NOTE,
    };
  }
  if (input.failed) {
    return { status: "unavailable", reason: COVERAGE_UNREADABLE_NOTE };
  }
  if (input.loading || !input.directory) return { status: "loading" };
  if (input.directory.branches.length === 0) {
    return { status: "no_register", reason: NO_BRANCH_REGISTER_NOTE };
  }
  if (input.directory.regions.length === 0) {
    return {
      status: "branches_only",
      reason: NO_REGIONS_DECLARED_NOTE,
      directory: input.directory,
    };
  }
  return { status: "ready", directory: input.directory };
}

/** Whether one of the three choices can be stated in this state. */
export function bookCoverageChoiceAvailable(
  availability: BookCoverageAvailability,
  kind: GrantDataScopeKind,
): boolean {
  if (kind === "all") return true;
  if (availability.status === "ready") return true;
  return availability.status === "branches_only" && kind === "branch";
}

/** The directory behind a state, or null when there is nothing to choose from. */
export function coverageDirectory(
  availability: BookCoverageAvailability,
): BranchDirectory | null {
  if (availability.status === "ready") return availability.directory;
  if (availability.status === "branches_only") return availability.directory;
  return null;
}

// --- describing a grant that already exists ---------------------------------

export type GrantScopeDisplay = Readonly<{
  /** Production copy naming the slice, for a labelled field. */
  label: string;
  /** True when this grant is narrower than the institution's whole book. */
  narrowed: boolean;
  /** A short fragment for the members list, or null when nothing narrows it. */
  fragment: string | null;
}>;

const WHOLE_BOOK_DISPLAY: GrantScopeDisplay = Object.freeze({
  label: "The institution's whole book",
  narrowed: false,
  fragment: null,
});

/**
 * A narrowing this screen cannot describe.
 *
 * Reached when the row names a kind this build does not know, or a kind that
 * narrows with no usable values. Both are read as NARROWED: the row is not the
 * whole book, and saying "whole book" because the shape is unfamiliar would
 * overstate the grant — which is the one direction an access display must never
 * be wrong in.
 */
const UNDESCRIBABLE_DISPLAY: GrantScopeDisplay = Object.freeze({
  label:
    "A limited part of this institution's book that this screen cannot describe yet",
  narrowed: true,
  fragment: "Limited coverage",
});

function joinValues(values: readonly string[]): string {
  if (values.length <= 3) return values.join(", ");
  return `${values.slice(0, 3).join(", ")} and ${values.length - 3} more`;
}

/**
 * How to show one existing grant's coverage.
 *
 * The LABEL is the server's `data_scope_label` whenever the row carries one.
 * That is the same rule as the authority sentence: the server owns the copy
 * that describes stored authority, so a screen cannot word a grant differently
 * from the record of it. The locally derived wording below is the fallback for
 * a row that has no label — and it must exist, because a row read from a
 * backend that has not been deployed yet would otherwise be described by
 * nothing at all.
 *
 * Every field is read in BOTH spellings on purpose. The generated client
 * spreads the raw JSON and then overwrites the fields it knows about, so a
 * column it has not been regenerated against arrives under its wire name and
 * becomes camelCase the day the client is regenerated — which for the coverage
 * columns has now happened, so `dataScopeKind` is the live path. The wire
 * spelling is kept because reading one spelling only would make a
 * branch-scoped grant read as institution-wide on exactly one side of a
 * regeneration, and this display must never overstate a grant.
 */
export function grantScopeDisplay(grant: unknown): GrantScopeDisplay {
  const rawKind =
    field(grant, "dataScopeKind") ?? field(grant, "data_scope_kind");
  const serverLabel = text(
    field(grant, "dataScopeLabel") ?? field(grant, "data_scope_label"),
  );
  if (rawKind === undefined || rawKind === null || rawKind === "all") {
    return serverLabel
      ? { ...WHOLE_BOOK_DISPLAY, label: serverLabel }
      : WHOLE_BOOK_DISPLAY;
  }
  const rawValues =
    field(grant, "dataScopeValues") ?? field(grant, "data_scope_values");
  const values = Array.isArray(rawValues)
    ? cleanValues(rawValues.filter((value) => typeof value === "string"))
    : [];
  const derived =
    values.length === 0
      ? null
      : rawKind === "branch"
        ? {
            label: `Selected branches: ${joinValues(values)}`,
            fragment: `${values.length} ${values.length === 1 ? "branch" : "branches"}`,
          }
        : rawKind === "region"
          ? {
              label: `Selected regions: ${joinValues(values)}`,
              fragment: `${values.length} ${values.length === 1 ? "region" : "regions"}`,
            }
          : null;
  if (!derived) {
    return serverLabel
      ? { ...UNDESCRIBABLE_DISPLAY, label: serverLabel }
      : UNDESCRIBABLE_DISPLAY;
  }
  return {
    label: serverLabel || derived.label,
    narrowed: true,
    fragment: derived.fragment,
  };
}

/** The coverage the composer is about to state, for the review step. */
export function draftScopeLabel(draft: GrantDraft): string {
  const scope = statedDataScope(draft);
  if (scope.kind === "all") return WHOLE_BOOK_DISPLAY.label;
  return grantScopeDisplay({
    data_scope_kind: scope.kind,
    data_scope_values: [...scope.values],
  }).label;
}

export function compactGrantFragment(grant: BindingRead): string {
  const role = optionLabel(ROLE_OPTIONS, grant.roleBundle);
  const moduleLabel = optionLabel(MODULE_OPTIONS, grant.moduleScope);
  const institution =
    grant.institutionScope === "organization"
      ? "Every institution"
      : (grant.institutionName ?? grant.institutionId ?? "Institution");
  // The coverage rides on the summary whenever it narrows the grant. Without
  // it the row reads as institution-wide for a grant that is not, and the row
  // is the only place most grants are ever looked at.
  const scope = grantScopeDisplay(grant).fragment;
  return [role, moduleLabel, institution, ...(scope ? [scope] : [])].join(
    " · ",
  );
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
