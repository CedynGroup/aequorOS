"use client";

/**
 * The governed forecast assumption register: the approved version in force,
 * the one change in flight, and the version history.
 *
 * Controls offer actions from projected edit and structural approval
 * capabilities. The server enforces the maker-checker contract documented in
 * backend/docs/forecasting_enforcement_rollout.md.
 */

import { useState } from "react";
import {
  CheckCircle2,
  FilePenLine,
  Send,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import type {
  ForecastAssumptionProvenanceRead,
  ForecastAssumptionRegisterRead,
  ForecastAssumptionVersionRead,
  ForecastPresetAssumptionsRead,
  ForecastPresetSetRead,
  ForecastPresetSetWrite,
} from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import {
  ASSUMPTION_FIELDS,
  type AssumptionField,
  scenarioLabel,
} from "@/components/forecasting/lib";
import {
  useCreateForecastAssumptionVersion,
  useDecideForecastAssumptionVersion,
  useSubmitForecastAssumptionVersion,
  useUpdateForecastAssumptionVersion,
} from "@/lib/api/hooks";
import { fmtDateUTC, fmtTimestamp, isoDate, num } from "@/lib/api/values";

/** The seven drivers a version governs; the other three are engine defaults. */
export const PRESET_FIELDS = ASSUMPTION_FIELDS.filter(
  (f) => !f.hasEngineDefault,
);

const PRESETS = [
  { code: "base", key: "base" },
  { code: "adverse", key: "adverse" },
  { code: "severely_adverse", key: "severelyAdverse" },
] as const;

type PresetKey = (typeof PRESETS)[number]["key"];
type PresetField = (typeof PRESET_FIELDS)[number]["key"] &
  keyof ForecastPresetAssumptionsRead;
type DraftValues = Record<PresetKey, Record<PresetField, string>>;

const STATUS_TONES: Record<
  ForecastAssumptionVersionRead["status"],
  StatusTone
> = {
  draft: "slate",
  submitted: "amber",
  approved: "success",
  rejected: "critical",
};

const STATUS_LABELS: Record<ForecastAssumptionVersionRead["status"], string> = {
  draft: "Draft",
  submitted: "Awaiting approval",
  approved: "Approved",
  rejected: "Rejected",
};

function presetValue(
  presets: ForecastPresetSetRead,
  preset: PresetKey,
  field: AssumptionField,
): string {
  return presets[preset][field.key as PresetField];
}

/** "Version 2 · approved by A. Mensah on 14 Jul 2026 · effective from 30 Jun 2026" */
export function provenanceLabel(
  provenance: ForecastAssumptionProvenanceRead,
): string {
  const approver = provenance.approvedByName ?? "an unnamed approver";
  return `Version ${provenance.versionNumber} · approved by ${approver} on ${fmtTimestamp(
    provenance.approvedAt,
  )} · effective from ${fmtDateUTC(provenance.effectiveFrom)}`;
}

type AssumptionRegisterProps = {
  bankId: string;
  register: ForecastAssumptionRegisterRead;
  canEdit: boolean;
  canApprove: boolean;
};

export default function AssumptionRegister(props: AssumptionRegisterProps) {
  const open = props.register.versions.find(
    (v) => v.id === props.register.openVersionId,
  );
  return (
    <AssumptionRegisterContent
      key={`${props.bankId}:${open?.id ?? "new"}:${open?.status ?? "new"}`}
      {...props}
    />
  );
}

function AssumptionRegisterContent({
  bankId,
  register,
  canEdit,
  canApprove,
}: AssumptionRegisterProps) {
  const effective = register.versions.find(
    (v) => v.id === register.effectiveVersionId,
  );
  const open = register.versions.find((v) => v.id === register.openVersionId);
  const [editing, setEditing] = useState(false);

  return (
    <div className="space-y-6">
      <InForce
        effective={effective}
        asOf={register.asOf}
        hasApproved={register.versions.some((v) => v.status === "approved")}
      />

      {editing ? (
        <VersionEditor
          key={open?.id ?? "new"}
          bankId={bankId}
          draft={open}
          basis={open?.presets ?? effective?.presets}
          defaultEffectiveFrom={register.asOf ?? isoDate(new Date())}
          onDone={() => setEditing(false)}
        />
      ) : open ? (
        <PendingVersion
          bankId={bankId}
          version={open}
          effective={effective}
          canEdit={canEdit}
          canApprove={canApprove}
          onEdit={() => setEditing(true)}
        />
      ) : canEdit ? (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
          >
            <FilePenLine size={13} aria-hidden />
            Propose new version
          </button>
        </div>
      ) : null}

      <VersionHistory versions={register.versions} />
    </div>
  );
}

function InForce({
  effective,
  asOf,
  hasApproved,
}: {
  effective: ForecastAssumptionVersionRead | undefined;
  asOf: string | null;
  hasApproved: boolean;
}) {
  if (!effective) {
    return (
      <div
        role="status"
        className="border-l-4 border-l-warning bg-warning-light/40 px-5 py-4 text-body text-navy"
      >
        <p className="font-medium inline-flex items-center gap-2">
          <ShieldAlert size={15} aria-hidden />
          {asOf
            ? `No approved forecast assumptions${hasApproved ? " in force" : ""} for the ${fmtDateUTC(new Date(asOf))} book`
            : "Forecasting needs a book date"}
        </p>
        <p className="mt-1 text-slate">
          {asOf
            ? hasApproved
              ? "An approved version must be effective on or before this book date."
              : "A version must be drafted, submitted and approved by a second person."
            : "Ingest a book so forecasting can resolve the approved version effective on or before its date."}
          {!asOf &&
            !hasApproved &&
            " No approved forecast assumptions exist yet; a version must also be drafted, submitted and approved by a second person."}{" "}
          Forecasts, the What-if Lab and the Optimizer are not computable until
          then. Nothing is substituted in the meantime.
        </p>
      </div>
    );
  }
  return (
    <SectionCard
      title="Approved assumptions in force"
      subtitle={
        asOf
          ? `The version every run on the ${fmtDateUTC(new Date(asOf))} book resolves`
          : "The version runs resolve"
      }
    >
      <dl className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 text-body">
        <Fact label="Version" value={`Version ${effective.versionNumber}`} />
        <Fact label="Approved by" value={effective.reviewedByName ?? "—"} />
        <Fact
          label="Approved on"
          value={
            effective.reviewedAt
              ? fmtTimestamp(new Date(effective.reviewedAt))
              : "—"
          }
        />
        <Fact
          label="Effective from"
          value={fmtDateUTC(effective.effectiveFrom)}
        />
      </dl>
      <p className="mt-3 text-caption text-slate">{effective.changeNote}</p>
    </SectionCard>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className="mt-1 text-navy font-medium">{value}</dd>
    </div>
  );
}

function PendingVersion({
  bankId,
  version,
  effective,
  canEdit,
  canApprove,
  onEdit,
}: {
  bankId: string;
  version: ForecastAssumptionVersionRead;
  effective: ForecastAssumptionVersionRead | undefined;
  canEdit: boolean;
  canApprove: boolean;
  onEdit: () => void;
}) {
  const submit = useSubmitForecastAssumptionVersion(bankId);
  const decide = useDecideForecastAssumptionVersion(bankId);
  const [note, setNote] = useState("");
  const error = submit.error ?? decide.error;

  return (
    <SectionCard
      title={`Version ${version.versionNumber} — pending change`}
      subtitle={
        version.status === "draft"
          ? "Drafted; submit it for a second person to approve"
          : "Submitted; a checker who neither drafted nor submitted it decides"
      }
      actions={
        <StatusPill tone={STATUS_TONES[version.status]}>
          {STATUS_LABELS[version.status]}
        </StatusPill>
      }
    >
      <div className="space-y-4">
        <p className="text-body text-navy">
          Effective from{" "}
          <span className="font-medium">
            {fmtDateUTC(version.effectiveFrom)}
          </span>
          {" · "}
          drafted by {version.createdByName ?? "—"}
          {version.submittedByName
            ? ` · submitted by ${version.submittedByName}`
            : ""}
        </p>
        <p className="text-caption text-slate">{version.changeNote}</p>

        <ValuesTable presets={version.presets} compareTo={effective?.presets} />

        {error ? (
          <ErrorPanel error={error} title="The change was refused" />
        ) : null}

        {version.status === "draft" && canEdit && (
          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={onEdit}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
            >
              <FilePenLine size={13} aria-hidden />
              Edit draft
            </button>
            <button
              type="button"
              disabled={submit.isPending}
              onClick={() => submit.mutate(version.id)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              <Send size={13} aria-hidden />
              Submit for approval
            </button>
          </div>
        )}

        {version.status === "submitted" &&
          (canApprove ? (
            <div className="space-y-2">
              <label className="block text-caption font-medium text-navy">
                Decision note
                <textarea
                  rows={2}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Board minute, or why the version is rejected"
                  className="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-body text-navy"
                />
              </label>
              <div className="flex items-center gap-2 justify-end">
                <button
                  type="button"
                  disabled={decide.isPending || note.trim() === ""}
                  title={
                    note.trim() === ""
                      ? "Say why the version is rejected"
                      : undefined
                  }
                  onClick={() =>
                    decide.mutate({
                      versionId: version.id,
                      decision: "reject",
                      payload: { note: note.trim() },
                    })
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-critical border border-critical/30 rounded-md bg-surface-raised hover:bg-critical-light disabled:opacity-60"
                >
                  <XCircle size={13} aria-hidden />
                  Reject
                </button>
                <button
                  type="button"
                  disabled={decide.isPending}
                  onClick={() =>
                    decide.mutate({
                      versionId: version.id,
                      decision: "approve",
                      payload: { note: note.trim() || null },
                    })
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
                >
                  <CheckCircle2 size={13} aria-hidden />
                  Approve
                </button>
              </div>
            </div>
          ) : (
            <p className="text-caption text-slate">
              Awaiting a checker with Forecasting approval authority.
            </p>
          ))}
      </div>
    </SectionCard>
  );
}

/** A version's values, with the change against the version in force. */
function ValuesTable({
  presets,
  compareTo,
}: {
  presets: ForecastPresetSetRead;
  compareTo: ForecastPresetSetRead | undefined;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse tnum">
        <thead>
          <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
            <th className="text-left px-4 py-2.5">Assumption</th>
            {PRESETS.map((p) => (
              <th key={p.code} className="text-right px-4 py-2.5">
                {scenarioLabel(p.code)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {PRESET_FIELDS.map((field) => (
            <tr
              key={field.key}
              className="border-b border-border-light last:border-b-0"
            >
              <td className="px-4 py-2.5 text-navy/90 font-medium">
                {field.label}
              </td>
              {PRESETS.map((p) => {
                const value = num(presetValue(presets, p.key, field));
                const before = compareTo
                  ? num(presetValue(compareTo, p.key, field))
                  : null;
                const changed = before !== null && before !== value;
                return (
                  <td
                    key={p.code}
                    className={`px-4 py-2.5 text-right font-mono tnum ${changed ? "text-action font-medium" : ""}`}
                  >
                    {value}
                    {field.unit}
                    {changed && (
                      <span className="ml-1.5 text-caption text-slate">
                        (was {before}
                        {field.unit})
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VersionEditor({
  bankId,
  draft,
  basis,
  defaultEffectiveFrom,
  onDone,
}: {
  bankId: string;
  /** The draft being revised; absent when proposing a new version. */
  draft: ForecastAssumptionVersionRead | undefined;
  /** The values the form starts from. */
  basis: ForecastPresetSetRead | undefined;
  defaultEffectiveFrom: string;
  onDone: () => void;
}) {
  const create = useCreateForecastAssumptionVersion(bankId);
  const update = useUpdateForecastAssumptionVersion(bankId);
  const [effectiveFrom, setEffectiveFrom] = useState(
    draft ? isoDate(draft.effectiveFrom) : defaultEffectiveFrom,
  );
  const [changeNote, setChangeNote] = useState(draft?.changeNote ?? "");
  const [values, setValues] = useState<DraftValues>(() => {
    const start = (preset: PresetKey) =>
      Object.fromEntries(
        PRESET_FIELDS.map((field) => [
          field.key,
          basis ? String(num(presetValue(basis, preset, field))) : "",
        ]),
      ) as Record<PresetField, string>;
    return {
      base: start("base"),
      adverse: start("adverse"),
      severelyAdverse: start("severelyAdverse"),
    };
  });
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;
  const complete =
    changeNote.trim() !== "" &&
    effectiveFrom !== "" &&
    PRESETS.every((p) =>
      PRESET_FIELDS.every(
        (f) => values[p.key][f.key as PresetField].trim() !== "",
      ),
    );

  const save = () => {
    const presets: ForecastPresetSetWrite = {
      base: values.base,
      adverse: values.adverse,
      severelyAdverse: values.severelyAdverse,
    };
    const onSuccess = () => onDone();
    if (draft) {
      update.mutate(
        {
          versionId: draft.id,
          payload: { effectiveFrom, presets, changeNote: changeNote.trim() },
        },
        { onSuccess },
      );
    } else {
      create.mutate(
        {
          effectiveFrom: new Date(effectiveFrom),
          presets,
          changeNote: changeNote.trim(),
        },
        { onSuccess },
      );
    }
  };

  return (
    <SectionCard
      title={
        draft
          ? `Edit draft version ${draft.versionNumber}`
          : "Propose new version"
      }
      subtitle="A complete set: every scenario with every driver. Values are percentages."
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (complete && !pending) save();
        }}
      >
        <label className="block text-caption font-medium text-navy max-w-xs">
          Effective from
          <input
            type="date"
            required
            value={effectiveFrom}
            onChange={(e) => setEffectiveFrom(e.target.value)}
            className="mt-1 w-full px-2 py-1.5 text-body text-navy border border-border rounded bg-surface-raised"
          />
          <span className="mt-1 block font-normal text-slate">
            A run resolves the approved version with the latest effective-from
            date on or before its book date. Ties use the latest approval time.
          </span>
        </label>

        <div className="overflow-x-auto">
          <table className="w-full text-body border-collapse tnum">
            <thead>
              <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                <th className="text-left px-4 py-2.5">Assumption</th>
                {PRESETS.map((p) => (
                  <th key={p.code} className="text-right px-4 py-2.5">
                    {scenarioLabel(p.code)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PRESET_FIELDS.map((field) => (
                <tr
                  key={field.key}
                  className="border-b border-border-light last:border-b-0"
                >
                  <td className="px-4 py-2 text-navy/90 font-medium">
                    {field.label}
                    <span className="text-slate font-normal">
                      {" "}
                      ({field.unit.trim()})
                    </span>
                  </td>
                  {PRESETS.map((p) => (
                    <td key={p.code} className="px-4 py-2 text-right">
                      <input
                        type="number"
                        step="any"
                        required
                        value={values[p.key][field.key as PresetField]}
                        onChange={(e) =>
                          setValues((current) => ({
                            ...current,
                            [p.key]: {
                              ...current[p.key],
                              [field.key]: e.target.value,
                            },
                          }))
                        }
                        aria-label={`${scenarioLabel(p.code)} ${field.label}`}
                        className="w-24 px-2 py-1 text-caption font-mono text-navy border border-border rounded bg-surface-raised tnum text-right"
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <label className="block text-caption font-medium text-navy">
          Change note
          <textarea
            required
            rows={2}
            value={changeNote}
            onChange={(e) => setChangeNote(e.target.value)}
            placeholder="What changed and why — the checker reads this"
            className="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-body text-navy"
          />
        </label>

        {error ? (
          <ErrorPanel error={error} title="The draft was refused" />
        ) : null}

        <div className="flex items-center gap-2 justify-end">
          <button
            type="button"
            onClick={onDone}
            className="px-3 py-2 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!complete || pending}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            <FilePenLine size={13} aria-hidden />
            Save draft
          </button>
        </div>
      </form>
    </SectionCard>
  );
}

function VersionHistory({
  versions,
}: {
  versions: ForecastAssumptionVersionRead[];
}) {
  return (
    <SectionCard
      title="Version history"
      subtitle="Every version of the bank's assumption set and who decided it"
      noPadding
    >
      {versions.length === 0 ? (
        <p className="px-5 py-4 text-body text-slate">No versions yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-body border-collapse tnum">
            <thead>
              <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                <th className="text-left px-4 py-2.5">Version</th>
                <th className="text-left px-4 py-2.5">Status</th>
                <th className="text-left px-4 py-2.5">Effective from</th>
                <th className="text-left px-4 py-2.5">Proposed by</th>
                <th className="text-left px-4 py-2.5">Decided by</th>
                <th className="text-left px-4 py-2.5">Note</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr
                  key={v.id}
                  className="border-b border-border-light last:border-b-0 align-top"
                >
                  <td className="px-4 py-2.5 font-medium text-navy">
                    Version {v.versionNumber}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusPill tone={STATUS_TONES[v.status]}>
                      {STATUS_LABELS[v.status]}
                    </StatusPill>
                  </td>
                  <td className="px-4 py-2.5">{fmtDateUTC(v.effectiveFrom)}</td>
                  <td className="px-4 py-2.5">
                    {v.submittedByName ?? v.createdByName ?? "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    {v.reviewedByName ?? "—"}
                    {v.reviewedAt && (
                      <span className="block text-caption text-slate">
                        {fmtTimestamp(new Date(v.reviewedAt))}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-caption text-slate">
                    {v.reviewNote ?? v.changeNote}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  );
}
