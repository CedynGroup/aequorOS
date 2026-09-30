"use client";

/**
 * Write one sentence: tell me when this figure crosses this line.
 *
 * The one combination a person gets wrong here is the threshold BASIS and its
 * number — a figure is judged either against a number the bank states or against
 * the limit already governed for it, never both and never neither. That is a
 * database CHECK, a request validator and a route refusal on the server, and it
 * is made unbuildable here: the governed option only appears for a figure the
 * catalogue declares a governed source for, and choosing it removes the number
 * field rather than disabling it.
 *
 * Only figures this reader's access already covers are offered, because the
 * catalogue the server returned is already filtered member by member — so an
 * alert that would be refused on save cannot be started.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, BellRing } from "lucide-react";
import type { BiCatalogueMeasureRead } from "@aequoros/risk-service-api";
import {
  DIRECTION_OPTIONS,
  alertSentence,
  governedLimitAvailable,
  parseRecipients,
  thresholdBasisOptions,
  thresholdFor,
  valueTypeLabel,
  type AlertDirection,
  type ThresholdBasis,
} from "@/components/bi/notifications";
import RecipientField from "@/components/bi/RecipientField";
import type { BiAlertRead, BiAlertUpsert } from "@/lib/api/bi";

export const ALERT_RECIPIENT_MAX = 50;

export type AlertDraft = Readonly<{
  name: string;
  measureId: string;
  direction: AlertDirection;
  basis: ThresholdBasis;
  stated: string;
  recipients: string;
  reason: string;
}>;

function draftFrom(alert: BiAlertRead | null): AlertDraft {
  if (alert === null) {
    return {
      name: "",
      measureId: "",
      direction: "above",
      basis: "stated",
      stated: "",
      recipients: "",
      reason: "",
    };
  }
  return {
    name: alert.name,
    measureId: alert.measureId,
    direction: alert.direction,
    basis: alert.thresholdBasis,
    stated: alert.threshold ?? "",
    recipients: alert.recipients.map((person) => person.email).join("\n"),
    reason: "",
  };
}

export default function AlertComposer({
  measures,
  editing,
  saving,
  errorMessage,
  unknownAddresses,
  onSubmit,
  onCancel,
}: {
  /** Only the figures this reader may already read — the server filtered them. */
  measures: readonly BiCatalogueMeasureRead[];
  /** The alert being changed, or null to write a new one. */
  editing: BiAlertRead | null;
  saving: boolean;
  errorMessage: string | null;
  unknownAddresses: readonly string[];
  onSubmit: (body: BiAlertUpsert) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<AlertDraft>(() => draftFrom(editing));
  useEffect(() => {
    setDraft(draftFrom(editing));
  }, [editing]);

  const measure = useMemo(
    () => measures.find((entry) => entry.id === draft.measureId) ?? null,
    [draft.measureId, measures],
  );
  const bases = thresholdBasisOptions(measure);

  // Picking a different figure can remove the governed option. The basis is
  // rebuilt from what the new figure supports rather than left as it was, so a
  // combination the server refuses cannot survive the change.
  useEffect(() => {
    if (draft.basis === "governed_limit" && !governedLimitAvailable(measure)) {
      setDraft((current) => ({ ...current, basis: "stated" }));
    }
  }, [draft.basis, measure]);

  const statedMissing = draft.basis === "stated" && draft.stated.trim() === "";
  const recipientCount = parseRecipients(draft.recipients).length;
  const complete =
    draft.name.trim() !== "" &&
    draft.measureId !== "" &&
    draft.reason.trim() !== "" &&
    !statedMissing &&
    recipientCount <= ALERT_RECIPIENT_MAX;

  function submit(): void {
    const threshold = thresholdFor(draft.basis, draft.stated.trim());
    onSubmit({
      name: draft.name.trim(),
      measureId: draft.measureId,
      direction: draft.direction,
      thresholdBasis: threshold.basis,
      threshold: threshold.threshold,
      notifyEmails: parseRecipients(draft.recipients),
      // Carried through unchanged. This form edits EMAIL recipients; an alert may
      // also name users by id (added through the API), and the server replaces its
      // stored list from the request — so not sending them here deleted them
      // silently on every edit. The owner is the only principal who may edit an
      // alert and `notify_user_ids` is the complete list for an owner, so this is
      // faithful rather than lossy.
      notifyUserIds: editing?.notifyUserIds ?? [],
      isActive: editing?.isActive ?? true,
      reason: draft.reason.trim(),
    });
  }

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (complete && !saving) submit();
      }}
    >
      <p className="inline-flex items-start gap-2 rounded-md bg-surface px-3 py-2 text-body text-navy">
        <BellRing
          size={15}
          className="mt-0.5 shrink-0 text-action"
          aria-hidden
        />
        {alertSentence(
          measure?.label ?? null,
          draft.direction,
          draft.basis,
          draft.stated,
        )}
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="bi-alert-name">
          <input
            id="bi-alert-name"
            value={draft.name}
            maxLength={120}
            onChange={(event) =>
              setDraft({ ...draft, name: event.target.value })
            }
            className={inputClass}
          />
        </Field>

        <Field label="Figure to watch" htmlFor="bi-alert-measure">
          <select
            id="bi-alert-measure"
            value={draft.measureId}
            onChange={(event) =>
              setDraft({ ...draft, measureId: event.target.value })
            }
            className={inputClass}
          >
            <option value="">Choose a figure</option>
            {measures.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Tell me when it" htmlFor="bi-alert-direction">
          <select
            id="bi-alert-direction"
            value={draft.direction}
            onChange={(event) =>
              setDraft({
                ...draft,
                direction: event.target.value as AlertDirection,
              })
            }
            className={inputClass}
          >
            {DIRECTION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Judged against" htmlFor="bi-alert-basis">
          <select
            id="bi-alert-basis"
            value={draft.basis}
            onChange={(event) => {
              // The pair is rebuilt, not patched: a governed basis cannot keep
              // a number a previous stated basis left behind, which is exactly
              // the combination the database's CHECK refuses.
              const chosen = thresholdFor(
                event.target.value as ThresholdBasis,
                draft.stated,
              );
              setDraft({
                ...draft,
                basis: chosen.basis,
                stated: chosen.threshold ?? "",
              });
            }}
            className={inputClass}
          >
            {bases.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <p className="text-caption text-slate leading-relaxed">
            {bases.find((option) => option.value === draft.basis)?.hint}
          </p>
        </Field>

        {/* The number exists only for a stated basis. Removed rather than
            disabled: a disabled field still looks like part of the answer. */}
        {draft.basis === "stated" && (
          <Field
            label={
              measure
                ? `Threshold, in ${valueTypeLabel(measure.valueType)}`
                : "Threshold"
            }
            htmlFor="bi-alert-threshold"
          >
            <input
              id="bi-alert-threshold"
              value={draft.stated}
              inputMode="decimal"
              onChange={(event) =>
                setDraft({ ...draft, stated: event.target.value })
              }
              className={inputClass}
            />
            {statedMissing && (
              <p className="text-caption text-critical">
                A number you set needs a value. Choose the governed limit
                instead if you want the line to come from the register.
              </p>
            )}
          </Field>
        )}
      </div>

      <RecipientField
        value={draft.recipients}
        onChange={(recipients) => setDraft({ ...draft, recipients })}
        max={ALERT_RECIPIENT_MAX}
        unknown={unknownAddresses}
        label="Tell these people"
        help="Each person is checked against their own access when the alert fires, so somebody who may not see this figure is recorded and not told. Leave it empty to record the verdict without notifying anyone."
      />

      <Field label="Why" htmlFor="bi-alert-reason">
        <input
          id="bi-alert-reason"
          value={draft.reason}
          maxLength={500}
          onChange={(event) =>
            setDraft({ ...draft, reason: event.target.value })
          }
          className={inputClass}
        />
        <p className="text-caption text-slate">
          Recorded with your name against this change.
        </p>
      </Field>

      {errorMessage && (
        <p className="inline-flex items-start gap-1.5 text-caption text-critical">
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          {errorMessage}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={!complete || saving}
          className="rounded-md bg-action px-3 py-2 text-caption font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving
            ? "Saving…"
            : editing
              ? "Save this alert"
              : "Create this alert"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

const inputClass =
  "rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-none";

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-caption font-medium text-navy">
        {label}
      </label>
      {children}
    </div>
  );
}
