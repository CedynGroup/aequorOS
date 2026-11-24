# Notification platform

AequorOS tells people what needs them, through the in-app feed and branded
email, from one platform every feature uses. This document is the contract.

## Standing rules at a glance

- **Every notice is a catalogue event.** Declare it once in
  `app/notifications/catalog.py`; publish it with `notifications.public.publish`.
  Never hand-write email HTML and never add a second mail path —
  `app/services/mailer.py` is the only transport.
- **Recipients come from authority, never from scalar roles.** A producer
  resolves who may act or see (for filing: `filing_notifications.stage_holders`)
  and names those people. The person who caused the event is excluded.
- **Publish inside the business transaction.** Feed rows, email deliveries and
  the dispatch job commit with the change that caused them; a rolled-back
  action notifies nobody and a mail outage never fails an action.
- **The feed always receives; email is shaped.** Email is decided per person
  and event by the resolution order below. Nothing reaches a mailbox that the
  recipient could not see in their own feed at send time.
- **Scheduled producers pass a `dedupe_key`** so a re-run never repeats a notice.
- **Jurisdiction is data.** Regulator names come from
  `app.policy.public.regulator_name`; email copy never names a country,
  regulator or regulator system.

## Pieces

| Piece | Where | What it does |
| --- | --- | --- |
| Catalogue | `app/notifications/catalog.py` | Every event: key, category, label, description, default email mode, platform lock, legacy type prefixes |
| Message | `app/notifications/rendering.py` | Title, summary, labelled facts, quoted comment, one action; renders feed body, branded HTML and plain text |
| Templates | `app/notifications/templates/` | One branded layout (`base.html`), single notice and daily summary, HTML and text |
| Publishing | `app/notifications/service/publishing.py` | `publish()`: feed rows + email deliveries + dispatch scheduling |
| Preferences | `app/notifications/service/preferences.py` | Email mode resolution; personal choices; organisation policy and settings |
| Dispatcher | `app/notifications/service/dispatch.py` | `notification_dispatch` job: send, retry, dead-letter, suppress, digest |
| Settings API | `app/notifications/api/manage_preferences.py` | `/notifications/preferences`, `/notifications/settings`, `/notifications/deliveries` |
| Filing notices | `app/reporting/service/filing_notifications.py` | Review chain and regulator notices, recipients by authority |
| Regulator sync | `app/reporting/service/submission_sync.py` | `regulatory_submission_sync` job: status of filed returns, regulator queries |
| Escalation | `app/reporting/service/review_reminders.py` | `filing_review_reminders` job: reminder, then escalation, per stage |

Tables (migration `202611230130`): `notifications` gains `event_key`, `data`
and `action_path`; `notification_deliveries` is the outbox and delivery log;
`notification_preferences`, `notification_policies` and `notification_settings`
hold personal choices, organisation rules and organisation settings. All are
tenant tables with forced row-level security.

## Email mode resolution

For one person and one event, first match wins:

1. the catalogue locks the event (`email_locked`, e.g. the regulator sending a
   return back or declining it) → the catalogue default;
2. the organisation switched email off → no email;
3. an administrator locked a policy for the event → that mode;
4. the person chose a mode → that mode;
5. an administrator set an unlocked policy → that mode;
6. the catalogue default.

Modes are `immediate`, `digest` (one summary email at the organisation's
summary hour, in its time zone) and `off`.

## Delivery

`publish` writes one `notification_deliveries` row per recipient who should get
email, due now or at the next summary hour, and schedules
`notification_dispatch` for the earliest due time (pulling an already-queued
run earlier; `job_queue.enqueue`'s coalescing keeps the later time, which is
wrong for sending). The dispatcher:

- claims due rows with `FOR UPDATE SKIP LOCKED`;
- re-checks the recipient at send time — active, has an address, passes
  `NOTIFICATION_EMAIL_ALLOWLIST`, and can still see the notice under the feed's
  visibility rules — otherwise marks the row `suppressed` with the reason;
- sends one email per immediate row and one summary per person for digest rows;
- on a temporary failure keeps the row `pending` with the error and retries
  after 1 min, 5 min, 15 min, 1 h, 4 h, 12 h; a permanent refusal (5xx) or the
  last attempt (`NOTIFICATION_MAX_ATTEMPTS`, default 6) makes it `dead`;
- expires rows untouched for three days;
- schedules its own next run, so retries and summaries need no tick. The hourly
  tick only re-seeds a lost chain (`dispatch.ensure_scheduled`).

Delivery is at-least-once: only a crash between the relay accepting a message
and the commit that follows can repeat it. An administrator can resend a `dead`
or `suppressed` delivery from the delivery log.

With no relay configured (`SMTP_HOST` and `SMTP_FROM` unset) no deliveries are
created at all, so switching email on later never sends a backlog.

## Branded email

One layout, inline styles, table markup for Outlook, a text wordmark and no
remote content: no hosted logo, web font or tracking pixel. Every email is
`multipart/alternative` with a complete plain-text part, carries
`Auto-Submitted: auto-generated`, a per-delivery `Message-ID`, the reason the
person received it and a link to their notification settings. Links point at
`BANK_APP_BASE_URL`.

## Filing and regulator notices

| Moment | Event | Who |
| --- | --- | --- |
| Sent for approval, or handed on to the next stage | `filing.review_requested` | Everyone who may decide that stage now: stage permission at the bank, maker-checker clean, officer title matching |
| A stage approves | `filing.stage_approved` | The preparer and the officers of earlier stages this round |
| A stage sends back | `filing.sent_back` | Officers who held the target stage before (or everyone who may decide it) |
| Waiting past the reminder window | `filing.review_overdue` | Everyone who may decide the stage |
| Waiting past the escalation window | `filing.review_escalated` | Those, the officers who worked on it, and the organisation's owners |
| Filed | `filing.filed` | Everyone who worked on the return |
| Gateway receipt | `regulator.received` | Everyone who worked on it plus everyone who may file it |
| Accepted / sent back / declined | `regulator.accepted` / `regulator.sent_back` / `regulator.declined` | Same |
| Regulator query | `regulator.query` | Same |

The chain hooks live in `filing_workflow/chain.py` (start, decide, hand off,
send back, stage approval by certification); filing and receipt notices live
in `workflow.add_submission_event`, the one place every submission is recorded.

## Regulator sync

`regulatory_submission_sync` checks every submitted return through
`workflow.check_regulator_status` — the same path as the officer's "check
status" button — every minute for the first 15 minutes after filing, then every
5 minutes to an hour, every 15 minutes to a day, then hourly for 14 days. A
poll that only says "still validating" writes nothing to the append-only
submission log (the sandbox simulator's polls excepted: its log is its state).
A decision moves the return and notifies as above. For gateway filings it also
reads the regulator's open queries for 60 days and notifies once per query.

## Escalation

`filing_review_reminders` sends one reminder after the organisation's reminder
window and one escalation after its escalation window, per return, round and
stage. "Waiting since" is the last decision recorded on the return. Windows and
the on/off switch are organisation settings.

## Adding an event

1. Add an `EventDefinition` to `catalog.py` and to `EVENTS`, with a
   description ending in a full stop (the preferences screen shows it).
2. In the producing feature, build a `Message` and call `publish` with the
   recipients resolved from authority, the locator of what the notice is about,
   and a `dedupe_key` if the producer is scheduled.
3. Test who receives it. Preferences, policy, the delivery log, branded email
   and digests need no change.

## Adding a channel

Deliveries carry a `channel` (`email` today). A new channel (Teams, Slack,
webhook) adds a value to the channel CHECK constraint, a delivery-creation rule
in `publishing.queue_email`, a sender in the dispatcher, and per-organisation
configuration whose secrets live in `EncryptedDbVault`.

## Deployment

| Variable | Meaning |
| --- | --- |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` | The relay. `SMTP_FROM` may carry a display name: `AequorOS <noreply@example.com>` |
| `BANK_APP_BASE_URL` | Origin of every email link |
| `NOTIFICATION_EMAIL_ALLOWLIST` | Non-production safety valve: comma-separated addresses or `@domains`; everyone else is `suppressed` |
| `NOTIFICATION_MAX_ATTEMPTS` | Attempts before a delivery is dead-lettered (default 6) |
| `NOTIFICATION_BATCH_SIZE` | Deliveries per dispatch run (default 100) |

The three jobs run in the core worker lane. A worker running older code does
not claim them, so they are inert until the worker that knows them runs.
