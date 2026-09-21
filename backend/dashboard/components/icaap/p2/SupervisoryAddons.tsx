"use client";

/**
 * Capital add-ons the supervisor has imposed on this institution, and the
 * letters that impose them.
 *
 * Three things make this panel different from the rest of the Pillar 2 work.
 *
 * IT IS NOT THE INSTITUTION'S OWN ASSESSMENT. Every row here is a requirement
 * set by the supervisor, evidenced by a letter the platform holds. The letter
 * is the record; the figures are read off it.
 *
 * IT IS NEVER PUBLISHED. A supervisory add-on is excluded from every public
 * disclosure the platform produces, and the panel says so where a reader will
 * see it rather than in a policy document.
 *
 * A RECORDED LETTER IS NOT YET IN FORCE. Recording is one person's act;
 * confirming it against the letter is another's. Until it is confirmed the row
 * reads "awaiting confirmation" and adds no capital.
 *
 * Amounts are shown as at the cycle's own date, so they agree with the rest of
 * the assessment rather than with today.
 */

import { useState } from "react";
import { Download, Plus } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  downloadIcaapSupervisoryAddonLetter,
  useConfirmIcaapSupervisoryAddon,
  useCreateIcaapSupervisoryAddon,
  useIcaapSupervisoryAddons,
  useWithdrawIcaapSupervisoryAddon,
  type IcaapSupervisoryAddon,
  type IcaapSupervisoryAddonDraft,
} from "@/lib/api/icaapRiskCapital";
import { regShort } from "@/lib/format";
import { fmtDateValue } from "../format";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import {
  COMPONENT_KEY_MAX,
  DESCRIPTION_MAX,
  ICON_SM,
  REASON_MAX,
  REFERENCE_MAX,
  ROWS_MEDIUM,
  ROW_KEY_MAX,
} from "./display";
import {
  ADDON_NEVER_PUBLIC,
  NOT_AVAILABLE,
  addonStatusCopy,
  appliesToBasisLabel,
  basisLabel,
  fmtAmount,
  fmtInUnit,
} from "./labels";

/** The bases an add-on may be expressed in, as the register declares them. */
const BASES = [
  "pct_total_rwa",
  "pct_credit_rwa",
  "pct_pillar1_credit_capital",
  "absolute",
];

const APPLIES_TO = ["solo", "consolidated", "both"];

export default function SupervisoryAddons({
  bankId,
  asOf,
}: {
  bankId: string;
  /** The cycle's own as-of date, so the converted amounts agree with it. */
  asOf?: Date | string | null;
}) {
  const scope = useModuleScope();
  const canRecord = scope.capitalEdit === true;
  const canApprove = scope.capitalApprove === true;

  const [includeInactive, setIncludeInactive] = useState(false);
  const [recording, setRecording] = useState(false);
  const [deciding, setDeciding] = useState<{
    addon: IcaapSupervisoryAddon;
    action: "confirm" | "withdraw";
  } | null>(null);

  const query = useIcaapSupervisoryAddons(bankId, {
    asOf: asOf ?? null,
    includeInactive,
  });
  const create = useCreateIcaapSupervisoryAddon(bankId);
  const confirm = useConfirmIcaapSupervisoryAddon(bankId);
  const withdraw = useWithdrawIcaapSupervisoryAddon(bankId);

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return <P2Unavailable title="Supervisory add-ons" message={unavailable} />;
  }

  const list = query.data;
  const addons = list?.addons ?? [];

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      {list && (
        <SectionCard
          title="Supervisory capital add-ons"
          subtitle={`Imposed by ${regShort()} and evidenced by its letter.`}
          actions={
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-caption text-slate">
                <input
                  type="checkbox"
                  checked={includeInactive}
                  onChange={(event) => setIncludeInactive(event.target.checked)}
                />
                Include withdrawn and superseded
              </label>
              {canRecord && (
                <SecondaryButton onClick={() => setRecording(true)}>
                  <Plus size={ICON_SM} aria-hidden />
                  Record a letter
                </SecondaryButton>
              )}
            </div>
          }
          noPadding
        >
          {addons.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="No supervisory add-on is recorded"
                description={`If ${regShort()} has required this institution to hold capital above its own assessment, record the letter here so the requirement is carried into the assessment and its evidence is held with it.`}
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-body">
                <thead>
                  <tr className="border-b border-border-light text-caption text-slate">
                    <th scope="col" className="px-4 py-2 text-left">
                      Letter
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Applies to
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Applies from
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      Requirement
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      Amount at the cycle date
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Status
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {addons.map((addon) => (
                    <AddonRow
                      key={addon.addonId}
                      bankId={bankId}
                      addon={addon}
                      canApprove={canApprove}
                      onDecide={(action) => setDeciding({ addon, action })}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="space-y-1 px-4 py-3 text-caption text-slate">
            <p>{ADDON_NEVER_PUBLIC}</p>
            {list.totalAmountAtAsOf !== null && (
              <p>
                Total in force at the cycle date:{" "}
                {fmtAmount(list.totalAmountAtAsOf, NOT_AVAILABLE)}.
              </p>
            )}
          </div>

          {recording && (
            <RecordAddonDialog
              isSaving={create.isPending}
              error={create.error}
              onClose={() => {
                create.reset();
                setRecording(false);
              }}
              onSubmit={(draft) =>
                create.mutate(draft, {
                  onSuccess: () => setRecording(false),
                })
              }
            />
          )}

          {deciding && (
            <DecideDialog
              addon={deciding.addon}
              action={deciding.action}
              isSaving={confirm.isPending || withdraw.isPending}
              onClose={() => setDeciding(null)}
              onSubmit={(reason) => {
                const variables = { addonId: deciding.addon.addonId, reason };
                if (deciding.action === "confirm") {
                  confirm.mutate(variables);
                } else {
                  withdraw.mutate(variables);
                }
                setDeciding(null);
              }}
            />
          )}
        </SectionCard>
      )}
    </QueryBoundary>
  );
}

function AddonRow({
  bankId,
  addon,
  canApprove,
  onDecide,
}: {
  bankId: string;
  addon: IcaapSupervisoryAddon;
  canApprove: boolean;
  onDecide: (action: "confirm" | "withdraw") => void;
}) {
  const status = addonStatusCopy(addon.status);
  const live = addon.status === "draft" || addon.status === "active";
  return (
    <tr className="border-b border-border-light/60 align-top">
      <th scope="row" className="px-4 py-2 text-left font-normal">
        <span className="block text-navy">{addon.letterReference}</span>
        <span className="block text-caption text-slate">
          Dated {fmtDateValue(addon.letterDate, "not stated")}
        </span>
        {addon.description && (
          <span className="mt-1 block text-caption text-slate">
            {addon.description}
          </span>
        )}
      </th>
      <td className="px-4 py-2 text-slate">
        <span className="block">{appliesToBasisLabel(addon.appliesToBasis)}</span>
        {addon.table5Row && (
          <span className="block text-caption">
            Reported under {addon.table5Row}
          </span>
        )}
      </td>
      <td className="px-4 py-2 text-slate">
        <span className="block">
          From {fmtDateValue(addon.effectiveFrom, "not stated")}
        </span>
        <span className="block text-caption">
          {addon.effectiveTo
            ? `until ${fmtDateValue(addon.effectiveTo)}`
            : "with no end date"}
        </span>
      </td>
      <td className="px-4 py-2 text-right">
        <span className="block tnum text-navy">
          {fmtInUnit(
            addon.basisValue,
            addon.basis === "absolute" ? "amount" : "percent",
            NOT_AVAILABLE,
          )}
        </span>
        <span className="block text-caption text-slate">
          {basisLabel(addon.basis)}
        </span>
      </td>
      <td className="px-4 py-2 text-right tnum">
        {fmtAmount(addon.amountAtAsOf, NOT_AVAILABLE)}
      </td>
      <td className="px-4 py-2">
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
        {addon.withdrawalReason && (
          <p className="mt-1 text-caption text-slate">
            {addon.withdrawalReason}
          </p>
        )}
      </td>
      <td className="px-4 py-2 text-right">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <SecondaryButton
            onClick={() => {
              void downloadIcaapSupervisoryAddonLetter(bankId, addon);
            }}
          >
            <Download size={ICON_SM} aria-hidden />
            Letter
          </SecondaryButton>
          {canApprove && addon.status === "draft" && (
            <SecondaryButton onClick={() => onDecide("confirm")}>
              Confirm
            </SecondaryButton>
          )}
          {canApprove && live && (
            <SecondaryButton onClick={() => onDecide("withdraw")}>
              Withdraw
            </SecondaryButton>
          )}
        </div>
      </td>
    </tr>
  );
}

function RecordAddonDialog({
  isSaving,
  error,
  onClose,
  onSubmit,
}: {
  isSaving: boolean;
  error: unknown;
  onClose: () => void;
  onSubmit: (draft: IcaapSupervisoryAddonDraft) => void;
}) {
  const [letterReference, setLetterReference] = useState("");
  const [letterDate, setLetterDate] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [appliesToBasis, setAppliesToBasis] = useState(APPLIES_TO[0]);
  const [basis, setBasis] = useState(BASES[0]);
  // Kept as TEXT, never a number: a supervisory requirement of 1.005% must
  // reach the server as the characters the letter states.
  const [basisValue, setBasisValue] = useState("");
  const [table5Row, setTable5Row] = useState("");
  const [componentKey, setComponentKey] = useState("");
  const [description, setDescription] = useState("");
  const [letter, setLetter] = useState<File | null>(null);

  const complete =
    letterReference.trim() !== "" &&
    letterDate !== "" &&
    effectiveFrom !== "" &&
    basisValue.trim() !== "" &&
    letter !== null;

  return (
    <Dialog
      title="Record a supervisory letter"
      description="The letter is held as the evidence for the requirement. A second person confirms it against the letter before it takes effect."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={isSaving || !complete}
            onClick={() => {
              if (!letter) return;
              onSubmit({
                letterReference: letterReference.trim(),
                letterDate,
                effectiveFrom,
                appliesToBasis,
                basis,
                basisValue: basisValue.trim(),
                letter,
                table5Row: table5Row.trim() || null,
                componentKey: componentKey.trim() || null,
                description: description.trim() || null,
              });
            }}
          >
            {isSaving ? "Recording…" : "Record the letter"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        <FieldLabel
          label="The supervisor's reference for the letter"
          hint="As printed on the letter, so it can be found again."
        >
          <input
            value={letterReference}
            maxLength={REFERENCE_MAX}
            onChange={(event) => setLetterReference(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <div className="grid grid-cols-2 gap-3">
          <FieldLabel label="Date of the letter">
            <input
              type="date"
              value={letterDate}
              onChange={(event) => setLetterDate(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
          <FieldLabel
            label="In force from"
            hint="The date the requirement starts to apply, which may be later than the letter."
          >
            <input
              type="date"
              value={effectiveFrom}
              onChange={(event) => setEffectiveFrom(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
        </div>

        <FieldLabel label="Who it applies to">
          <select
            value={appliesToBasis}
            onChange={(event) => setAppliesToBasis(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          >
            {APPLIES_TO.map((value) => (
              <option key={value} value={value}>
                {appliesToBasisLabel(value)}
              </option>
            ))}
          </select>
        </FieldLabel>

        <div className="grid grid-cols-2 gap-3">
          <FieldLabel label="How the requirement is expressed">
            <select
              value={basis}
              onChange={(event) => setBasis(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            >
              {BASES.map((value) => (
                <option key={value} value={value}>
                  {basisLabel(value)}
                </option>
              ))}
            </select>
          </FieldLabel>
          <FieldLabel
            label="The figure the letter states"
            hint="Typed exactly as the letter states it. It is sent as written and is never rounded here."
          >
            <input
              value={basisValue}
              inputMode="decimal"
              onChange={(event) => setBasisValue(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body tnum"
            />
          </FieldLabel>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <FieldLabel
            label="Table 5 row (optional)"
            hint="Where the requirement is reported in the return, if the letter says."
          >
            <input
              value={table5Row}
              maxLength={ROW_KEY_MAX}
              onChange={(event) => setTable5Row(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
          <FieldLabel
            label="Risk it relates to (optional)"
            hint="Leave blank when the letter imposes the add-on on the institution as a whole."
          >
            <input
              value={componentKey}
              maxLength={COMPONENT_KEY_MAX}
              onChange={(event) => setComponentKey(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
        </div>

        <FieldLabel
          label="What the letter requires, in your own words (optional)"
          hint="A reviewer reads this before opening the letter."
        >
          <textarea
            value={description}
            maxLength={DESCRIPTION_MAX}
            rows={ROWS_MEDIUM}
            onChange={(event) => setDescription(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <FieldLabel
          label="The letter itself"
          hint="The file is checked by its contents, so a document renamed to another extension is refused."
        >
          <input
            type="file"
            onChange={(event) => setLetter(event.target.files?.[0] ?? null)}
            className="mt-1 w-full text-body"
          />
        </FieldLabel>

        {Boolean(error) && (
          <p className="text-body text-critical">
            {error instanceof Error
              ? error.message
              : "The letter could not be recorded."}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function DecideDialog({
  addon,
  action,
  isSaving,
  onClose,
  onSubmit,
}: {
  addon: IcaapSupervisoryAddon;
  action: "confirm" | "withdraw";
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const confirming = action === "confirm";
  return (
    <Dialog
      title={
        confirming
          ? `Confirm ${addon.letterReference}`
          : `Withdraw ${addon.letterReference}`
      }
      description={
        confirming
          ? "Confirming records that you have read the letter and that the figures recorded match it. The requirement takes effect from the date recorded."
          : "Withdrawing records that the requirement no longer applies. The letter and its history are kept."
      }
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={isSaving || reason.trim() === ""}
            onClick={() => onSubmit(reason.trim())}
          >
            {confirming ? "Confirm the add-on" : "Withdraw the add-on"}
          </PrimaryButton>
        </>
      }
    >
      <FieldLabel
        label="Reason"
        hint="Recorded in the audit trail beside your name."
      >
        <input
          value={reason}
          maxLength={REASON_MAX}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
        />
      </FieldLabel>
    </Dialog>
  );
}
