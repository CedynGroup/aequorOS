"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Lock } from "lucide-react";
import type {
  EmailMode,
  NotificationOrgSettingsRead,
} from "@aequoros/risk-service-api";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import PageHeader from "@/components/ui/PageHeader";
import { SkeletonLine } from "@/components/ui/Skeleton";
import {
  useNotificationOrgSettings,
  useUpdateNotificationOrgSettings,
  type PolicyEdit,
} from "@/lib/api/notificationSettings";
import {
  EMAIL_MODES,
  MODE_SHORT_LABELS,
  hourLabel,
  orgSettingsProblem,
} from "@/lib/notifications/copy";

const TIMEZONES = [
  "Africa/Accra",
  "Africa/Lagos",
  "Africa/Nairobi",
  "Africa/Johannesburg",
  "Europe/London",
  "America/New_York",
  "UTC",
];

const INPUT_CLASS =
  "w-full px-3 py-2.5 border border-border rounded-md bg-surface text-body text-navy focus:outline-hidden focus:ring-2 focus:ring-action/25 focus:border-action";

type Form = {
  emailEnabled: boolean;
  timeZone: string;
  digestHour: number;
  remindersEnabled: boolean;
  reminderAfterHours: number;
  escalateAfterHours: number;
  /** "default" means the platform default applies (no rule). */
  policies: Record<string, { mode: EmailMode | "default"; locked: boolean }>;
};

function formFrom(settings: NotificationOrgSettingsRead): Form {
  return {
    emailEnabled: settings.emailEnabled,
    timeZone: settings.timeZone,
    digestHour: settings.digestHour,
    remindersEnabled: settings.remindersEnabled,
    reminderAfterHours: settings.reminderAfterHours,
    escalateAfterHours: settings.escalateAfterHours,
    policies: Object.fromEntries(
      settings.policies.map((policy) => [
        policy.key,
        { mode: policy.emailMode ?? "default", locked: policy.locked },
      ]),
    ),
  };
}

export default function NotificationOrganisationPage() {
  const settings = useNotificationOrgSettings();
  const save = useUpdateNotificationOrgSettings();
  const [form, setForm] = useState<Form | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(
    null,
  );

  useEffect(() => {
    if (settings.data && form === null) setForm(formFrom(settings.data));
  }, [settings.data, form]);

  const grouped = useMemo(() => {
    const groups = new Map<string, NotificationOrgSettingsRead["policies"]>();
    for (const policy of settings.data?.policies ?? []) {
      groups.set(policy.category, [...(groups.get(policy.category) ?? []), policy]);
    }
    return [...groups.entries()];
  }, [settings.data]);

  const problem = form ? orgSettingsProblem(form) : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!form || problem || !settings.data) return;
    setMessage(null);
    const policies: Record<string, PolicyEdit> = {};
    for (const policy of settings.data.policies) {
      if (policy.platformLocked) continue;
      const edit = form.policies[policy.key];
      policies[policy.key] =
        !edit || edit.mode === "default"
          ? null
          : { emailMode: edit.mode, locked: edit.locked };
    }
    save.mutate(
      {
        emailEnabled: form.emailEnabled,
        timeZone: form.timeZone,
        digestHour: form.digestHour,
        remindersEnabled: form.remindersEnabled,
        reminderAfterHours: form.reminderAfterHours,
        escalateAfterHours: form.escalateAfterHours,
        policies,
      },
      {
        onSuccess: (saved) => {
          setForm(formFrom(saved));
          setMessage({ kind: "ok", text: "Saved. Changes apply to the next notification." });
        },
        onError: (error) =>
          setMessage({
            kind: "error",
            text: error instanceof Error ? error.message : "Could not save these settings.",
          }),
      },
    );
  };

  const update = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((current) => (current ? { ...current, [key]: value } : current));

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: "Notifications", href: "/settings/notifications" },
          { label: "Organisation settings" },
        ]}
        title="Organisation notification settings"
        subtitle="Decide how AequorOS notifies everyone in your organisation."
      />
      <div className="px-4 md:px-8 py-6 max-w-5xl">
        {settings.isLoading || (settings.data && !form) ? (
          <Card>
            <CardBody className="space-y-4">
              <SkeletonLine className="w-48" />
              <SkeletonLine className="w-full" />
            </CardBody>
          </Card>
        ) : settings.data === null ? (
          <Card>
            <CardBody>
              <p className="text-body text-navy">
                Only account administrators can change organisation
                notification settings.
              </p>
            </CardBody>
          </Card>
        ) : settings.error || !form || !settings.data ? (
          <Card>
            <CardBody>
              <p className="text-body text-danger">
                Could not load the organisation settings. {settings.error?.message}
              </p>
            </CardBody>
          </Card>
        ) : (
          <form onSubmit={onSubmit} className="space-y-6">
            {!settings.data.emailConfigured && (
              <p
                role="status"
                className="rounded-md border border-warning/40 bg-warning-light/30 px-4 py-3 text-body text-navy"
              >
                Email isn&rsquo;t set up for AequorOS yet. These settings take
                effect as soon as it is.
              </p>
            )}
            <Card>
              <CardHeader title="Email and daily summary" />
              <CardBody className="space-y-5">
                <Toggle
                  id="email-enabled"
                  checked={form.emailEnabled}
                  onChange={(value) => update("emailEnabled", value)}
                  label="Send notifications by email"
                  hint="When off, people see notifications in the app only. Regulator refusals and returns sent back for correction are always emailed."
                />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <label className="block">
                    <span className="block text-caption font-medium text-navy mb-1.5">
                      Time zone
                    </span>
                    <select
                      value={form.timeZone}
                      onChange={(event) => update("timeZone", event.target.value)}
                      className={INPUT_CLASS}
                    >
                      {[...new Set([form.timeZone, ...TIMEZONES])].map((zone) => (
                        <option key={zone} value={zone}>
                          {zone.replaceAll("_", " ")}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="block text-caption font-medium text-navy mb-1.5">
                      Daily summary time
                    </span>
                    <select
                      value={form.digestHour}
                      onChange={(event) => update("digestHour", Number(event.target.value))}
                      className={INPUT_CLASS}
                    >
                      {Array.from({ length: 24 }, (_, hour) => (
                        <option key={hour} value={hour}>
                          {hourLabel(hour)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Reminders and escalation"
                subtitle="For returns waiting at a review stage with no decision."
              />
              <CardBody className="space-y-5">
                <Toggle
                  id="reminders-enabled"
                  checked={form.remindersEnabled}
                  onChange={(value) => update("remindersEnabled", value)}
                  label="Remind and escalate"
                  hint="Reminds the people who can take the stage, then escalates to them, the people who worked on the return and your organisation's owners."
                />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <HoursField
                    id="reminder-hours"
                    label="First reminder after (hours)"
                    value={form.reminderAfterHours}
                    disabled={!form.remindersEnabled}
                    onChange={(value) => update("reminderAfterHours", value)}
                  />
                  <HoursField
                    id="escalate-hours"
                    label="Escalate after (hours)"
                    value={form.escalateAfterHours}
                    disabled={!form.remindersEnabled}
                    onChange={(value) => update("escalateAfterHours", value)}
                  />
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Defaults for everyone"
                subtitle="Choose how each notification reaches people who haven't picked for themselves. Lock it to apply it to everyone."
              />
              <div className="overflow-x-auto">
                <table className="w-full text-body">
                  <thead>
                    <tr className="text-left text-caption text-slate border-b border-border-light">
                      <th className="px-5 py-2 font-medium">Notification</th>
                      <th className="px-3 py-2 font-medium">Default for everyone</th>
                      <th className="px-5 py-2 font-medium">Lock</th>
                    </tr>
                  </thead>
                  {grouped.map(([category, policies]) => (
                    <tbody key={category} className="divide-y divide-border-light">
                      <tr>
                        <td
                          colSpan={3}
                          className="px-5 pt-4 pb-1 text-caption font-semibold uppercase tracking-wide text-slate"
                        >
                          {category}
                        </td>
                      </tr>
                      {policies.map((policy) => {
                        const edit = form.policies[policy.key] ?? {
                          mode: "default" as const,
                          locked: false,
                        };
                        return (
                          <tr key={policy.key}>
                            <td className="px-5 py-3 align-top">
                              <p className="font-medium text-navy">{policy.label}</p>
                              <p className="text-caption text-slate">{policy.description}</p>
                            </td>
                            <td className="px-3 py-3 align-top">
                              {policy.platformLocked ? (
                                <span className="inline-flex items-center gap-1 text-caption text-slate">
                                  <Lock size={12} aria-hidden /> Always emailed
                                </span>
                              ) : (
                                <select
                                  aria-label={`Default for “${policy.label}”`}
                                  value={edit.mode}
                                  onChange={(event) =>
                                    update("policies", {
                                      ...form.policies,
                                      [policy.key]: {
                                        mode: event.target.value as EmailMode | "default",
                                        locked:
                                          event.target.value === "default" ? false : edit.locked,
                                      },
                                    })
                                  }
                                  className={`${INPUT_CLASS} min-w-44`}
                                >
                                  <option value="default">
                                    AequorOS default ({MODE_SHORT_LABELS[policy.defaultEmail].toLowerCase()})
                                  </option>
                                  {EMAIL_MODES.map((mode) => (
                                    <option key={mode} value={mode}>
                                      {MODE_SHORT_LABELS[mode]}
                                    </option>
                                  ))}
                                </select>
                              )}
                            </td>
                            <td className="px-5 py-3 align-top">
                              {!policy.platformLocked && (
                                <input
                                  type="checkbox"
                                  aria-label={`Lock “${policy.label}” for everyone`}
                                  checked={edit.locked}
                                  disabled={edit.mode === "default"}
                                  onChange={(event) =>
                                    update("policies", {
                                      ...form.policies,
                                      [policy.key]: { ...edit, locked: event.target.checked },
                                    })
                                  }
                                  className="w-4 h-4 accent-action disabled:opacity-40"
                                />
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  ))}
                </table>
              </div>
            </Card>

            <div className="flex items-center gap-4">
              <button
                type="submit"
                disabled={save.isPending || problem !== null}
                className="px-4 py-2 rounded-sm bg-action text-white text-caption font-medium hover:bg-action/90 disabled:opacity-60"
              >
                {save.isPending ? "Saving…" : "Save settings"}
              </button>
              {problem ? (
                <p role="alert" className="text-caption text-danger">
                  {problem}
                </p>
              ) : message ? (
                <p
                  role={message.kind === "error" ? "alert" : "status"}
                  className={`text-caption ${message.kind === "error" ? "text-danger" : "text-success"}`}
                >
                  {message.text}
                </p>
              ) : null}
            </div>
          </form>
        )}
      </div>
    </>
  );
}

function Toggle({
  id,
  checked,
  onChange,
  label,
  hint,
}: {
  id: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <label htmlFor={id} className="flex items-start gap-3 cursor-pointer">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 w-4 h-4 accent-action"
      />
      <span>
        <span className="block text-body font-medium text-navy">{label}</span>
        <span className="block text-caption text-slate">{hint}</span>
      </span>
    </label>
  );
}

function HoursField({
  id,
  label,
  value,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label htmlFor={id} className="block">
      <span className="block text-caption font-medium text-navy mb-1.5">{label}</span>
      <input
        id={id}
        type="number"
        min={1}
        step={1}
        value={Number.isFinite(value) ? value : ""}
        disabled={disabled}
        onChange={(event) => onChange(Number.parseInt(event.target.value, 10))}
        className={`${INPUT_CLASS} disabled:opacity-60`}
      />
    </label>
  );
}
