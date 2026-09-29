/**
 * The two combinations a reader will get wrong cannot be built here.
 *
 * A cadence and its schedule, and a threshold basis and its number, are each
 * refused three times on the server — a request validator, a route check and a
 * database CHECK. That is correct, and it is not an interface. A control that can
 * produce a combination the server refuses turns a reader's ordinary mistake into
 * an error message, so both pairings are rebuilt as a whole rather than patched,
 * and the tests below are about the impossibility rather than about the message.
 *
 * The cadence rule is checked against `app/models/bi_notifications.py` itself —
 * the CHECK constraint is parsed out of it — because a client that believed a
 * daily report may carry a weekday would send one the database refuses, and a
 * client that believed a weekly one may not would hide a legal schedule.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import {
  CADENCE_OPTIONS,
  CADENCE_SHAPES,
  DAY_OF_MONTH_MAX,
  WEEKDAYS,
  alertSentence,
  deliveryTone,
  governedLimitAvailable,
  malformedRecipients,
  parseRecipients,
  scheduleFor,
  scheduleIsComplete,
  scheduleSentence,
  thresholdBasisOptions,
  thresholdFor,
  type Cadence,
  type DeliveryStatus,
} from "./notifications";

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

function repoRoot(): string {
  let dir = __dirname;
  for (let index = 0; index < 10; index += 1) {
    if (existsSync(join(dir, "backend", "app"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`could not locate the repository root from ${__dirname}`);
}

const MODEL_SOURCE = readFileSync(
  join(repoRoot(), "backend", "app", "models", "bi_notifications.py"),
  "utf8",
);

// --- the cadence rule is the database's --------------------------------------

test("every cadence the server stores is offered, and no other", () => {
  const match =
    /SUBSCRIPTION_CADENCES: tuple\[str, \.\.\.\] = \(([^)]*)\)/.exec(
      MODEL_SOURCE,
    );
  assert.ok(match, "could not read SUBSCRIPTION_CADENCES from the model");
  const declared = [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  assert.deepEqual(
    CADENCE_OPTIONS.map((option) => option.value).sort(),
    [...declared].sort(),
    "the cadence control and the stored vocabulary disagree",
  );
  assert.deepEqual(Object.keys(CADENCE_SHAPES).sort(), [...declared].sort());
});

test("each cadence's fields are exactly the ones its CHECK constraint requires", () => {
  // The constraint reads, per cadence:
  //   (cadence = 'weekly' AND hour IS NOT NULL AND minute IS NOT NULL
  //    AND day_of_week IS NOT NULL AND day_of_month IS NULL)
  // so for each cadence the fields it REQUIRES are the ones it says IS NOT NULL.
  // The expression is written as adjacent Python string literals, so the quotes
  // and the line breaks between them are removed before it is read — the SQL is
  // the concatenation, not any one literal.
  const span =
    /\(cadence = 'on_new_data'[\s\S]*?ck_bi_subscriptions_schedule_matches_cadence/.exec(
      MODEL_SOURCE,
    );
  assert.ok(span, "could not find the schedule/cadence CHECK constraint");
  const sql = span[0].replace(/"/g, "").replace(/\s+/g, " ");
  for (const cadence of Object.keys(CADENCE_SHAPES) as Cadence[]) {
    const clause = new RegExp(`cadence = '${cadence}'([^)]*)`).exec(sql);
    assert.ok(clause, `the CHECK constraint says nothing about ${cadence}`);
    const required = (field: string): boolean =>
      new RegExp(`${field} IS NOT NULL`).test(clause[1]);
    const shape = CADENCE_SHAPES[cadence];
    assert.equal(required("hour"), shape.clock, `${cadence}: hour`);
    assert.equal(required("minute"), shape.clock, `${cadence}: minute`);
    assert.equal(
      required("day_of_week"),
      shape.dayOfWeek,
      `${cadence}: weekday`,
    );
    assert.equal(
      required("day_of_month"),
      shape.dayOfMonth,
      `${cadence}: day of month`,
    );
  }
});

test("the day-of-month ceiling is the server's, so no month is skipped", () => {
  const match = /day_of_month >= 1 AND day_of_month <= (\d+)/.exec(
    MODEL_SOURCE,
  );
  assert.ok(match, "could not read the day_of_month CHECK from the model");
  assert.equal(DAY_OF_MONTH_MAX, Number(match[1]));
});

test("weekdays are numbered as the server stores them, Monday first", () => {
  const match = /day_of_week >= (\d+) AND day_of_week <= (\d+)/.exec(
    MODEL_SOURCE,
  );
  assert.ok(match, "could not read the day_of_week CHECK from the model");
  assert.equal(WEEKDAYS[0].value, Number(match[1]));
  assert.equal(WEEKDAYS[WEEKDAYS.length - 1].value, Number(match[2]));
  assert.equal(WEEKDAYS[0].label, "Monday");
});

// --- an invalid combination is unbuildable -----------------------------------

test("changing the cadence clears the fields the new cadence does not use", () => {
  const weekly = scheduleFor("weekly", {
    hour: 6,
    minute: 15,
    dayOfWeek: 5,
    dayOfMonth: null,
  });
  assert.equal(weekly.dayOfWeek, 5);

  // Weekly -> daily: the weekday cannot survive.
  const daily = scheduleFor("daily", weekly);
  assert.equal(daily.dayOfWeek, null);
  assert.equal(daily.hour, 6, "the time the reader chose is kept");

  // Daily -> monthly: a day of month appears and the weekday stays absent.
  const monthly = scheduleFor("monthly", daily);
  assert.equal(monthly.dayOfWeek, null);
  assert.equal(monthly.dayOfMonth, 1);

  // Anything -> on_new_data: no clock at all.
  const onNewData = scheduleFor("on_new_data", monthly);
  assert.deepEqual(onNewData, {
    hour: null,
    minute: null,
    dayOfWeek: null,
    dayOfMonth: null,
  });
});

test("every schedule the controls can produce satisfies its cadence", () => {
  // The exhaustive statement of the property: whatever a reader was holding
  // before, rebuilding for a cadence yields a schedule the server accepts.
  const cadences = Object.keys(CADENCE_SHAPES) as Cadence[];
  for (const from of cadences) {
    for (const to of cadences) {
      const built = scheduleFor(to, scheduleFor(from));
      assert.ok(
        scheduleIsComplete(to, built),
        `${from} -> ${to} produced a schedule the server would refuse`,
      );
    }
  }
});

test("midnight is a time and not an absence", () => {
  // `hour: 0` must survive a rebuild: a schedule that treated it as unset would
  // silently move a midnight report to the default hour.
  const built = scheduleFor("daily", {
    hour: 0,
    minute: 0,
    dayOfWeek: null,
    dayOfMonth: null,
  });
  assert.equal(built.hour, 0);
  assert.equal(built.minute, 0);
});

test("a governed limit is not offered for a figure that has none", () => {
  assert.deepEqual(
    thresholdBasisOptions({ thresholdsSource: null }).map((o) => o.value),
    ["stated"],
  );
  assert.equal(governedLimitAvailable({ thresholdsSource: null }), false);
  assert.equal(governedLimitAvailable(null), false);
  assert.equal(governedLimitAvailable(undefined), false);

  const governed = thresholdBasisOptions({ thresholdsSource: "car_min" });
  assert.deepEqual(
    governed.map((o) => o.value),
    ["stated", "governed_limit"],
  );
  assert.equal(governedLimitAvailable({ thresholdsSource: "car_min" }), true);
});

test("choosing the governed basis clears the number the reader had typed", () => {
  assert.deepEqual(thresholdFor("stated", "12.5"), {
    basis: "stated",
    threshold: "12.5",
  });
  assert.deepEqual(thresholdFor("governed_limit", "12.5"), {
    basis: "governed_limit",
    threshold: null,
  });
});

test("no option label or hint on this surface is a wire code", () => {
  const strings = [
    ...CADENCE_OPTIONS.flatMap((o) => [o.label, o.hint]),
    ...WEEKDAYS.map((d) => d.label),
    ...thresholdBasisOptions({ thresholdsSource: "car_min" }).flatMap((o) => [
      o.label,
      o.hint,
    ]),
  ];
  for (const value of strings) {
    assert.ok(value.length > 0);
    assert.ok(
      !/[a-z]+_[a-z]+/.test(value),
      `"${value}" reads like a wire code`,
    );
  }
});

// --- the sentences -----------------------------------------------------------

test("a schedule always states the zone it is read in", () => {
  assert.equal(
    scheduleSentence(
      "weekly",
      { hour: 7, minute: 30, dayOfWeek: 1, dayOfMonth: null },
      "Africa/Accra",
    ),
    "Sent every Monday at 07:30 Africa/Accra.",
  );
  assert.equal(
    scheduleSentence(
      "monthly",
      { hour: 18, minute: 5, dayOfWeek: null, dayOfMonth: 21 },
      "Africa/Lagos",
    ),
    "Sent on the 21st of every month at 18:05 Africa/Lagos.",
  );
  assert.equal(
    scheduleSentence(
      "daily",
      { hour: 0, minute: 0, dayOfWeek: null, dayOfMonth: null },
      "UTC",
    ),
    "Sent every day at 00:00 UTC.",
  );
  const onNewData = scheduleSentence(
    "on_new_data",
    { hour: null, minute: null, dayOfWeek: null, dayOfMonth: null },
    "UTC",
  );
  assert.ok(!onNewData.includes("UTC"), "there is no time to place in a zone");
  assert.ok(onNewData.includes("rebuilt"));
});

test("an alert reads as a sentence before it is saved", () => {
  assert.equal(
    alertSentence("Gross loans", "above", "stated", " 1000000 "),
    "Tell me when Gross loans rises above 1000000.",
  );
  assert.equal(
    alertSentence("Capital adequacy ratio", "below", "governed_limit", ""),
    "Tell me when Capital adequacy ratio falls below the limit governed for it.",
  );
  assert.equal(
    alertSentence(null, "above", "stated", ""),
    "Tell me when the figure you choose rises above a number you set.",
  );
});

// --- a refusal is an outcome, not a failure ----------------------------------

test("a refused delivery does not read as something broken", () => {
  assert.equal(deliveryTone("denied"), "neutral");
  assert.equal(deliveryTone("no_data"), "neutral");
  assert.equal(deliveryTone("sent"), "positive");
  assert.equal(deliveryTone("pending"), "pending");
  // The only outcome somebody has to act on.
  assert.equal(deliveryTone("failed"), "warning");
  const statuses: DeliveryStatus[] = [
    "pending",
    "sent",
    "denied",
    "no_data",
    "failed",
  ];
  const warnings = statuses.filter((s) => deliveryTone(s) === "warning");
  assert.deepEqual(warnings, ["failed"]);
});

// --- recipients --------------------------------------------------------------

test("addresses are normalised the way the server normalises them", () => {
  assert.deepEqual(
    parseRecipients(
      " Treasurer@Example.Test , cfo@example.test\nTREASURER@example.test ",
    ),
    ["treasurer@example.test", "cfo@example.test"],
  );
  assert.deepEqual(parseRecipients(""), []);
  assert.deepEqual(parseRecipients("not-an-address"), []);
});

test("something that cannot be an address at all is named back to the reader", () => {
  assert.deepEqual(malformedRecipients("cfo@example.test, jane"), ["jane"]);
  assert.deepEqual(malformedRecipients("cfo@example.test"), []);
});

// --- the payload states every field the contract carries ----------------------

test("every field the notification contracts carry is one the payload states", () => {
  // THE PIN THAT WAS MISSING. `lib/api/bi.ts` posts alerts and subscriptions
  // through a hand-written transport, so no compiler checks its request shape
  // against the contract — and it omitted `notify_user_ids` and
  // `recipient_user_ids`. The server REPLACES both from the request, where the
  // schema defaults them to an empty list, so every dashboard edit of an alert
  // silently deleted the user recipients an Org Owner had added through the API,
  // and answered 200 (verification auditor V2).
  //
  // The generated serializers are the contract. Read from the package source
  // rather than imported, because this suite runs as plain Node.
  const generated = join(repoRoot(), "packages", "risk-service-api", "src", "models");
  const transport = readFileSync(join(repoRoot(), "backend", "dashboard", "lib", "api", "bi.ts"), "utf8");
  for (const [model, builder] of [
    ["BiAlertUpsert", "alertPayload"],
    ["BiSubscriptionUpsert", "subscriptionPayload"],
  ] as const) {
    const modelPath = join(generated, `${model}.ts`);
    assert.ok(existsSync(modelPath), `generated model not found: ${modelPath}`);
    const body =
      new RegExp(`export function ${model}ToJSONTyped[\\s\\S]*?return \\{([\\s\\S]*?)\\n  \\};`).exec(
        readFileSync(modelPath, "utf8"),
      );
    assert.ok(body, `could not read the generated ${model} serializer`);
    const wireKeys = [...body[1].matchAll(/^\s{4}([a-z_0-9]+):/gm)].map((m) => m[1]);
    assert.ok(wireKeys.length >= 8, `read only ${wireKeys.length} keys from ${model}`);

    const fn = new RegExp(`function ${builder}\\(([\\s\\S]*?)\\n\\}`).exec(transport);
    assert.ok(fn, `could not read ${builder} in lib/api/bi.ts`);
    const missing = wireKeys.filter((key) => !new RegExp(`\\b${key}:`).test(fn[1]));
    assert.deepEqual(
      missing,
      [],
      `${builder} does not state ${missing.join(", ")}. The server replaces what ` +
        `the request omits, so an omitted field DELETES what is stored — which is ` +
        `how every dashboard edit wiped an alert's user recipients.`,
    );
  }
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("notifications.test.ts: all assertions passed");
