"use client";

import Link from "next/link";
import { useState } from "react";
import { Lock, MailWarning } from "lucide-react";
import type {
  EmailMode,
  NotificationEventPreferenceRead,
} from "@aequoros/risk-service-api";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import PageHeader from "@/components/ui/PageHeader";
import { SkeletonLine } from "@/components/ui/Skeleton";
import {
  useNotificationOrgSettings,
  useNotificationPreferences,
  useSetNotificationPreference,
} from "@/lib/api/notificationSettings";
import {
  EMAIL_MODES,
  MODE_LABELS,
  hourLabel,
  preferenceState,
  zoneLabel,
} from "@/lib/notifications/copy";

export default function NotificationPreferencesPage() {
  const preferences = useNotificationPreferences();
  const orgSettings = useNotificationOrgSettings();
  const isAdmin = orgSettings.data != null;
  const data = preferences.data;

  return (
    <>
      <PageHeader
        title="Notifications"
        subtitle="Everything always appears in the bell. Choose which updates also reach your inbox."
        action={
          isAdmin ? (
            <div className="flex gap-2">
              <Link
                href="/settings/notifications/organisation"
                className="px-3 py-2 rounded-sm border border-border text-caption font-medium text-navy hover:bg-surface-alt"
              >
                Organisation settings
              </Link>
              <Link
                href="/settings/notifications/deliveries"
                className="px-3 py-2 rounded-sm border border-border text-caption font-medium text-navy hover:bg-surface-alt"
              >
                Delivery log
              </Link>
            </div>
          ) : undefined
        }
      />
      <div className="px-4 md:px-8 py-6 max-w-5xl space-y-6">
        {preferences.isLoading ? (
          <Card>
            <CardBody className="space-y-4">
              <SkeletonLine className="w-48" />
              <SkeletonLine className="w-full" />
              <SkeletonLine className="w-3/4" />
            </CardBody>
          </Card>
        ) : preferences.error || !data ? (
          <Card>
            <CardBody>
              <p className="text-body text-danger">
                Could not load your notification settings.{" "}
                {preferences.error?.message}
              </p>
              <button
                type="button"
                onClick={() => void preferences.refetch()}
                className="mt-4 px-4 py-2 rounded-sm bg-action text-white text-caption font-medium hover:bg-action/90"
              >
                Try again
              </button>
            </CardBody>
          </Card>
        ) : (
          <>
            {!data.emailConfigured ? (
              <Banner>
                Email isn&rsquo;t set up for AequorOS yet — you&rsquo;ll still
                see everything in the app.
              </Banner>
            ) : !data.emailEnabled ? (
              <Banner>
                Your organisation has switched email off. You&rsquo;ll still
                see everything in the app, and urgent regulator responses are
                still emailed.
              </Banner>
            ) : null}
            <p className="text-body text-slate">
              Daily summaries arrive at {hourLabel(data.digestHour)} (
              {zoneLabel(data.timeZone)}).
            </p>
            {data.categories.map((category) => (
              <Card key={category.key}>
                <CardHeader title={category.label} />
                <ul className="divide-y divide-border-light">
                  {category.events.map((event) => (
                    <PreferenceRow key={event.key} event={event} />
                  ))}
                </ul>
              </Card>
            ))}
          </>
        )}
      </div>
    </>
  );
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-md border border-warning/40 bg-warning-light/30 px-4 py-3 text-body text-navy"
    >
      <MailWarning size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
      <p>{children}</p>
    </div>
  );
}

function PreferenceRow({ event }: { event: NotificationEventPreferenceRead }) {
  const save = useSetNotificationPreference();
  const [error, setError] = useState<string | null>(null);
  const state = preferenceState(event);

  const choose = (mode: EmailMode | null) => {
    setError(null);
    save.mutate(
      { key: event.key, mode },
      {
        onError: (failure) =>
          setError(
            failure instanceof Error
              ? failure.message
              : "Could not save this choice.",
          ),
      },
    );
  };

  return (
    <li className="px-5 py-4 flex flex-col md:flex-row md:items-start gap-3 md:gap-6">
      <div className="flex-1 min-w-0">
        <p className="text-body font-medium text-navy">{event.label}</p>
        <p className="mt-0.5 text-caption text-slate">{event.description}</p>
        {state.note && (
          <p className="mt-1 text-caption text-slate flex items-center gap-1">
            {!state.editable && <Lock size={12} aria-hidden />}
            {state.note}
          </p>
        )}
        {state.editable && !state.followsDefault && (
          <button
            type="button"
            onClick={() => choose(null)}
            disabled={save.isPending}
            className="mt-1 text-caption font-medium text-action hover:text-action-hover disabled:text-slate"
          >
            Use the organisation default
          </button>
        )}
        {error && (
          <p role="alert" className="mt-1 text-caption text-danger">
            {error}
          </p>
        )}
      </div>
      <div
        role="radiogroup"
        aria-label={`Email for “${event.label}”`}
        className="inline-flex shrink-0 rounded-md border border-border overflow-hidden self-start"
      >
        {EMAIL_MODES.map((mode) => {
          const active = state.selected === mode;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={!state.editable || save.isPending}
              onClick={() => {
                if (!active || state.followsDefault) choose(mode);
              }}
              className={`px-3 py-2 text-caption font-medium border-r last:border-r-0 border-border transition-colors ${
                active
                  ? state.followsDefault
                    ? "bg-action-light/40 text-navy"
                    : "bg-action text-white"
                  : "bg-surface text-slate hover:text-navy"
              } disabled:cursor-not-allowed ${
                !state.editable && !active ? "opacity-50" : ""
              }`}
            >
              {MODE_LABELS[mode]}
            </button>
          );
        })}
      </div>
    </li>
  );
}
