"use client";

/**
 * Who the platform will mail, named by address.
 *
 * WHY ADDRESSES AND NOT A PICKER. Reading this organization's user directory
 * (`GET /organization/users`) needs its own Account authority, and the platform
 * decided deliberately that administering an account does not imply reading its
 * directory — so an analyst who may build a report does not hold it. A picker
 * would therefore be empty for most of the people this form is for, and building
 * one anyway would mean disclosing the tenant's staff list to anyone who can open
 * Explore. Typing an address discloses nothing: you have to know it already.
 *
 * The server resolves every address to an active identity of this organization
 * and refuses the WHOLE request if one does not resolve, naming the ones that
 * did not. Those are marked here rather than silently dropped, because a report
 * that quietly reaches fewer people than its author believes is the failure this
 * form exists to prevent.
 */

import { AlertCircle, Users } from "lucide-react";
import {
  malformedRecipients,
  parseRecipients,
} from "@/components/bi/notifications";

export default function RecipientField({
  value,
  onChange,
  max,
  unknown,
  label,
  help,
}: {
  value: string;
  onChange: (value: string) => void;
  max: number;
  /** Addresses the server said it could not resolve, from the last attempt. */
  unknown: readonly string[];
  label: string;
  help: string;
}) {
  const parsed = parseRecipients(value);
  const malformed = malformedRecipients(value);
  const overCap = parsed.length > max;

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor="bi-recipients"
        className="text-caption font-medium text-navy"
      >
        {label}
      </label>
      <p className="text-caption text-slate leading-relaxed">{help}</p>
      <textarea
        id="bi-recipients"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={3}
        spellCheck={false}
        className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
        placeholder="name@yourbank.com"
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-slate">
        <span className="inline-flex items-center gap-1">
          <Users size={12} aria-hidden />
          {parsed.length === 1 ? "1 person" : `${parsed.length} people`} named
        </span>
        <span>At most {max} for one report.</span>
      </div>
      {overCap && (
        <p className="inline-flex items-start gap-1.5 text-caption text-critical">
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          Remove {parsed.length - max} so that no more than {max} people are
          named. One send goes to everyone on the list, so the list is capped.
        </p>
      )}
      {malformed.length > 0 && (
        <p className="inline-flex items-start gap-1.5 text-caption text-warning">
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          {malformed.join(", ")} {malformed.length === 1 ? "is" : "are"} not an
          email address.
        </p>
      )}
      {unknown.length > 0 && (
        <p className="inline-flex items-start gap-1.5 text-caption text-critical">
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          {unknown.join(", ")} {unknown.length === 1 ? "is" : "are"} not an
          active user of this organization, so nothing was saved. Everyone on
          the list has to be able to sign in, because each copy is prepared
          under that person&apos;s own access.
        </p>
      )}
    </div>
  );
}
