/**
 * Static guard against the fail-open patterns the 2026-08-20 platform audit
 * found in the regulatory UI (§1.3, P0-19 … P0-23).
 *
 * There is no component-test harness in this app, so this scans the SOURCE of
 * the board-facing surfaces for the constructs that caused the findings. It is
 * deliberately narrow: each rule pins one concrete defect, names it, and says
 * what to do instead — so a future edit that reintroduces the shape fails here
 * rather than in front of a preparer.
 *
 * Run: `pnpm --filter @aequoros/dashboard test`
 */

import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';

/**
 * The dashboard package root. Resolved by walking up from this module rather
 * than from `__dirname` directly, because the suite runs from the compiled
 * `.test-out/` tree, and from `process.cwd()` only when invoked via the script.
 */
function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const manifest = join(dir, 'package.json');
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, 'utf8')).name as string;
      if (name === '@aequoros/dashboard') return dir;
    }
    dir = dirname(dir);
  }
  throw new Error('could not locate the @aequoros/dashboard package root');
}

const ROOT = dashboardRoot();

/**
 * The board-facing regulatory surfaces this guard covers.
 *
 * `app/(app)/` was added after WS-H demonstrated the gap empirically: it
 * reintroduced `threshold={num(coupling?.car_min_pct ?? '0')}` into
 * `app/(app)/reports/stress-board-pack/page.tsx` and this guard still reported
 * "35 regulatory UI files clean". That page had FOUR fail-open sites — two
 * chart thresholds and two KPI tiles that lit GREEN against a fabricated 0%
 * floor on a printed, signed board pack.
 *
 * The lesson generalises: the route files under `app/` compose the components
 * and pass the thresholds, so a guard that scans only `components/` watches the
 * half that renders and not the half that decides.
 *
 * WS-T widened it a second time, for the mirror-image reason. The scan covered
 * three module component trees out of the six the product runs, so
 * `components/ftp/businessLines.ts` — which computes a business-line margin the
 * backend does not publish, and returned a fabricated `0%` for a line with no
 * balance — sat outside it for the whole remediation programme. Every component
 * tree that renders a regulatory or financial figure is now in scope: the six
 * risk modules (liquidity, basel, irr, fx, ftp, forecasting), the SDI regime
 * views, the stress/workbench surfaces, and the composed board-facing pages
 * (home, risk, alerts, reports, submissions, live). A tree is left out only
 * when it renders no figure at all — `ui`, `shell`, `tour`, `profile`,
 * `settings`, `impersonation`, and the ingestion/connection consoles.
 */
const SCANNED_DIRS = [
  'components/stress',
  'components/liquidity',
  'components/basel',
  'components/credit',
  'components/irr',
  'components/fx',
  'components/ftp',
  'components/forecasting',
  'components/sdi',
  'components/workbench',
  'components/risk',
  'components/reports',
  'components/alerts',
  'components/home',
  'components/live',
  'components/submissions',
  'components/behavioral',
  'components/charts',
  'components/institution',
  'components/markets',
  'components/positions',
  'app/(app)',
];
const SCANNED_FILES: string[] = [];

type Rule = {
  id: string;
  pattern: RegExp;
  message: string;
  /**
   * When set, a match is accepted if the surrounding lines explicitly test the
   * same field for null — i.e. the caller already renders the absence and only
   * reaches `num()` on the non-null branch.
   */
  acceptExplicitNullGuard?: boolean;
  /** Paths (relative to the dashboard root) exempted, each with a reason. */
  allow?: Record<string, string>;
};

const RULES: Rule[] = [
  {
    id: 'P0-21 hardcoded regulatory floor',
    pattern: /^\s*(?:export\s+)?const\s+[A-Z][A-Z0-9_]*(?:FLOOR|MINIMUM|MIN_PCT|TARGET_PCT)[A-Z0-9_]*\s*=\s*-?\d/m,
    message:
      'A regulatory threshold is hardcoded as a module constant. Read the floor from the run/summary payload instead — an SDI s.29 floor is 10%, a universal bank 13%, and display code cannot know which tenant it is rendering.',
  },
  {
    // TWO SPELLINGS, ONE DEFECT (NEW-51). This rule originally matched only the
    // snake_case field names as they arrive on the wire — `car_min_pct`,
    // `lcr_min_pct` — which is how the raw payload reads. But most of this app
    // consumes the GENERATED client, and `@aequoros/risk-service-api` maps every
    // field to camelCase: `json["car_min_pct"] → carMinPct`. The whole camelCase
    // surface was therefore unpoliced, and it is the larger of the two: the
    // Basel overview and the capital planner defaulted
    //     num(data?.buffers.carMinPct ?? '10')
    //     num(data?.buffers.carEarlyWarningPct ?? '10.5')
    //     num(data?.buffers.carCriticalPct ?? '9')
    // for the whole remediation programme while this guard reported the
    // regulatory UI clean. The literals were not merely fabricated but wrong:
    // Ghana's minimum is CRD ¶71's 10% plus the ¶75 conservation buffer — 13%
    // today, and moved four times since 2020 — so a `10` understates the bar the
    // bank is judged against, and `10.5`/`9` match no published instrument.
    // The camelCase half also covers the shape with no `_pct` suffix at all
    // (`tier1Min ?? 8`, `cet1Min ?? 6.5`), which is how an "assumed minimum"
    // gets written down. Keep BOTH halves whenever this rule is edited.
    id: 'P0-19 zero-on-absence floor fallback',
    pattern:
      /(?:_min_pct|_floor|_target_pct|_critical_pct|_early_warning_pct|_limit_pct|Min|Minimum|Floor|Threshold|Limit|Target|Critical|Warning)(?:Pct|Ratio|Bps)?\s*(?:\?\?|\|\|)\s*['"`]?-?\d/,
    message:
      "A missing regulatory floor is being defaulted to a number (e.g. `?? '0'`, `?? '10'`, `?? 8`). Every ratio clears a 0% floor and a written-down floor is judged against a bar nobody set, so this renders breaches as compliant — or compliance as a breach. Both spellings are covered: the wire's `car_min_pct` and the generated client's `carMinPct`. Use `numOrNull` and `assessAgainstFloor` — an absent floor must render as \"not assessed\", never a substituted ladder.",
  },
  {
    id: 'P0-23 num() applied to a nullable regulatory figure',
    pattern:
      /\bnum\(\s*[A-Za-z0-9_.?!\[\]]*\b(?:stressed_lcr_pct|baseline_lcr_pct|cet1_ratio_pct|car_min_pct|lcr_min_pct|car_target_pct|cumulative_mismatch_ghs|pct_total_deposits|top_five_pct|value_pct|car_pct)\b/g,
    message:
      '`num()` maps null to 0, and this field is nullable — a missing ratio would plot and compare as a real 0%. Use `numOrNull`, or test the field for null on the same expression and render the absence.',
    acceptExplicitNullGuard: true,
  },
  {
    id: 'P0-21 hardcoded floor in a caption',
    pattern: /\bfloor \d+(?:\.\d+)?%/,
    message:
      'A column or chart caption states a numeric floor literally. Derive the caption from the floor on the payload (`fmtFloorPct`) so it cannot disagree with the threshold actually applied.',
  },
  {
    // NEW-53. A FLOOR HAS ONE AUTHORITY, AND IT IS NOT A STORED RUN.
    //
    // `runMetricThreshold(run, 'tier1_ratio_pct')` reads the threshold that was
    // snapshotted into a RegulatoryRun when that run executed. It is evidence
    // about a filing, not the requirement in force now — and it is `null` for
    // every bank before its first official run, which is not the same fact as
    // "this institution has no floor". `/basel` read it for Tier 1, CET1 and
    // leverage and consequently showed, on ONE screen: a green Tier 1 KPI edge
    // (live engine, classified against the governed 8%), "This run carries no
    // Tier 1 minimum · NOT ASSESSED" (this helper, with `latest_run_id` null),
    // and "PASS — at or above the 8% regulatory minimum" (the same governed
    // floor again). Three panels, three answers, one ratio.
    //
    // The current floor comes from the governed parameter set on the module
    // payload — `buffers.tier1MinPct` / `carMinPct`, the SDI s.29 summary, the
    // run's §59(f) coupling — compared with `assessAgainstFloor`. A surface
    // that genuinely wants to state what a PAST run applied (a run-detail or
    // audit view) is a legitimate use: add it to this rule's `allow` map with
    // that reason, so the exception is recorded rather than assumed.
    id: 'NEW-53 stored-run threshold used as the current floor',
    // Calls only — the lookbehind lets the helper's own definition (and its
    // docstring, which names this trap) stay in `components/liquidity/runData.ts`.
    pattern: /(?<!function\s)\brunMetricThreshold\s*\(/,
    message:
      "A stored run's `threshold_min` is being read as a regulatory floor. It records what was applied WHEN THAT RUN RAN and is absent entirely before an institution's first official run — so it renders a live ratio as \"not assessed\" beside a KPI and a validation that both used the real, governed floor. Read the floor from the governed parameter set on the payload (`buffers.*MinPct`, the SDI s.29 summary, the run's §59(f) coupling) and compare with `assessAgainstFloor`.",
  },
  {
    // The forensic calculation-architecture audit's §5 divergence register,
    // row "FTP business-line margin — presentational shadow calculation". The
    // shipped defect was
    //     line.weightedMarginPct =
    //       line.balanceGhs > 0 ? (line.contributionGhs / line.balanceGhs) * 100 : 0;
    // — a client-computed margin whose divide-guard returned a REAL, and
    // unusually good, 0%. It survived every earlier sweep because
    // `components/ftp/` was outside the scan.
    id: '§5 client-side ratio with a fabricated zero',
    pattern:
      /(?:Pct|Ratio|Margin|Nim|Coverage|Headroom)\b\s*[:=]\s*[^;]{0,240}\?[^;]{0,240}\/[^;]{0,120}:\s*-?\d/,
    message:
      'A ratio computed in the browser falls back to a bare number when its denominator is unusable. A guarded division that yields 0 renders a fabricated measurement — indistinguishable on screen from a real one, and it compares below every floor and above every zeroed floor. Widen the field to `number | null`, return null, and render the absence ("not computable"). If the figure is a view aggregate the backend does not publish, say so on screen too — it is presentational, not an alternate authority.',
  },
];

type Hit = { index: number; text: string };

function matches(source: string, pattern: RegExp): Hit[] {
  const hits: Hit[] = [];
  if (!pattern.global) {
    const single = source.match(pattern);
    if (single && single.index !== undefined) {
      hits.push({ index: single.index, text: single[0] });
    }
    return hits;
  }
  const re = new RegExp(pattern.source, pattern.flags);
  let m: RegExpExecArray | null = re.exec(source);
  while (m !== null) {
    hits.push({ index: m.index, text: m[0] });
    m = re.exec(source);
  }
  return hits;
}

function lineOf(source: string, index: number): number {
  return source.slice(0, index).split('\n').length;
}

/**
 * True when the field named in the hit is explicitly tested for null within a
 * few lines — the multi-line ternary shape `x === null ? '—' : num(x)`.
 */
function hasNullGuard(lines: string[], source: string, hit: Hit): boolean {
  const field = /\b([a-z0-9_]+)\b\s*\)?\s*$/.exec(hit.text)?.[1];
  if (!field) return false;
  const line = lineOf(source, hit.index) - 1;
  const window = lines.slice(Math.max(0, line - 5), line + 3).join('\n');
  return (
    new RegExp(`${field}\\s*(?:===|!==)\\s*null`).test(window) ||
    new RegExp(`${field}\\s*!==\\s*undefined`).test(window) ||
    new RegExp(`numOrNull\\([^)]*${field}`).test(window)
  );
}

function walk(dir: string, out: string[]): void {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      walk(full, out);
      continue;
    }
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
}

const files: string[] = [];
for (const dir of SCANNED_DIRS) walk(join(ROOT, dir), files);
for (const file of SCANNED_FILES) files.push(join(ROOT, file));

const failures: string[] = [];

for (const file of files) {
  const rel = relative(ROOT, file).split('\\').join('/');
  const source = readFileSync(file, 'utf8');
  // Comments explain the defect by name in several of these files; strip line
  // comments and block comments so prose about the bug is not read as the bug.
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '');
  const lines = code.split('\n');
  for (const rule of RULES) {
    if (rule.allow && rel in rule.allow) continue;
    for (const hit of matches(code, rule.pattern)) {
      if (rule.acceptExplicitNullGuard && hasNullGuard(lines, code, hit)) continue;
      failures.push(
        `${rel}:${lineOf(code, hit.index)} [${rule.id}] matched ${JSON.stringify(hit.text.trim())}\n    → ${rule.message}`
      );
      break;
    }
  }
}

// The guard must actually be looking at something. The floor is raised with
// every widening so a directory silently dropping out of the scan fails here
// rather than quietly shrinking the covered surface.
assert.ok(files.length >= 210, `expected to scan the regulatory UI, found ${files.length} files`);

if (failures.length > 0) {
  for (const failure of failures) console.error(`FAIL ${failure}`);
  console.error(`\n${failures.length} fail-open pattern(s) found in the regulatory UI.`);
  process.exit(1);
}
console.log(`fail-open-guard.test.ts: ${files.length} regulatory UI files clean.`);
