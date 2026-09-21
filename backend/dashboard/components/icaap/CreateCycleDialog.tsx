"use client";

/**
 * Start an ICAAP cycle.
 *
 * What this form deliberately does NOT do: derive the reporting date or the
 * submission deadline. Both come from the framework, resolved server-side
 * (D-024 — no regulatory number, date or interval in dashboard code), and both
 * appear on the cycle once it exists. If the chosen year or kind is outside
 * what the framework covers, the server refuses and its sentence is shown
 * verbatim rather than being second-guessed here.
 *
 * P1 offers Annual and Rehearsal only (DV-007 §5): the material-change and
 * regulator-request kinds arrive with the P3 workflow that gives them meaning.
 */

import { useState } from "react";
import { FlaskConical } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import { regShort } from "@/lib/format";
import { useCreateIcaapCycle, type IcaapCycleKind } from "@/lib/api/icaap";
import type { IcaapFrameworkSummaryRead } from "@/lib/api/icaap";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "./Dialog";
import { REHEARSAL_NOTICE } from "./format";

/** The years a preparer can pick. A convenience list, not a regulatory bound. */
function selectableYears(): number[] {
  const thisYear = new Date().getUTCFullYear();
  const years: number[] = [];
  for (let year = thisYear + 1; year >= thisYear - 4; year -= 1) {
    years.push(year);
  }
  return years;
}

export default function CreateCycleDialog({
  bankId,
  frameworks,
  onClose,
  onCreated,
}: {
  bankId: string;
  frameworks: readonly IcaapFrameworkSummaryRead[];
  onClose: () => void;
  onCreated: (cycleId: string) => void;
}) {
  const years = selectableYears();
  const [fiscalYear, setFiscalYear] = useState<number>(
    years[1] ?? years[0] ?? new Date().getUTCFullYear(),
  );
  const [cycleKind, setCycleKind] = useState<IcaapCycleKind>("rehearsal");
  const [basis, setBasis] = useState<"solo" | "consolidated">("solo");
  const [subsidiaries, setSubsidiaries] = useState(false);
  const [frameworkKey, setFrameworkKey] = useState<string>(
    frameworks[0] ? `${frameworks[0].code}|${frameworks[0].version}` : "",
  );
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const create = useCreateIcaapCycle(bankId);

  const [code, version] = frameworkKey.split("|");
  const framework = frameworks.find(
    (entry) => entry.code === code && entry.version === version,
  );
  const canSubmit =
    Boolean(code && version) && reason.trim().length > 0 && !create.isPending;

  const submit = () => {
    if (!canSubmit) return;
    create.mutate(
      {
        fiscalYear,
        cycleKind,
        basis,
        frameworkCode: code,
        frameworkVersion: version,
        title: title.trim() ? title.trim() : null,
        subsidiariesDeclared: subsidiaries,
        reason: reason.trim(),
      },
      { onSuccess: (cycle) => onCreated(cycle.id) },
    );
  };

  return (
    <Dialog
      title="New ICAAP cycle"
      description="The reporting date and the submission deadline come from the framework."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose} disabled={create.isPending}>
            Cancel
          </SecondaryButton>
          <PrimaryButton onClick={submit} disabled={!canSubmit}>
            {create.isPending ? "Creating…" : "Create cycle"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-4">
        {frameworks.length === 0 && (
          <p className="text-body text-slate">
            No ICAAP framework is published for this institution&apos;s
            jurisdiction and licence class yet.
          </p>
        )}

        <FieldLabel label="Framework">
          <select
            aria-label="Framework"
            className={INPUT_CLASS}
            value={frameworkKey}
            onChange={(event) => setFrameworkKey(event.target.value)}
          >
            {frameworks.map((entry) => (
              <option
                key={`${entry.code}|${entry.version}`}
                value={`${entry.code}|${entry.version}`}
              >
                {`${entry.regulator} · ${entry.shortTitle} · ${entry.version}${
                  entry.status === "exposure_draft" ? " (exposure draft)" : ""
                }`}
              </option>
            ))}
          </select>
        </FieldLabel>
        {/*
          THE REGULATOR NAMED HERE IS THE FRAMEWORK'S OWN, NOT THE TENANT'S.

          A deployment publishes one framework per jurisdiction it serves, and
          more than one can be offered at once. `regShort()` answers with the
          ACTIVE institution's regulator, which is the right answer for the rest
          of the app and the wrong one here: it would attribute a framework to
          a supervisor who did not write it the moment a second jurisdiction is
          published. The summary carries its own `regulator`, so the option
          label and this sentence both use that.
        */}
        {framework && (
          <p className="-mt-2 text-caption text-slate">
            Issued by {framework.regulator}.
          </p>
        )}
        {framework?.status === "exposure_draft" && (
          <p className="-mt-1 text-caption text-warning">
            This version is an exposure draft. {framework.regulator} may change
            the text before it is final.
          </p>
        )}

        <div className="grid grid-cols-2 gap-4">
          <FieldLabel label="Financial year">
            <select
              aria-label="Financial year"
              className={INPUT_CLASS}
              value={fiscalYear}
              onChange={(event) => setFiscalYear(Number(event.target.value))}
            >
              {years.map((year) => (
                <option key={year} value={year}>
                  {year}
                </option>
              ))}
            </select>
          </FieldLabel>
          <FieldLabel label="Basis">
            <select
              aria-label="Basis"
              className={INPUT_CLASS}
              value={basis}
              onChange={(event) =>
                setBasis(event.target.value as "solo" | "consolidated")
              }
            >
              <option value="solo">Solo</option>
              <option value="consolidated">Consolidated</option>
            </select>
          </FieldLabel>
        </div>

        <fieldset>
          <legend className="text-caption font-medium text-navy">Kind</legend>
          <div className="mt-1 space-y-2">
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="icaap-cycle-kind"
                className="mt-1"
                checked={cycleKind === "annual"}
                onChange={() => setCycleKind("annual")}
              />
              <span>
                <span className="text-body text-ink">Annual</span>
                <span className="block text-caption text-slate">
                  The institution&apos;s ICAAP for the financial year, filed
                  with {regShort()}.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="icaap-cycle-kind"
                className="mt-1"
                checked={cycleKind === "rehearsal"}
                onChange={() => setCycleKind("rehearsal")}
              />
              <span>
                <span className="inline-flex items-center gap-1.5 text-body text-ink">
                  <FlaskConical size={13} aria-hidden />
                  Rehearsal
                </span>
                <span className="block text-caption text-slate">
                  {REHEARSAL_NOTICE}
                </span>
              </span>
            </label>
          </div>
        </fieldset>

        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-1"
            checked={subsidiaries}
            onChange={(event) => setSubsidiaries(event.target.checked)}
          />
          <span>
            <span className="text-body text-ink">
              This institution has subsidiaries
            </span>
            <span className="block text-caption text-slate">
              A group files on both bases. Create a second cycle on the other
              basis; the readiness checklist will ask for it.
            </span>
          </span>
        </label>

        <FieldLabel
          label="Title (optional)"
          hint="Leave blank to use the framework's own wording."
        >
          <input
            className={INPUT_CLASS}
            value={title}
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
          />
        </FieldLabel>

        <FieldLabel
          label="Reason"
          hint="Recorded in the audit trail with your name."
        >
          <input
            className={INPUT_CLASS}
            value={reason}
            maxLength={2000}
            onChange={(event) => setReason(event.target.value)}
          />
        </FieldLabel>

        {create.error != null && (
          <p className="text-body text-critical">
            {isApiError(create.error)
              ? create.error.message
              : "Could not create the cycle."}
          </p>
        )}
      </div>
    </Dialog>
  );
}
