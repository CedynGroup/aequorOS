import assert from "node:assert/strict";
import { runBackend } from "../../../unit/backend";

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
  type Schedule,
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

const cadences = runBackend<string[]>(`
from app.bi.models.notifications import SUBSCRIPTION_CADENCES
print(json.dumps(list(SUBSCRIPTION_CADENCES)))
`);

function scheduleVerdicts(
  cases: { cadence: string; schedule: Schedule }[],
): { schema: boolean; database: boolean }[] {
  return runBackend(
    `
import sqlite3
from sqlalchemy import CheckConstraint
from app.bi.models.notifications import BiSubscription
from app.bi.schemas.notifications import BiSubscriptionUpsert

names = {"ck_bi_subscriptions_cadence", "ck_bi_subscriptions_hour",
    "ck_bi_subscriptions_minute", "ck_bi_subscriptions_day_of_week",
    "ck_bi_subscriptions_day_of_month", "ck_bi_subscriptions_schedule_matches_cadence"}
checks = [str(c.sqltext) for c in BiSubscription.__table__.constraints
    if isinstance(c, CheckConstraint) and c.name in names]
assert len(checks) == len(names)
results = []
with sqlite3.connect(":memory:") as db:
    for case in payload:
        schedule = case["schedule"]
        values = {"cadence": case["cadence"], "hour": schedule["hour"],
            "minute": schedule["minute"], "day_of_week": schedule["dayOfWeek"],
            "day_of_month": schedule["dayOfMonth"]}
        columns = ", ".join(f":{name} AS {name}" for name in values)
        database = all(db.execute(f"SELECT ({check}) IS NOT FALSE FROM (SELECT {columns})",
            values).fetchone()[0] for check in checks)
        try:
            BiSubscriptionUpsert.model_validate({
                **values, "name": "Treasury report", "artifact_format": "csv",
                "query": {"measures": ["loans.balance_rc"], "time": {"as_of": "2026-06-30"}},
                "reason": "Schedule parity", "recipient_emails": ["reader@example.com"]})
        except ValueError:
            schema = False
        else:
            schema = True
        results.append({"schema": schema, "database": database})
print(json.dumps(results))
`,
    cases,
  );
}

test("every stored cadence is offered", () => {
  assert.deepEqual(
    CADENCE_OPTIONS.map((option) => option.value).sort(),
    [...cadences].sort(),
  );
  assert.deepEqual(Object.keys(CADENCE_SHAPES).sort(), [...cadences].sort());
});

test("every schedule shape agrees with the wire validator and database CHECKs", () => {
  const cases = (Object.keys(CADENCE_SHAPES) as Cadence[]).flatMap((cadence) =>
    Array.from({ length: 16 }, (_, mask) => ({
      cadence,
      schedule: {
        hour: mask & 1 ? 7 : null,
        minute: mask & 2 ? 30 : null,
        dayOfWeek: mask & 4 ? 1 : null,
        dayOfMonth: mask & 8 ? 1 : null,
      },
    })),
  );
  const expected = cases.map(({ cadence, schedule }) => {
    const accepted = scheduleIsComplete(cadence, schedule);
    return { schema: accepted, database: accepted };
  });
  assert.deepEqual(scheduleVerdicts(cases), expected);
});

test("the monthly ceiling is enforced without skipping a month", () => {
  const days = [0, 1, DAY_OF_MONTH_MAX, DAY_OF_MONTH_MAX + 1, 31];
  const cases = days.map((dayOfMonth) => ({
    cadence: "monthly",
    schedule: { hour: 7, minute: 30, dayOfWeek: null, dayOfMonth },
  }));
  assert.deepEqual(
    scheduleVerdicts(cases),
    [false, true, true, false, false].map((accepted) => ({
      schema: accepted,
      database: accepted,
    })),
  );
});

test("weekday numbers run Monday through Sunday in both validators", () => {
  assert.deepEqual(
    WEEKDAYS.map((day) => day.value),
    [1, 2, 3, 4, 5, 6, 7],
  );
  assert.equal(WEEKDAYS[0].label, "Monday");
  assert.equal(WEEKDAYS[6].label, "Sunday");
  const cases = Array.from({ length: 9 }, (_, dayOfWeek) => ({
    cadence: "weekly",
    schedule: { hour: 7, minute: 30, dayOfWeek, dayOfMonth: null },
  }));
  assert.deepEqual(
    scheduleVerdicts(cases),
    cases.map(({ schedule }) => {
      const accepted = schedule.dayOfWeek >= 1 && schedule.dayOfWeek <= 7;
      return { schema: accepted, database: accepted };
    }),
  );
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

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("notifications.test.ts: all assertions passed");
