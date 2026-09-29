/**
 * The three Phase 2 surfaces a module landing page mounts — and that were
 * built and never mounted (audit A360-7 S3).
 *
 * `docs/bi.md` §Insights layer: "`<InsightStrip>` sits under `PageHeader` on
 * the Command Center and every module landing page. `KpiStat` gains an
 * `explain` prop that opens `ExplainDrawer`. `TrustBadge` goes in the
 * `ChartFrame` actions slot." All three existed, in `components/bi/`, reached
 * by exactly one route. A module user never saw a statement, a trust verdict or
 * a provenance drawer. Every gate was green, because each piece was correct.
 *
 * So this file reads the SOURCE of the landing pages and asserts they reach the
 * pieces — the shape `lib/api/grants.test.ts` and `lib/api/ask.test.ts` use,
 * because "the component exists and nothing on screen calls it" is a defect no
 * unit test of the component can see. Comments are stripped before scanning so
 * prose about a symbol is not read as a use of it. The pure resolver behind the
 * explain affordance is exercised directly: it decides which catalogue measure a
 * KPI is a copy of for THIS institution, and a wrong pick is a drawer that names
 * the wrong source.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  KPI_EXPLAIN_LABEL,
  kpiExplainQuery,
  liveEngineMeasureId,
  type EngineMeasureLike,
} from "../ui/KpiStat";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error instanceof Error ? error.message : error);
  }
}

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

function repoRoot(): string {
  let dir = dashboardRoot();
  for (let index = 0; index < 8; index += 1) {
    if (existsSync(join(dir, "backend", "app"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("could not locate the repository root from " + __dirname);
}

const ROOT = dashboardRoot();

/** A file's source with its comments removed. */
function code(relativePath: string): string {
  const source = readFileSync(join(ROOT, relativePath), "utf8");
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
}

/** Every `<ChartFrame …>…</ChartFrame>` block in a page, in order. */
function chartFrames(source: string): string[] {
  const blocks: string[] = [];
  let cursor = 0;
  for (;;) {
    const open = source.indexOf("<ChartFrame", cursor);
    if (open < 0) break;
    const close = source.indexOf("</ChartFrame>", open);
    assert.ok(close > open, "unterminated <ChartFrame");
    blocks.push(source.slice(open, close));
    cursor = close;
  }
  return blocks;
}

/**
 * The Command Center and every module landing page in the spec's sense: the
 * route a module's sidebar entry lands on. IRRBB's is `app/(app)/irr/page.tsx`,
 * which is outside this track's remit and is deliberately not listed here yet;
 * add it when it is mounted.
 */
const LANDING_PAGES = [
  "app/(app)/page.tsx",
  "app/(app)/basel/page.tsx",
  "app/(app)/liquidity/page.tsx",
  "app/(app)/credit/page.tsx",
  "app/(app)/forecasting/page.tsx",
  "app/(app)/fx/page.tsx",
  "app/(app)/ftp/page.tsx",
  "app/(app)/markets/page.tsx",
  "app/(app)/risk/page.tsx",
];

// --- 1. the strip is mounted, and mounted connected -------------------------

test("every landing page mounts the connected insight strip", () => {
  for (const page of LANDING_PAGES) {
    const source = code(page);
    assert.ok(
      /import\s*\{[^}]*\bLandingInsightStrip\b[^}]*\}\s*from\s*["']@\/components\/bi\/InsightStrip["']/.test(
        source,
      ),
      `${page} must import LandingInsightStrip from components/bi/InsightStrip`,
    );
    assert.ok(
      /<LandingInsightStrip\b[^>]*\bbankId=\{/.test(source) &&
        /<LandingInsightStrip\b[^>]*\basOf=\{/.test(source),
      `${page} must render <LandingInsightStrip> with the institution and the page's reporting date`,
    );
    assert.equal(
      /<InsightStrip\b/.test(source),
      false,
      `${page} must mount the CONNECTED strip, which owns the flag and 404 gating; the raw InsightStrip is the insights hub's, which has its own date picker`,
    );
  }
});

test("the connected strip is absent when BI is off, unknown or not served, and refuses as a refusal", () => {
  const strip = code("components/bi/InsightStrip.tsx");
  const start = strip.indexOf("export function LandingInsightStrip");
  const end = strip.indexOf("export function useKpiExplain");
  assert.ok(start >= 0 && end > start, "LandingInsightStrip must be exported before useKpiExplain");
  const fn = strip.slice(start, end);
  assert.ok(/useBiAvailability\(\)/.test(fn), "it must ask the feature flag");
  assert.ok(
    /const enabled = availability\.biEnabled === true;/.test(fn),
    "`undefined` (not yet known) and `false` (off, or the flag read failed) must both be treated as off",
  );
  assert.ok(
    /useBiInsights\(bankId, day, undefined, enabled\)/.test(fn),
    "the flag must gate the FETCH, not just the render — a deployment with BI off must receive no request",
  );
  assert.ok(
    /if \(!enabled \|\| !bankId \|\| day === null\) return null;/.test(fn),
    "off, no institution or no date renders nothing — not a skeleton, not an error",
  );
  assert.ok(
    /if \(isBiUnavailable\(insights\.error\)\) return null;/.test(fn),
    "a 404 is 'not here', and renders nothing",
  );
  assert.equal(
    /isBiAccessDenied/.test(fn),
    false,
    "a 403 is a refusal: it must reach InsightStrip, which renders RestrictedWidget, and never be swallowed here",
  );
  assert.ok(
    /error=\{insights\.error\}/.test(fn),
    "the route's error is handed to the strip as-is",
  );
});

test("the Command Center reaches BI only through the strip, with no second deferred entry", () => {
  const home = code("app/(app)/page.tsx");
  const biImports = [
    ...home.matchAll(/from\s+["']@\/components\/bi\/([^"']+)["']/g),
  ].map((match) => match[1]);
  assert.deepEqual(
    biImports,
    ["InsightStrip"],
    "scripts/assert-home-route-bundle.mjs protects this route's bundle; every BI import here must be justified against it",
  );
  const strip = code("components/bi/InsightStrip.tsx");
  assert.equal(
    /next\/dynamic/.test(strip),
    false,
    "the home route may carry exactly ONE deferred entry (the ratio chart) and the bundle guard counts them; a dynamic() in the strip would be a second",
  );
  assert.equal(
    /EChart/.test(strip),
    false,
    "the strip draws with the SVG Sparkline, never the charting runtime",
  );
});

// --- 2. KpiStat.explain opens ExplainDrawer ----------------------------------

test("KpiStat gains an optional explain prop and is unchanged without it", () => {
  const kpi = code("components/ui/KpiStat.tsx");
  assert.ok(
    /explain\?: \(\) => void;/.test(kpi),
    "the prop is optional: most KPIs in the app are not BI measures",
  );
  assert.ok(
    /\{explain \? \(/.test(kpi) && /onClick=\{explain\}/.test(kpi),
    "the affordance renders only when the prop is set, and it calls the prop",
  );
  assert.ok(
    kpi.includes("aria-label={KPI_EXPLAIN_LABEL}") &&
      kpi.includes("title={KPI_EXPLAIN_LABEL}"),
    "the button carries the production label as its accessible name",
  );
  assert.equal(KPI_EXPLAIN_LABEL, "Where this figure came from");
  assert.equal(
    /@\/lib\/api\/bi|@\/components\/bi\//.test(kpi),
    false,
    "KpiStat stays a ui component: no BI client, no BI component — that is what keeps it importable by this Node test",
  );
});

test("useKpiExplain opens ExplainDrawer for the live copy, at the page's date, or offers nothing", () => {
  const strip = code("components/bi/InsightStrip.tsx");
  const start = strip.indexOf("export function useKpiExplain");
  assert.ok(start >= 0, "useKpiExplain must be exported from components/bi/InsightStrip");
  const hook = strip.slice(start);
  assert.ok(
    /import ExplainDrawer from "\.\/ExplainDrawer";/.test(strip),
    "the hook renders the drawer the spec names",
  );
  assert.ok(/<ExplainDrawer/.test(hook), "…and renders it");
  assert.ok(
    /measure=\{explaining\?\.measure \?\? null\}/.test(hook) &&
      /query=\{explaining\?\.query \?\? null\}/.test(hook),
    "the drawer is given the measure AND the query it was read in — provenance is a property of a figure in context",
  );
  assert.ok(
    /const enabled = availability\.biEnabled === true;/.test(hook) &&
      /useBiCatalogue\(bankId, enabled\)/.test(hook),
    "the catalogue is read only when BI is on",
  );
  assert.ok(
    /if \(!enabled \|\| !bankId \|\| day === null \|\| !measures\) return undefined;/.test(
      hook,
    ),
    "off, no institution, no date or no catalogue → no affordance",
  );
  assert.ok(
    /liveEngineMeasureId\(measures, metricId, capitalRegime\)/.test(hook) &&
      /if \(measure === null\) return undefined;/.test(hook),
    "a metric with no honest live copy for this institution gets no button",
  );
  assert.ok(
    /kpiExplainQuery\(measure, day\)/.test(hook),
    "the drawer asks about the page's reporting date, not today",
  );
  assert.ok(
    /institutionType\?\.detail\?\.capitalRegime \?\? null/.test(hook),
    "the institution's regime comes from the bank payload, never a literal",
  );
});

/**
 * Each module landing page and the registry metric ids its KPIs are copies of.
 * A page listed here must open provenance for EVERY id listed and render the
 * drawer once. Not every KPI qualifies: "LCR headroom" is a derived figure,
 * FX's "Tier 1 capital" is the capital module's, and a SAVED forecast run's
 * KPIs are a different object from the live copy BI holds.
 */
const EXPLAIN_PAGES: Record<string, readonly string[]> = {
  basel: ["car_pct", "tier1_ratio_pct", "cet1_ratio_pct", "leverage_ratio_pct"],
  liquidity: [
    "lcr_pct",
    "hqla_total_ghs",
    "net_outflows_30d_ghs",
    "nsfr_pct",
    "asf_total_ghs",
    "rsf_total_ghs",
  ],
  credit: [
    "gross_loans_ghs",
    "npl_ratio_pct",
    "npl_exposure_ghs",
    "total_provision_required_ghs",
    "provision_coverage_pct",
    "par_30_pct",
    "par_90_pct",
  ],
  fx: ["nop_pct_tier1", "single_ccy_max_pct", "nop_ghs"],
  ftp: [
    "portfolio_nim_pct",
    "weighted_asset_yield_pct",
    "weighted_funding_credit_pct",
  ],
  forecasting: ["year5_car_pct", "year5_lcr_pct", "year5_nsfr_pct", "avg_roe_pct"],
};

test("each module KPI that is an engine copy opens its provenance, and the page renders the drawer", () => {
  for (const [page, metrics] of Object.entries(EXPLAIN_PAGES)) {
    const path = `app/(app)/${page}/page.tsx`;
    const source = code(path);
    assert.ok(
      /import\s*\{[^}]*\buseKpiExplain\b[^}]*\}\s*from\s*["']@\/components\/bi\/InsightStrip["']/.test(
        source,
      ) && /const explain = useKpiExplain\(bankId, asOf\);/.test(source),
      `${path} must call useKpiExplain(bankId, asOf)`,
    );
    for (const metric of metrics) {
      assert.ok(
        source.includes(`explain={explain.explainFor("${metric}")}`) ||
          source.includes(`explain={explain.explainFor('${metric}')}`),
        `${path} must open provenance for ${metric}`,
      );
    }
    assert.ok(
      /\{explain\.drawer\}/.test(source),
      `${path} must render the drawer — a button with nothing to open is the inert-feature defect again`,
    );
  }
});

test("every metric id a page names is a registry metric the catalogue copies", () => {
  const engine = join(
    repoRoot(),
    "backend",
    "app",
    "domain",
    "bi",
    "catalogue",
    "engine.py",
  );
  assert.ok(existsSync(engine), `catalogue source not found: ${engine}`);
  const source = readFileSync(engine, "utf8");
  const start = source.indexOf("ENGINE_LABELS");
  const end = source.indexOf("TIER_LABELS");
  assert.ok(start >= 0 && end > start, "ENGINE_LABELS must precede TIER_LABELS in engine.py");
  const known = new Set(
    [...source.slice(start, end).matchAll(/^\s*"([a-z0-9_]+)":/gm)].map(
      (match) => match[1],
    ),
  );
  assert.ok(known.size > 50, `read only ${known.size} registry labels`);
  for (const [page, metrics] of Object.entries(EXPLAIN_PAGES)) {
    for (const metric of metrics) {
      assert.ok(
        known.has(metric),
        `${page} names ${metric}, which is not a registry metric the catalogue copies — a typo here is a KPI that silently never gets its button`,
      );
    }
  }
});

test("liveEngineMeasureId resolves one live copy, the institution's own regime among several, and refuses the rest", () => {
  const catalogue: readonly EngineMeasureLike[] = [
    {
      id: "engine.car_pct.crd.live",
      measureKind: "certified_engine",
      engineMetricId: "car_pct",
      engineTier: "live",
    },
    {
      id: "engine.car_pct.s29.live",
      measureKind: "certified_engine",
      engineMetricId: "car_pct",
      engineTier: "live",
    },
    {
      id: "engine.car_pct.crd.official",
      measureKind: "certified_engine",
      engineMetricId: "car_pct",
      engineTier: "official",
    },
    {
      id: "engine.ecl_total_ghs.ifrs9.live",
      measureKind: "certified_engine",
      engineMetricId: "ecl_total_ghs",
      engineTier: "live",
    },
    {
      id: "crd.npl_ratio",
      measureKind: "portfolio",
      engineMetricId: null,
      engineTier: null,
    },
  ];
  // One live copy under a class-neutral regime applies to both classes.
  assert.equal(
    liveEngineMeasureId(catalogue, "ecl_total_ghs", "s29"),
    "engine.ecl_total_ghs.ifrs9.live",
  );
  // Two live copies: the institution's own capital regime decides.
  assert.equal(
    liveEngineMeasureId(catalogue, "car_pct", "crd"),
    "engine.car_pct.crd.live",
  );
  assert.equal(
    liveEngineMeasureId(catalogue, "car_pct", "s29"),
    "engine.car_pct.s29.live",
  );
  // Two live copies and no regime to choose by: refuse, never guess.
  assert.equal(liveEngineMeasureId(catalogue, "car_pct", null), null);
  assert.equal(liveEngineMeasureId(catalogue, "car_pct", undefined), null);
  // Two live copies, neither under the institution's regime: refuse.
  assert.equal(liveEngineMeasureId(catalogue, "car_pct", "lmtd"), null);
  // A portfolio measure is not an engine copy, whatever its id looks like.
  assert.equal(liveEngineMeasureId(catalogue, "npl_ratio", "crd"), null);
  // No copy at all, or no catalogue yet.
  assert.equal(liveEngineMeasureId(catalogue, "lcr_pct", "crd"), null);
  assert.equal(liveEngineMeasureId(undefined, "car_pct", "crd"), null);
  // The OFFICIAL tier is never offered under a live KPI, even when it is the only copy.
  assert.equal(liveEngineMeasureId([catalogue[2]], "car_pct", "crd"), null);
});

test("the explain query is one measure at the page's date and nothing else", () => {
  assert.deepEqual(kpiExplainQuery("engine.car_pct.crd.live", "2026-06-30"), {
    measures: ["engine.car_pct.crd.live"],
    time: { asOf: "2026-06-30" },
  });
});

// --- 3. TrustBadge in the ChartFrame actions slot ---------------------------

test("ChartFrame reserves the first place in its actions row for the trust badge, and is unchanged without it", () => {
  const frame = code("components/ui/ChartFrame.tsx");
  assert.ok(/trust\?: ReactNode;/.test(frame), "the slot is an optional prop");
  assert.ok(
    /\{\(trust \|\| actions\) && \(/.test(frame),
    "the row renders when EITHER is given, so a page with no actions still shows the badge and a page with neither renders as before",
  );
  assert.ok(
    /\{trust\}\s*\{actions\}/.test(frame),
    "the badge renders before the page's own actions — its own prop, so a page that fills the slot cannot drop it",
  );
});

test("ReconciliationTrustBadge draws the server's verdict, and nothing where there is none", () => {
  const badge = code("components/bi/TrustBadge.tsx");
  const start = badge.indexOf("export function ReconciliationTrustBadge");
  assert.ok(start >= 0, "ReconciliationTrustBadge must be exported from components/bi/TrustBadge");
  const fn = badge.slice(start);
  assert.ok(
    /const enabled = availability\.biEnabled === true;/.test(fn) &&
      /useBiTrust\(bankId, day, enabled\)/.test(fn),
    "the flag gates the fetch",
  );
  assert.ok(
    /if \(!enabled \|\| !bankId \|\| day === null\) return null;/.test(fn),
    "off, no institution or no date → nothing",
  );
  assert.ok(
    /if \(trust\.isPending \|\| trust\.error \|\| !trust\.data\) return null;/.test(fn),
    "pending, refused (403), not served (404) or failed → nothing; a placeholder verdict is a verdict",
  );
  assert.ok(
    /status=\{trust\.data\.status\}/.test(fn),
    "the status drawn is the server's roll-up",
  );
  assert.equal(
    /status=["']/.test(fn),
    false,
    "never a literal verdict",
  );
  assert.ok(
    /\.filter\(\(check\) => check\.status !== "green"\)/.test(fn),
    "the checks that did not pass are named on hover — grey (not assessed) included",
  );
  // The presentational badge itself must still refuse to widen an unknown value.
  assert.ok(
    /status === "green" \|\| status === "amber" \|\| status === "red"/.test(badge) &&
      /return PRESENTATION\.grey;/.test(badge),
    "an unrecognised or absent verdict degrades to grey, never to a pass",
  );
});

/**
 * Which charts on which page carry the badge — and which deliberately do not.
 * The badge belongs on a chart whose figures ARE the institution's computed
 * position at the page's reporting date, because that is what the
 * reconciliation checks compare. A projection path is not that position, and
 * the transfer curve is market inputs plus parameters; a badge on either would
 * assert a comparison nobody made. Every `<ChartFrame>` on these pages must be
 * in one list or the other, so a new chart is classified rather than defaulted.
 */
const BADGED_CHARTS: Record<string, readonly string[]> = {
  basel: ["CAR — reporting-period trend", "Capital waterfall"],
  liquidity: ["LCR & NSFR — reporting-period trend", "Net-outflow decomposition"],
  fx: ["Net position by currency"],
  ftp: ["Portfolio NIM trend"],
  forecasting: [],
};
const UNBADGED_CHARTS: Record<string, readonly string[]> = {
  basel: [],
  liquidity: [],
  fx: [],
  ftp: ["Transfer curve composition"],
  forecasting: [
    "Balance-sheet projection",
    "Asset composition",
    "Asset waterfall",
    "CAR path",
    "LCR path",
    "NSFR path",
  ],
};

test("every module chart of the computed position carries the reconciliation badge, and no other chart does", () => {
  const TRUST = "trust={<ReconciliationTrustBadge bankId={bankId} asOf={asOf} />}";
  for (const page of Object.keys(BADGED_CHARTS)) {
    const path = `app/(app)/${page}/page.tsx`;
    const source = code(path);
    const frames = chartFrames(source);
    const badged = BADGED_CHARTS[page];
    const unbadged = UNBADGED_CHARTS[page];
    assert.equal(
      frames.length,
      badged.length + unbadged.length,
      `${path} has ${frames.length} <ChartFrame> blocks; ${badged.length + unbadged.length} are classified above — classify the new one`,
    );
    if (badged.length > 0) {
      assert.ok(
        /import \{ ReconciliationTrustBadge \} from ["']@\/components\/bi\/TrustBadge["']/.test(
          source,
        ),
        `${path} must import ReconciliationTrustBadge`,
      );
    }
    for (const title of badged) {
      const frame = frames.find((block) => block.includes(`title="${title}"`));
      assert.ok(frame, `${path}: no <ChartFrame title="${title}">`);
      assert.ok(
        frame.includes(TRUST),
        `${path}: the "${title}" chart must carry ${TRUST} in its trust slot`,
      );
    }
    for (const title of unbadged) {
      const frame = frames.find((block) => block.includes(`title="${title}"`));
      assert.ok(frame, `${path}: no <ChartFrame title="${title}">`);
      assert.equal(
        /\btrust=/.test(frame),
        false,
        `${path}: the "${title}" chart is not a computed position and must not carry a reconciliation verdict`,
      );
    }
  }
});

// --- the badge's hover names checks in words, and the same words as the server --

test("the trust badge's hover names each outstanding check in the platform's words, never as a wire token", () => {
  const badge = code("components/bi/TrustBadge.tsx");
  assert.equal(
    /failingChecks\.join\(/.test(badge),
    false,
    "the raw check ids must never be joined into the tooltip (audit A360-6)",
  );
  assert.ok(
    /const failing = outstandingChecksSentence\(failingChecks\);/.test(badge),
    "the tooltip goes through outstandingChecksSentence",
  );
  const start = badge.indexOf("export function outstandingChecksSentence");
  assert.ok(start >= 0, "outstandingChecksSentence must be exported");
  const fn = badge.slice(start, badge.indexOf("export default function TrustBadge"));
  assert.ok(
    /RECONCILIATION_CHECK_LABELS\[id\]/.test(fn),
    "each id is looked up in the label map",
  );
  assert.equal(
    /\$\{id\}/.test(fn),
    false,
    "an id this client cannot name is counted, never printed",
  );
  assert.ok(
    /further checks? this client cannot name/.test(fn),
    "an unnamed check is still reported as outstanding — dropping it would read as fewer failures",
  );
});

test("the check-label map mirrors the server's CHECK_LABELS exactly and covers every check the platform stores", () => {
  const badge = code("components/bi/TrustBadge.tsx");
  const mapStart = badge.indexOf("export const RECONCILIATION_CHECK_LABELS");
  assert.ok(mapStart >= 0, "RECONCILIATION_CHECK_LABELS must be exported");
  const mapBody = badge.slice(mapStart, badge.indexOf("};", mapStart));
  const mirrored = new Map(
    [...mapBody.matchAll(/^\s*(R\d+):\s*"([^"]+)",?$/gm)].map((match) => [
      match[1],
      match[2],
    ]),
  );

  const readBi = readFileSync(
    join(repoRoot(), "backend", "app", "features", "read_bi.py"),
    "utf8",
  );
  const labelsStart = readBi.indexOf("CHECK_LABELS: Mapping[str, str] = {");
  assert.ok(labelsStart >= 0, "could not find CHECK_LABELS in read_bi.py");
  const labelsBody = readBi.slice(labelsStart, readBi.indexOf("}", labelsStart));
  const server = new Map(
    [...labelsBody.matchAll(/^\s*"(R\d+)":\s*"([^"]+)",?$/gm)].map((match) => [
      match[1],
      match[2],
    ]),
  );
  assert.ok(server.size >= 12, `read only ${server.size} server labels`);
  assert.deepEqual(
    [...mirrored.entries()].sort(),
    [...server.entries()].sort(),
    "TrustBadge.RECONCILIATION_CHECK_LABELS must equal read_bi.py CHECK_LABELS, id for id and word for word: the hover on a chart and the list on the Insights hub describe one check with one sentence",
  );

  // …and the server's own list is complete against the ids the platform stores,
  // so a list of ten cannot hide behind a mirror that matches it.
  const models = readFileSync(
    join(repoRoot(), "backend", "app", "models", "bi.py"),
    "utf8",
  );
  const range =
    /RECONCILIATION_CHECK_IDS: tuple\[str, \.\.\.\] = tuple\(f"R\{number\}" for number in range\(1, (\d+)\)\)/.exec(
      models,
    );
  assert.ok(range, "could not read RECONCILIATION_CHECK_IDS from app/models/bi.py");
  const stored = Array.from({ length: Number(range[1]) - 1 }, (_, index) => `R${index + 1}`);
  assert.ok(stored.includes("R12"), "R11 and R12 are stored checks (Phase 5)");
  for (const id of stored) {
    assert.ok(mirrored.has(id), `${id} is a stored check with no sentence in TrustBadge`);
  }
});

// --- a refusal on a module page is the SERVER's decision --------------------

test("the strip paraphrases a grant denial and renders every other refusal in the server's sentence", () => {
  const strip = code("components/bi/InsightStrip.tsx");
  const start = strip.indexOf("export default function InsightStrip");
  const end = strip.indexOf("export function LandingInsightStrip");
  const fn = strip.slice(start, end);
  const denial = fn.indexOf("isBiAccessDenied(error)");
  const refusal = fn.indexOf("biRefusalSentence(error)");
  const failure = fn.indexOf("<ErrorPanel");
  assert.ok(denial >= 0, "a grant denial is paraphrased by RestrictedWidget");
  assert.ok(
    refusal > denial && /<RefusedWidget sentence=\{refusal\}/.test(fn),
    "every other 403 is rendered verbatim through RefusedWidget — an impersonated staff session is not something an organization owner can grant",
  );
  assert.ok(
    failure > refusal,
    "the ordinary failure panel comes last: a refusal is not a fault",
  );
});

// --- 4. one reporting date per page ----------------------------------------

test("each page's BI surfaces speak about the page's own reporting date, never today", () => {
  for (const page of ["basel", "liquidity", "credit", "fx", "ftp"]) {
    const source = code(`app/(app)/${page}/page.tsx`);
    assert.ok(
      /const asOf = data\??\.period\.periodEnd( \?\? null)?;/.test(source),
      `${page}: asOf must be the dashboard payload's reporting period end`,
    );
  }
  for (const page of ["forecasting", "markets", "risk"]) {
    const source = code(`app/(app)/${page}/page.tsx`);
    assert.ok(
      /asOf=\{period\?\.periodEnd\}/.test(source),
      `${page}: asOf must be the selected reporting period's end`,
    );
  }
  assert.ok(
    /asOf=\{effective\.period\.periodEnd\}/.test(code("app/(app)/page.tsx")),
    "the Command Center speaks about the EFFECTIVE period — the one every panel on it reads",
  );
  for (const page of LANDING_PAGES) {
    assert.equal(
      /new Date\(\)[^;]*LandingInsightStrip|LandingInsightStrip[^>]*new Date\(\)/.test(
        code(page),
      ),
      false,
      `${page}: today's date is not a reporting date`,
    );
  }
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}

console.log(
  "landingSurfaces.test.ts: the insight strip, the KPI provenance drawer and the " +
    "chart trust badge are mounted on the Command Center and every module landing " +
    "page, gated on the feature flag, silent when not served, refusing as a refusal, " +
    "and speaking about the page's own reporting date.",
);
