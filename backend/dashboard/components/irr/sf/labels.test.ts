/**
 * The IRRBB Standardised Framework copy, held to the four rules it exists for.
 *
 *  1. **No raw token reaches a reader.** A scenario, a deposit category, a
 *     time bucket, a modelling default and a run status all arrive as codes;
 *     an UNLABELLED one gets a neutral sentence rather than the code itself.
 *  2. **Nothing unrecognised is drawn as a pass.** An unknown run status is
 *     neutral, and an outlier verdict that cannot be assessed says so instead
 *     of reading as "below the threshold".
 *  3. **A refusal is a refusal.** The one refusal name has one sentence, that
 *     sentence names what could not be valued and why the measure is withheld
 *     rather than reported, and an unrecognised code still refuses in words.
 *  4. **No digits (D-024) and no jurisdiction literal.** A sentence stating a
 *     shock, a threshold, a cap or a date would be a regulatory value written
 *     into display code; a currency code, a regulator name or a country
 *     belongs to `lib/format.ts`, which reads them from the institution.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";

import {
  APPLIED_TO,
  ASSUMPTIONS_MEANING,
  ASSUMPTIONS_NONE,
  CONFIRMED_CHIP,
  COUNT_NOT_REPORTED,
  CURRENCIES_MEANING,
  EXCLUSIONS_MEANING,
  EXCLUSIONS_NONE,
  IMMUTABLE_RUN,
  INTERIM_STILL_VALID,
  INTERIM_SUPERSEDED,
  LADDER_MEANING,
  MANDATE_HEADING_NOT_YET,
  MANDATE_HEADING_REQUIRED,
  MANDATE_HEADING_UNKNOWN,
  MANDATE_PENDING,
  MANDATE_UNREADABLE,
  MEASURES_MEANING,
  NMD_MEANING,
  NOT_REPORTED,
  NO_RESULT_ASK_COLLEAGUE,
  NO_RESULT_RUN_IT,
  OUTLIER_ABOVE,
  OUTLIER_BELOW,
  OUTLIER_NOT_ASSESSABLE,
  PARAMETERS_EMPTY,
  PENDING_CHIP,
  PENDING_MEANING,
  REFUSAL_HEADING,
  REFUSAL_NEXT_STEP,
  REPRESENTATIVE_CHIP,
  REPRESENTATIVE_MEANING,
  RUN_ACTION,
  RUN_FAILED,
  RUN_NEEDS_AUTHORITY,
  RUN_NEEDS_PERIOD,
  SCENARIOS_MEANING,
  SF_OPTIONS_UNSUPPORTED,
  SUPERVISORY_MONITORING,
  TABLE8_MEANING,
  UNNAMED_BUCKET,
  UNNAMED_CATEGORY,
  UNNAMED_DEFAULT,
  UNNAMED_LINE,
  UNNAMED_MEASURE,
  UNNAMED_SCENARIO,
  VIEW_NEEDS_AUTHORITY,
  labelled,
  mandateCopy,
  outlierSentence,
  outlierTone,
  refusalSentence,
  runStatusCopy,
} from "./labels";
import {
  SF_NOT_ENABLED,
  SF_NOT_ENABLED_TITLE,
  SF_NO_RESULT,
  SF_NO_RESULT_TITLE,
  sfNotice,
} from "./availability";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

/** A mandate as the normaliser would produce it, with the fields overridden. */
function mandate(overrides: Record<string, unknown> = {}) {
  return {
    mandatory: false,
    mandatoryFrom: null,
    asOf: null,
    confirmationStatus: null,
    sourceCitation: null,
    statement: null,
    stated: false,
    settled: false,
    ...overrides,
  } as Parameters<typeof mandateCopy>[0];
}

/**
 * "Pillar 2" is the NAME of a supervisory pillar, not a quantity.
 *
 * The digit rule exists so a threshold, a shock size, a cap or a deadline
 * cannot be written into display code. The pillar's name carries none of that
 * meaning and cannot be reworded without making the sentence wrong, so it is
 * removed before the check — and named here so the exemption is one reviewed
 * phrase rather than a relaxed rule.
 */
const PILLAR_NAME = /Pillar 2/g;

/**
 * Every sentence this module can put in front of a reader.
 *
 * Listed rather than reflected so that adding copy without adding it here is
 * visible in the diff — the same trade the P3 suite makes.
 */
const SENTENCES: string[] = [
  SUPERVISORY_MONITORING,
  IMMUTABLE_RUN,
  MANDATE_UNREADABLE,
  MANDATE_PENDING,
  MANDATE_HEADING_REQUIRED,
  MANDATE_HEADING_NOT_YET,
  MANDATE_HEADING_UNKNOWN,
  INTERIM_STILL_VALID,
  INTERIM_SUPERSEDED,
  REFUSAL_HEADING,
  REFUSAL_NEXT_STEP,
  refusalSentence(SF_OPTIONS_UNSUPPORTED, null),
  refusalSentence(null, null),
  REPRESENTATIVE_CHIP,
  REPRESENTATIVE_MEANING,
  PENDING_CHIP,
  PENDING_MEANING,
  CONFIRMED_CHIP,
  PARAMETERS_EMPTY,
  ASSUMPTIONS_MEANING,
  ASSUMPTIONS_NONE,
  EXCLUSIONS_MEANING,
  EXCLUSIONS_NONE,
  APPLIED_TO,
  COUNT_NOT_REPORTED,
  NOT_REPORTED,
  OUTLIER_ABOVE,
  OUTLIER_BELOW,
  OUTLIER_NOT_ASSESSABLE,
  UNNAMED_SCENARIO,
  UNNAMED_MEASURE,
  UNNAMED_CATEGORY,
  UNNAMED_BUCKET,
  UNNAMED_DEFAULT,
  UNNAMED_LINE,
  RUN_ACTION,
  RUN_FAILED,
  RUN_NEEDS_PERIOD,
  RUN_NEEDS_AUTHORITY,
  NO_RESULT_ASK_COLLEAGUE,
  NO_RESULT_RUN_IT,
  VIEW_NEEDS_AUTHORITY,
  MEASURES_MEANING,
  SCENARIOS_MEANING,
  TABLE8_MEANING,
  CURRENCIES_MEANING,
  NMD_MEANING,
  LADDER_MEANING,
  SF_NOT_ENABLED,
  SF_NOT_ENABLED_TITLE,
  SF_NO_RESULT,
  SF_NO_RESULT_TITLE,
  runStatusCopy("succeeded").label,
  runStatusCopy("failed").label,
  runStatusCopy(null).label,
];

// ---------------------------------------------------------------------------
// 1. The screen is not a return
// ---------------------------------------------------------------------------

test("the standing sentence says these figures are not filed", () => {
  assert.ok(/not a return/i.test(SUPERVISORY_MONITORING));
  assert.ok(/supervisory monitoring/i.test(SUPERVISORY_MONITORING));
  // And it names the one route by which a framework figure DOES reach a filing,
  // so "not filed" cannot be read as "never matters".
  assert.ok(/capital requirement/i.test(SUPERVISORY_MONITORING));
});

// ---------------------------------------------------------------------------
// 2. The mandate is accurate on BOTH sides, and never presented as settled
// ---------------------------------------------------------------------------

test("an unreadable mandate says neither required nor not required", () => {
  const copy = mandateCopy(mandate());
  assert.equal(copy.heading, MANDATE_HEADING_UNKNOWN);
  assert.equal(copy.body, MANDATE_UNREADABLE);
  assert.ok(!/not required\b(?!.*unresolved)/i.test(copy.body.split(".")[0]));
  // The dangerous reading is the reassuring one, so the sentence says so
  // explicitly rather than leaving it to inference.
  assert.ok(/unresolved/i.test(copy.body));
  assert.notEqual(copy.tone, "slate");
});

test("before the date, the interim method is stated to be CORRECT", () => {
  const copy = mandateCopy(
    mandate({ stated: true, statement: "It becomes mandatory later." }),
  );
  assert.equal(copy.heading, MANDATE_HEADING_NOT_YET);
  assert.ok(copy.body.includes("It becomes mandatory later."));
  assert.ok(copy.body.includes(INTERIM_STILL_VALID));
  assert.ok(/correct basis/i.test(copy.body));
  // A preparer using the interim method today is not doing anything wrong.
  assert.ok(!/must|cannot|violation|breach/i.test(INTERIM_STILL_VALID));
});

test("from the date, the interim method is stated to be superseded", () => {
  const copy = mandateCopy(
    mandate({ stated: true, mandatory: true, statement: "It applies now." }),
  );
  assert.equal(copy.heading, MANDATE_HEADING_REQUIRED);
  assert.ok(copy.body.includes("It applies now."));
  assert.ok(copy.body.includes(INTERIM_SUPERSEDED));
  assert.ok(/cannot be frozen/i.test(copy.body));
});

test("a pending commencement date is never presented as settled", () => {
  const pending = mandateCopy(
    mandate({ stated: true, statement: "x", confirmationStatus: "pending" }),
  );
  assert.equal(pending.qualifier, MANDATE_PENDING);
  assert.ok(/pending confirmation/i.test(MANDATE_PENDING));
  assert.ok(/may change/i.test(MANDATE_PENDING));

  // Only the server's own "confirmed" drops the qualifier. An absent status,
  // an unknown status and an empty one all keep it.
  for (const status of [null, "", "approved", "active"]) {
    assert.equal(
      mandateCopy(
        mandate({ stated: true, statement: "x", confirmationStatus: status }),
      ).qualifier,
      MANDATE_PENDING,
      `a "${String(status)}" confirmation status dropped the qualifier`,
    );
  }
  assert.equal(
    mandateCopy(
      mandate({ stated: true, statement: "x", settled: true }),
    ).qualifier,
    null,
  );
});

test("the mandate copy never writes a date of its own", () => {
  for (const sentence of [
    MANDATE_UNREADABLE,
    MANDATE_PENDING,
    INTERIM_STILL_VALID,
    INTERIM_SUPERSEDED,
    MANDATE_HEADING_REQUIRED,
    MANDATE_HEADING_NOT_YET,
    MANDATE_HEADING_UNKNOWN,
  ]) {
    assert.ok(
      !/\d/.test(sentence.replace(PILLAR_NAME, "")),
      `a date or number reached: "${sentence}"`,
    );
  }
});

// ---------------------------------------------------------------------------
// 3. A refusal is a refusal — never a zero, never a blank
// ---------------------------------------------------------------------------

test("the options refusal has ONE name", () => {
  assert.equal(SF_OPTIONS_UNSUPPORTED, "irrbb_sf_options_unsupported");
  // The retired second name must not reappear anywhere in the module.
  assert.ok(!/automatic_options_not_modelled/.test(refusalSentence(SF_OPTIONS_UNSUPPORTED, null)));
});

test("the refusal sentence says what could not be valued, and why nothing is shown", () => {
  const sentence = refusalSentence(SF_OPTIONS_UNSUPPORTED, null);
  assert.ok(/option/i.test(sentence));
  assert.ok(/refused/i.test(sentence));
  assert.ok(/understate/i.test(sentence));
  assert.ok(!/\d/.test(sentence));
  // And the next step is an action, not a reassurance.
  assert.ok(/another way|supervisor/i.test(REFUSAL_NEXT_STEP));
  assert.ok(/understate/i.test(REFUSAL_NEXT_STEP));
  assert.ok(!/zero|nil|no risk/i.test(REFUSAL_NEXT_STEP));
});

test("the refusal never prints the raw code, and never falls silent", () => {
  const unknown = refusalSentence("some_future_code", null);
  assert.ok(!unknown.includes("some_future_code"));
  assert.ok(unknown.trim() !== "");
  assert.ok(/refused/i.test(unknown));
  // A message the server DID send is preferred over the generic sentence.
  assert.equal(refusalSentence("some_future_code", "Books disagree."), "Books disagree.");
  assert.ok(/refused/i.test(refusalSentence(null, "   ")));
});

test("the refusal heading does not read as a system failure", () => {
  assert.ok(/could not measure/i.test(REFUSAL_HEADING));
  assert.ok(!/error|crash|broken/i.test(REFUSAL_HEADING));
});

// ---------------------------------------------------------------------------
// 4. Verdicts: nothing unrecognised is a pass
// ---------------------------------------------------------------------------

test("an unassessable outlier test says so and is NOT toned as a pass", () => {
  assert.equal(outlierSentence(false, false), OUTLIER_NOT_ASSESSABLE);
  assert.equal(outlierSentence(false, true), OUTLIER_NOT_ASSESSABLE);
  assert.equal(outlierTone(false, false), "slate");
  assert.equal(outlierTone(false, true), "slate");
  assert.ok(/pass nobody granted/i.test(OUTLIER_NOT_ASSESSABLE));
});

test("an assessable outlier test states the side it fell", () => {
  assert.equal(outlierSentence(true, true), OUTLIER_ABOVE);
  assert.equal(outlierSentence(true, false), OUTLIER_BELOW);
  assert.equal(outlierTone(true, true), "breach");
  assert.equal(outlierTone(true, false), "compliant");
});

test("an unrecognised run status is neutral, never a pass", () => {
  const unknown = runStatusCopy("teleported");
  assert.equal(unknown.tone, "slate");
  assert.ok(!unknown.label.includes("teleported"));
  assert.equal(runStatusCopy(null).tone, "slate");
  assert.equal(runStatusCopy("succeeded").tone, "compliant");
  assert.equal(runStatusCopy("failed").tone, "breach");
});

// ---------------------------------------------------------------------------
// 5. No raw token, ever
// ---------------------------------------------------------------------------

test("an unlabelled code gets a name, never the code", () => {
  for (const fallback of [
    UNNAMED_SCENARIO,
    UNNAMED_MEASURE,
    UNNAMED_CATEGORY,
    UNNAMED_BUCKET,
    UNNAMED_DEFAULT,
    UNNAMED_LINE,
  ]) {
    assert.equal(labelled(null, fallback), fallback);
    assert.equal(labelled("", fallback), fallback);
    assert.equal(labelled("   ", fallback), fallback);
    assert.ok(!/_/.test(fallback), `a token leaked into "${fallback}"`);
  }
  assert.equal(labelled("Parallel up", UNNAMED_SCENARIO), "Parallel up");
});

test("no label in this module carries an underscored token", () => {
  for (const sentence of SENTENCES) {
    assert.ok(
      !/\b[a-z]+_[a-z_]+\b/.test(sentence),
      `an underscored token reached display copy: "${sentence}"`,
    );
  }
});

// ---------------------------------------------------------------------------
// 6. The two "nothing here" states are distinguishable, and neither leaks
// ---------------------------------------------------------------------------

test("a 404 is no result, and 405/501 is not enabled", () => {
  assert.equal(sfNotice({ status: 404 })?.kind, "no-result");
  assert.equal(sfNotice({ status: 405 })?.kind, "not-enabled");
  assert.equal(sfNotice({ status: 501 })?.kind, "not-enabled");
});

test("a genuine failure is NOT a notice — it must still look like an error", () => {
  for (const status of [400, 403, 409, 422, 500, 503]) {
    assert.equal(sfNotice({ status }), null, `${status} was swallowed`);
  }
  assert.equal(sfNotice(null), null);
  assert.equal(sfNotice(undefined), null);
  assert.equal(sfNotice("boom"), null);
  assert.equal(sfNotice({}), null);
});

test("the server's own 404 sentence is never printed", () => {
  // Both 404s this route can answer — "no run yet" and the deny-hide "Bank not
  // found." — must render OUR copy, so the deny-hide can neither confuse a
  // reader nor hint at what exists.
  const denied = sfNotice({
    status: 404,
    message: "Bank not found.",
    name: "ApiError",
  });
  assert.equal(denied?.message, SF_NO_RESULT);
  assert.ok(!denied?.message.includes("Bank not found"));
  assert.equal(denied?.title, SF_NO_RESULT_TITLE);
});

test("the graceful empty envelope keeps the server's own reason", () => {
  const notice = sfNotice({
    name: "ModuleUnavailableError",
    reason: "No canonical book has been ingested for this institution.",
  });
  assert.equal(notice?.kind, "not-enabled");
  assert.equal(
    notice?.message,
    "No canonical book has been ingested for this institution.",
  );
  assert.equal(
    sfNotice({ name: "ModuleUnavailableError", reason: "  " })?.message,
    SF_NOT_ENABLED,
  );
});

test("neither empty state reads as a fault", () => {
  for (const sentence of [SF_NOT_ENABLED, SF_NO_RESULT]) {
    assert.ok(/nothing has gone wrong/i.test(sentence));
  }
});

// ---------------------------------------------------------------------------
// 7. D-024 and jurisdiction neutrality, over the whole module
// ---------------------------------------------------------------------------


test("no sentence in this module states a number", () => {
  for (const sentence of SENTENCES) {
    assert.ok(
      !/\d/.test(sentence.replace(PILLAR_NAME, "")),
      `a digit reached display copy: "${sentence}"`,
    );
  }
});

test("the pillar-name exemption is still earned", () => {
  assert.ok(
    SENTENCES.some((sentence) => PILLAR_NAME.test(sentence)),
    "no sentence uses the pillar's name — drop the exemption",
  );
  PILLAR_NAME.lastIndex = 0;
});

function dashboardRoot(): string {
  let dir = __dirname;
  for (let index = 0; index < 8; index += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const ROOT = dashboardRoot();
const SF_DIR = join(ROOT, "components/irr/sf");
const ENTRY = join(ROOT, "components/irr/StandardisedFramework.tsx");

/** Source with comments removed — a header may name what it forbids. */
function code(file: string): string {
  return readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
      continue;
    }
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

test("no framework file spells a jurisdiction, a regulator or a currency", () => {
  const files = [...sourceFiles(SF_DIR), ENTRY];
  assert.ok(files.length > 0, "the scan found no files — it would pass vacuously");
  for (const file of files) {
    const source = code(file);
    for (const literal of ["GHS", "BoG", "Ghana", "cedi", "Bank of Ghana"]) {
      assert.ok(
        !source.includes(literal),
        `${file} spells "${literal}" — read it from the institution instead`,
      );
    }
  }
  console.log(
    `IRRBB standardised framework: ${files.length} file(s) jurisdiction-neutral`,
  );
});

if (failures > 0) {
  console.error(`${failures} IRRBB standardised framework copy test(s) failed`);
  process.exit(1);
}
console.log("IRRBB standardised framework copy: all checks passed");
