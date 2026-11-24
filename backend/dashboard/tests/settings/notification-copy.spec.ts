import { describe, expect, it } from "vitest";
import fc from "fast-check";
import type {
  EmailMode,
  NotificationEventPreferenceRead,
} from "@aequoros/risk-service-api";

import {
  EMAIL_MODES,
  MODE_LABELS,
  deliveryReason,
  orgSettingsProblem,
  preferenceState,
  untilLabel,
} from "@/lib/notifications/copy";

const mode = fc.constantFrom<EmailMode>(...EMAIL_MODES);

const event = fc.record({
  key: fc.constant("filing.review_requested"),
  label: fc.constant("A return is waiting for your review"),
  description: fc.constant("A return reached a stage you can act on."),
  defaultEmail: mode,
  emailMode: mode,
  choice: fc.option(mode, { nil: null }),
  lockedBy: fc.constantFrom<NotificationEventPreferenceRead["lockedBy"]>(
    null,
    "platform",
    "organisation",
  ),
});

describe("preferenceState", () => {
  it("locked events are never editable and always explain why", () => {
    fc.assert(
      fc.property(event, (value) => {
        const state = preferenceState(value);
        if (value.lockedBy !== null) {
          expect(state.editable).toBe(false);
          expect(state.selected).toBe(value.emailMode);
          expect(state.note).toMatch(/Always emailed|Set by your organisation/);
        } else {
          expect(state.editable).toBe(true);
        }
      }),
    );
  });

  it("an editable event shows the person's choice, or the default it follows", () => {
    fc.assert(
      fc.property(event, (value) => {
        fc.pre(value.lockedBy === null);
        const state = preferenceState(value);
        if (value.choice === null) {
          expect(state.followsDefault).toBe(true);
          expect(state.selected).toBe(value.emailMode);
          expect(state.note).toBe(
            `Organisation default: ${MODE_LABELS[value.defaultEmail].toLowerCase()}.`,
          );
        } else {
          expect(state.followsDefault).toBe(false);
          expect(state.selected).toBe(value.choice);
          expect(state.note).toBeNull();
        }
      }),
    );
  });

  it("never shows an API enum to the person", () => {
    fc.assert(
      fc.property(event, (value) => {
        const note = preferenceState(value).note ?? "";
        expect(note).not.toMatch(/\b(immediate|digest|organisation_default)\b/);
      }),
    );
  });
});

describe("deliveryReason", () => {
  it.each([
    ["recipient_inactive", "Person is deactivated"],
    ["no_address", "Person has no email address"],
    ["not_on_allowlist", "Blocked by the test-mode allowlist"],
    ["recipient_lost_access", "Person no longer has access"],
    ["expired", "Expired before it could be sent"],
  ])("translates %s", (code, expected) => {
    expect(deliveryReason(code)).toBe(expected);
  });

  it("summarises relay errors and keeps the server's words", () => {
    expect(
      deliveryReason("SMTPRecipientsRefused: {'a@b.test': (550, b'No such user')}"),
    ).toBe(
      "The mail server refused this address ({'a@b.test': (550, b'No such user')})",
    );
    expect(deliveryReason("SMTPServerDisconnected: ")).toBe("The mail server disconnected");
  });

  it("is empty for a delivery with no error and passes unknown text through", () => {
    expect(deliveryReason(null)).toBeNull();
    expect(deliveryReason("")).toBeNull();
    fc.assert(
      fc.property(fc.string({ minLength: 1 }).filter((s) => !s.includes(": ")), (text) => {
        const known = [
          "recipient_inactive",
          "no_address",
          "not_on_allowlist",
          "recipient_lost_access",
          "expired",
        ];
        fc.pre(!known.includes(text));
        expect(deliveryReason(text)).toBe(text);
      }),
    );
  });
});

describe("orgSettingsProblem", () => {
  it("accepts escalation strictly after the reminder", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 23 }),
        fc.integer({ min: 1, max: 500 }),
        fc.integer({ min: 1, max: 500 }),
        (digestHour, reminder, gap) => {
          expect(
            orgSettingsProblem({
              digestHour,
              reminderAfterHours: reminder,
              escalateAfterHours: reminder + gap,
            }),
          ).toBeNull();
          expect(
            orgSettingsProblem({
              digestHour,
              reminderAfterHours: reminder,
              escalateAfterHours: reminder,
            }),
          ).toBe("Escalation must come after the first reminder.");
        },
      ),
    );
  });

  it("refuses a missing or out-of-range value", () => {
    expect(
      orgSettingsProblem({ digestHour: 24, reminderAfterHours: 1, escalateAfterHours: 2 }),
    ).toMatch(/summary hour/);
    expect(
      orgSettingsProblem({ digestHour: 7, reminderAfterHours: Number.NaN, escalateAfterHours: 2 }),
    ).toMatch(/at least one hour/);
  });
});

describe("untilLabel", () => {
  const now = new Date("2026-11-23T09:00:00Z");
  it.each([
    [0, "now"],
    [1, "in 1 minute"],
    [5, "in 5 minutes"],
    [60, "in 1 hour"],
    [240, "in 4 hours"],
  ])("%i minutes ahead reads %s", (minutes, expected) => {
    expect(untilLabel(new Date(now.getTime() + minutes * 60_000), now)).toBe(expected);
  });
});
