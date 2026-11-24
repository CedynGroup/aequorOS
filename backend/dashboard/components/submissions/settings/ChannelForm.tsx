"use client";

/**
 * One submission channel's stored configuration. Institution code, reporting
 * basis and contacts are not here: every channel takes them from the filing
 * profile. Credentials are write-only (the API returns only a fingerprint).
 *
 * The stored row is read only by the exact reg/restricted view binding, and
 * the PUT replaces the whole stored map, so the form is offered only to that
 * reader: a configure-only caller who cannot read what is stored must not be
 * handed a blank form that would overwrite it.
 */

import { useEffect, useId, useState, type ReactNode } from "react";
import { KeyRound, Loader2, Save } from "lucide-react";
import type { ChannelConfigRead } from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  isChannelConfigMissingError,
  useChannelConfig,
  useSaveChannelConfig,
} from "@/lib/api/hooks";
import { useConfigureEligibility, useViewEligibility } from "@/lib/api/configureAccess";
import { centralBankName } from "@/lib/format";
import { DeniedSettings } from "./DeniedSettings";

export type FormChannel = "orass_api" | "orass_sandbox" | "email";

const SIMULATED_OUTCOMES = [
  { value: "ack", label: "Accepted" },
  { value: "reject", label: "Returned for correction" },
  { value: "slow", label: "Accepted after a delay" },
  { value: "decline", label: "Declined" },
];

const RESUBMISSION_OUTCOMES = [
  { value: "grant", label: "Granted" },
  { value: "deny", label: "Refused" },
];

const SIGN_IN_METHODS = [
  { value: "api_key", label: "API key" },
  { value: "basic", label: "Username and password" },
];

type FormState = {
  sandboxBehavior: string;
  resubmissionBehavior: string;
  downtime: boolean;
  fallbackRecipient: string;
  apiBaseUrl: string;
  authMode: string;
  timeoutSeconds: string;
  verifyTls: boolean;
  principalUserName: string;
  principalUserEmail: string;
  username: string;
  password: string;
  apiKey: string;
};

const EMPTY_FORM: FormState = {
  sandboxBehavior: "ack",
  resubmissionBehavior: "grant",
  downtime: false,
  fallbackRecipient: "",
  apiBaseUrl: "",
  authMode: "api_key",
  timeoutSeconds: "",
  verifyTls: true,
  principalUserName: "",
  principalUserEmail: "",
  username: "",
  password: "",
  apiKey: "",
};

function formFromConfig(config: ChannelConfigRead | undefined): FormState {
  if (!config) return EMPTY_FORM;
  const raw = config.config as Record<string, unknown>;
  const text = (value: unknown, fallback = "") =>
    typeof value === "string" ? value : fallback;
  return {
    ...EMPTY_FORM,
    sandboxBehavior: text(raw.sandbox_behavior, "ack"),
    resubmissionBehavior: text(raw.resubmission_behavior, "grant"),
    downtime: raw.downtime === true,
    fallbackRecipient: text(raw.fallback_recipient),
    apiBaseUrl: text(raw.api_base_url),
    authMode: text(raw.auth_mode, "api_key"),
    timeoutSeconds: raw.timeout_seconds == null ? "" : String(raw.timeout_seconds),
    verifyTls: raw.verify_tls !== false,
    principalUserName: text(raw.principal_user_name),
    principalUserEmail: text(raw.principal_user_email),
  };
}

const COPY: Record<FormChannel, { title: string; subtitle: string; save: string }> = {
  orass_api: {
    title: "ORASS access",
    subtitle: "Institution code, reporting basis and contacts come from your filing profile.",
    save: "Save access details",
  },
  orass_sandbox: {
    title: "Rehearsal simulator",
    subtitle:
      "Practise ORASS filings end to end. Rehearsals are marked as such everywhere and never reach the regulator.",
    save: "Save simulator settings",
  },
  email: {
    title: "Email fallback",
    subtitle:
      "For downtime only: produces the complete files and step-by-step instructions to email them.",
    save: "Save",
  },
};

const inputClass =
  "w-full min-h-10 rounded-sm border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light";
const labelClass = "mb-1.5 block text-caption font-medium text-navy";

function Field({
  id,
  label,
  hint,
  children,
  wide = false,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "md:col-span-2" : undefined}>
      <label className={labelClass} htmlFor={id}>
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-micro leading-relaxed text-slate">{hint}</p>}
    </div>
  );
}

export default function ChannelForm({
  bankId,
  channel,
}: {
  bankId: string;
  channel: FormChannel;
}) {
  const { allowed: canView, reason: viewReason } = useViewEligibility(bankId, "reg");
  const configQuery = useChannelConfig(bankId, channel, { enabled: canView });
  const save = useSaveChannelConfig(bankId);
  const { allowed: canConfigure, reason: configureReason } = useConfigureEligibility(
    bankId,
    "reg",
  );
  const reasonId = useId();
  const fieldId = useId();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [hydrated, setHydrated] = useState(false);
  const unconfigured = isChannelConfigMissingError(configQuery.error);
  const config = configQuery.data;

  useEffect(() => {
    if (hydrated) return;
    if (config) {
      setForm(formFromConfig(config));
      setHydrated(true);
    } else if (unconfigured) {
      setForm(EMPTY_FORM);
      setHydrated(true);
    }
  }, [config, unconfigured, hydrated]);

  const copy = COPY[channel];
  if (!canView) return <DeniedSettings title={copy.title} reason={viewReason} />;
  if (configQuery.isLoading || !hydrated) return <SkeletonCard />;
  if (configQuery.error && !unconfigured) {
    return (
      <ErrorPanel
        error={configQuery.error}
        onRetry={() => configQuery.refetch()}
        title={`Could not load ${copy.title.toLowerCase()}`}
      />
    );
  }

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));
  const id = (name: string) => `${fieldId}-${name}`;

  const handleSave = () => {
    if (!canConfigure) return; // refused before any side effect, as the server refuses
    const payload: Record<string, unknown> = {};
    if (channel === "orass_api") {
      payload.api_base_url = form.apiBaseUrl.trim();
      payload.auth_mode = form.authMode;
      const timeout = Number.parseInt(form.timeoutSeconds, 10);
      if (Number.isFinite(timeout) && timeout > 0) payload.timeout_seconds = timeout;
      payload.verify_tls = form.verifyTls;
    }
    if (channel === "orass_sandbox") {
      payload.sandbox_behavior = form.sandboxBehavior;
      payload.resubmission_behavior = form.resubmissionBehavior;
      payload.downtime = form.downtime;
    }
    if (channel === "orass_api" || channel === "orass_sandbox") {
      if (form.principalUserName.trim()) payload.principal_user_name = form.principalUserName.trim();
      if (form.principalUserEmail.trim()) {
        payload.principal_user_email = form.principalUserEmail.trim();
      }
    }
    if (channel === "email" && form.fallbackRecipient.trim()) {
      payload.fallback_recipient = form.fallbackRecipient.trim();
    }
    // Credentials are sent only when someone typed new material.
    let credentials: Record<string, unknown> | undefined;
    if (channel === "orass_api" && form.authMode === "api_key") {
      if (form.apiKey.trim() !== "") credentials = { api_key: form.apiKey.trim() };
    } else if (channel === "orass_api") {
      if (form.username.trim() !== "" || form.password !== "") {
        credentials = { username: form.username.trim(), password: form.password };
      }
    }
    save.mutate(
      { channel, config: payload, credentials },
      {
        onSuccess: () =>
          setForm((prev) => ({ ...prev, username: "", password: "", apiKey: "" })),
      },
    );
  };

  return (
    <SectionCard
      title={copy.title}
      subtitle={copy.subtitle}
      actions={
        <span className="inline-flex max-w-64 flex-wrap items-center justify-end gap-2">
          {!canConfigure && (
            <span id={reasonId} className="text-right text-micro text-slate">
              {configureReason}
            </span>
          )}
          <button
            type="button"
            disabled={!canConfigure || save.isPending}
            aria-describedby={!canConfigure ? reasonId : undefined}
            onClick={handleSave}
            className="inline-flex min-h-10 items-center gap-1.5 px-3 text-caption font-medium btn-primary disabled:opacity-60"
          >
            {save.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <Save size={13} aria-hidden />
            )}
            {copy.save}
          </button>
        </span>
      }
    >
      <div className="grid max-w-3xl grid-cols-1 gap-4 md:grid-cols-2">
        {channel === "orass_api" && (
          <>
            <Field id={id("url")} label="ORASS API address" wide>
              <input
                id={id("url")}
                value={form.apiBaseUrl}
                onChange={(e) => set("apiBaseUrl", e.target.value)}
                placeholder={`Provided by the ${centralBankName()}`}
                className={inputClass}
              />
            </Field>
            <Field id={id("auth")} label="Sign-in method">
              <select
                id={id("auth")}
                value={form.authMode}
                onChange={(e) => set("authMode", e.target.value)}
                className={inputClass}
              >
                {SIGN_IN_METHODS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id={id("timeout")} label="Wait for a response (seconds)">
              <input
                id={id("timeout")}
                type="number"
                min={1}
                value={form.timeoutSeconds}
                onChange={(e) => set("timeoutSeconds", e.target.value)}
                placeholder="30"
                className={inputClass}
              />
            </Field>
            <div className="md:col-span-2">
              <label className="inline-flex min-h-10 cursor-pointer items-center gap-2.5 text-body text-navy">
                <input
                  type="checkbox"
                  checked={form.verifyTls}
                  onChange={(e) => set("verifyTls", e.target.checked)}
                  className="h-4 w-4 accent-[rgb(var(--accent))]"
                />
                Check the ORASS server’s certificate
              </label>
            </div>
          </>
        )}

        {channel === "orass_sandbox" && (
          <>
            <Field id={id("outcome")} label="Simulated outcome">
              <select
                id={id("outcome")}
                value={form.sandboxBehavior}
                onChange={(e) => set("sandboxBehavior", e.target.value)}
                className={inputClass}
              >
                {SIMULATED_OUTCOMES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id={id("resubmission")} label="Requests to resubmit">
              <select
                id={id("resubmission")}
                value={form.resubmissionBehavior}
                onChange={(e) => set("resubmissionBehavior", e.target.value)}
                className={inputClass}
              >
                {RESUBMISSION_OUTCOMES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            <div className="md:col-span-2">
              <label className="inline-flex cursor-pointer items-start gap-2.5 text-body text-navy">
                <input
                  type="checkbox"
                  checked={form.downtime}
                  onChange={(e) => set("downtime", e.target.checked)}
                  className="mt-1 h-4 w-4 accent-[rgb(var(--accent))]"
                />
                <span>
                  <span className="block">Simulate an ORASS outage</span>
                  <span className="block text-caption text-slate">
                    Rehearsal filings fail as if ORASS were down, so your team can
                    practise the email fallback and the re-upload that completes it.
                  </span>
                </span>
              </label>
            </div>
          </>
        )}

        {(channel === "orass_api" || channel === "orass_sandbox") && (
          <>
            <Field
              id={id("principal-name")}
              label="ORASS principal user"
              hint="Only this person can submit on the ORASS portal."
            >
              <input
                id={id("principal-name")}
                value={form.principalUserName}
                onChange={(e) => set("principalUserName", e.target.value)}
                placeholder="Name and title"
                className={inputClass}
              />
            </Field>
            <Field id={id("principal-email")} label="Principal user email">
              <input
                id={id("principal-email")}
                type="email"
                value={form.principalUserEmail}
                onChange={(e) => set("principalUserEmail", e.target.value)}
                placeholder="principal@yourbank"
                className={inputClass}
              />
            </Field>
          </>
        )}

        {channel === "email" && (
          <Field
            id={id("recipient")}
            label="Downtime return address"
            hint="The address your supervision contact gave you for returns filed while the regulator’s system is down."
            wide
          >
            <input
              id={id("recipient")}
              type="email"
              value={form.fallbackRecipient}
              onChange={(e) => set("fallbackRecipient", e.target.value)}
              placeholder="returns@regulator"
              className={inputClass}
            />
          </Field>
        )}
      </div>

      {channel === "orass_api" && (
        <div className="mt-6 max-w-3xl border-t border-border-light pt-5">
          <div className="flex flex-wrap items-center gap-3">
            <p className="inline-flex items-center gap-2 text-body font-medium text-navy">
              <KeyRound size={14} className="text-slate" aria-hidden />
              {form.authMode === "api_key" ? "API key" : "Portal sign-in"}
            </p>
            <StatusPill tone={config?.hasCredentials ? "success" : "slate"}>
              {config?.hasCredentials ? "Stored" : "None stored"}
            </StatusPill>
          </div>
          <p className="mt-1 text-caption leading-relaxed text-slate">
            Stored encrypted and used only while a filing is sent. It is never shown
            again; you can replace it at any time. Leave blank to keep the stored one.
          </p>
          <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
            {form.authMode === "api_key" ? (
              <Field id={id("api-key")} label="API key">
                <input
                  id={id("api-key")}
                  type="password"
                  value={form.apiKey}
                  onChange={(e) => set("apiKey", e.target.value)}
                  autoComplete="new-password"
                  placeholder={`Paste the key the ${centralBankName()} issued`}
                  className={inputClass}
                />
              </Field>
            ) : (
              <>
                <Field id={id("username")} label="Portal username">
                  <input
                    id={id("username")}
                    value={form.username}
                    onChange={(e) => set("username", e.target.value)}
                    autoComplete="off"
                    className={inputClass}
                  />
                </Field>
                <Field id={id("password")} label="Portal password">
                  <input
                    id={id("password")}
                    type="password"
                    value={form.password}
                    onChange={(e) => set("password", e.target.value)}
                    autoComplete="new-password"
                    className={inputClass}
                  />
                </Field>
              </>
            )}
          </div>
        </div>
      )}

      {save.error && (
        <div className="mt-4 max-w-3xl">
          <ErrorPanel error={save.error} title="Could not save" />
        </div>
      )}
      {save.isSuccess && (
        <p role="status" className="mt-4 text-caption font-medium text-success">
          Saved.
        </p>
      )}
    </SectionCard>
  );
}
